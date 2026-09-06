"""Headless end-to-end test for SmartSehat — Day 2 stub mode (offline).

Covers the Day-2 node sequence:
  STT_RESULT -> VOICE_TRANSCRIPT_CONFIRMED -> STRUCTURE_EXTRACTED ->
  TRIAGE_DECISION + VISION_FINDING (parallel) ->
  REASONING_RESULT -> MEDICATION_REVIEW -> HITL_DECISION -> RX_FINALIZED
and preserves every Day-1 safety invariant (Bug #1: gate-2 modify
re-enters the pharmacy loop; Bug #3: HITL audit records true
original-vs-final; RED path skips reasoning/pharm).

Scenario 5: the deterministic triage floor — Urdu-script AND Devanagari
(Qwen ASR output script) chest-pain transcripts must trigger RED even
with NO structured symptoms. Safety law: triage must not depend on LLM
extraction, and must work for BOTH STT engines in the chain.

All scenarios force stub mode, prohibit model/network calls, and use temporary
databases. These are software workflow checks using synthetic medicines and
patient context; their success is not evidence of clinical validation.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from unittest.mock import patch

from smartsehat import agents, audit              # noqa: E402
from smartsehat.auth import DemoAuthenticationProvider  # noqa: E402
from smartsehat.rules_triage import evaluate      # noqa: E402
from smartsehat.graph import resume_case, run_case  # noqa: E402
from smartsehat.medication_review import NO_MEDICINES, format_medicines, normalize_medicines  # noqa: E402
import seed                                       # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond, detail))
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}{(' — ' + detail) if detail else ''}")


def _event_types(case_id: str) -> list[str]:
    return [e["event_type"] for e in audit.get_case_timeline(case_id)]


def _order_ok(types: list[str], *seq: str) -> bool:
    idxs = []
    for s in seq:
        if s not in types:
            return False
        idxs.append(types.index(s))
    return all(idxs[i] < idxs[i + 1] for i in range(len(idxs) - 1))


CANNED_URDU = "مجھے پچھلے دو ہفتوں سے کھانسی ہے، رات کو پسینہ آتا ہے"
STUB_CONFIG = {"agents": "stub", "allowlist_medicines": ["paracetamol", "amoxicillin"]}
SYNTHETIC_CONTEXT = {
    "age": 42, "sex": "M", "weight_kg": 70, "allergies": [],
    "current_meds": [], "pregnant": False, "renal_impairment": False,
    "liver_disease": False, "context_confirmed": True,
}
REVIEW_NOTE = (
    "Synthetic test: reviewed listed evidence, indication, missing coverage and "
    "escalation needs. This recorded decision is not clinical validation."
)


def _medicine(name: str, dose_mg: float, frequency: float = 3) -> list[dict]:
    return [{"name": name, "strength": "500 mg", "dose_mg": dose_mg,
             "frequency_per_day": frequency, "duration_days": 2, "route": "oral",
             "indication": "Synthetic workflow test; requires clinician assessment"}]


def _expected_draft(medicines: list[dict]) -> str:
    normalized, errors = normalize_medicines(medicines)
    assert not errors, errors
    return format_medicines(normalized)


def _actor():
    return DemoAuthenticationProvider().authenticate("PM-2213")


def _approve(thread_id: str, ckpt: str):
    return resume_case(thread_id, _actor(), action="approve", checkpoint_db=ckpt,
                       medication_review_acknowledged=True, reason=REVIEW_NOTE)


def _no_network(*args, **kwargs):
    raise AssertionError("Workflow tests must not call models or the network.")


def _confirm_transcript(thread_id: str, ckpt: str, text: str = CANNED_URDU):
    """Advance through the transcript gate — must happen before the HITL gate."""
    return resume_case(thread_id, _actor(), action="confirm", transcript=text, checkpoint_db=ckpt)


# ----------------------------------------------------------------------
# Scenario 1 — YELLOW: modify then approve
# ----------------------------------------------------------------------

def scenario_yellow_modify_then_approve() -> None:
    ckpt = os.path.join(os.path.dirname(os.environ["SMARTSEHAT_DB"]), "yellow_ckpt.db")
    case_id, thread_id, first = run_case("PT-0001", _actor(), checkpoint_db=ckpt,
                                        image_path=os.path.join(os.path.dirname(__file__), "assets", "xray", "tb.jpg"),
                                        medication_context=SYNTHETIC_CONTEXT)
    check("yellow: first invoke reached TRANSCRIPT gate",
          "__interrupt__" in first,
          f"result keys: {list(first.keys())}")

    r_after_confirm = _confirm_transcript(thread_id, ckpt)
    check("yellow: transcript confirm advances to HITL gate",
          "__interrupt__" in r_after_confirm,
          f"result keys: {list(r_after_confirm.keys())}")

    edited_medicines = _medicine("amoxicillin", 500)
    r_mod = resume_case(thread_id, _actor(), action="modify",
                        proposed_medicines=edited_medicines,
                        checkpoint_db=ckpt)
    check("yellow: modify triggers second HITL interrupt",
          "__interrupt__" in r_mod,
          f"result keys: {list(r_mod.keys())}")

    r_final = _approve(thread_id, ckpt)
    check("yellow: approve completes graph",
          "__interrupt__" not in r_final,
          f"result keys: {list(r_final.keys())}")

    case_row = audit.get_case(case_id)
    check("yellow: case row completed_at set",
          bool(case_row and case_row.get("completed_at")),
          str(case_row.get("completed_at") if case_row else None))
    check("yellow: hitl_outcome is 'modified' (approved with edits)",
          bool(case_row) and case_row.get("hitl_outcome") == "modified",
          str(case_row.get("hitl_outcome") if case_row else None))

    types = _event_types(case_id)
    required = {"STT_RESULT", "VOICE_TRANSCRIPT_CONFIRMED", "STRUCTURE_EXTRACTED",
                "TRIAGE_DECISION", "VISION_FINDING", "REASONING_RESULT",
                "MEDICATION_REVIEW", "HITL_DECISION", "RX_FINALIZED"}
    check("yellow: all required events present",
          required.issubset(set(types)),
          f"missing: {required - set(types)}")
    check("yellow: medication review re-ran after modify (2x MEDICATION_REVIEW)",
          types.count("MEDICATION_REVIEW") == 2,
          f"count={types.count('MEDICATION_REVIEW')}")
    check("yellow: critical order stt→confirmed→structure→reasoning→hitl→final",
          _order_ok(types, "STT_RESULT", "VOICE_TRANSCRIPT_CONFIRMED",
                    "STRUCTURE_EXTRACTED", "REASONING_RESULT", "MEDICATION_REVIEW",
                    "HITL_DECISION", "RX_FINALIZED"))

    decs = audit.get_hitl_decisions(case_id)
    check("yellow: exactly one HITL decision recorded", len(decs) == 1,
          f"count={len(decs)}")
    if decs:
        orig = json.loads(decs[0]["original_json"])
        fin = json.loads(decs[0]["final_json"])
        check("audit: original != final after modification", orig != fin,
              f"orig={orig.get('prescription_draft')} | final={fin.get('prescription_draft')}")
        check("audit: final contains the paramedic's edit",
              fin.get("prescription_draft") == _expected_draft(edited_medicines))
        check("audit: original preserves the PRE-edit draft",
              orig.get("prescription_draft") == NO_MEDICINES)
        review = fin.get("medication_review") or {}
        check("audit: final records source version, fingerprint and acknowledgement",
              bool(review.get("ruleset_version")) and bool(review.get("input_hash"))
              and fin.get("review_acknowledged") is True)


# ----------------------------------------------------------------------
# Scenario 2 — Bug #1: modify at the SECOND gate must re-run pharm
# ----------------------------------------------------------------------

def scenario_modify_at_second_gate() -> None:
    ckpt = os.path.join(os.path.dirname(os.environ["SMARTSEHAT_DB"]), "gate2_ckpt.db")
    case_id, thread_id, _first = run_case("PT-0001", _actor(), checkpoint_db=ckpt,
                                         medication_context=SYNTHETIC_CONTEXT)
    _confirm_transcript(thread_id, ckpt)

    first_edit = _medicine("paracetamol", 500, 2)
    latest_edit = _medicine("paracetamol", 1000, 3)
    resume_case(thread_id, _actor(), action="modify", proposed_medicines=first_edit, checkpoint_db=ckpt)
    r2 = resume_case(thread_id, _actor(), action="modify", proposed_medicines=latest_edit, checkpoint_db=ckpt)
    check("gate2: second-gate modify re-enters HITL (pharm re-ran)",
          "__interrupt__" in r2,
          f"result keys: {list(r2.keys())}")

    r3 = _approve(thread_id, ckpt)
    check("gate2: final approve completes graph",
          "__interrupt__" not in r3)

    types = _event_types(case_id)
    check("gate2: pharm ran THREE times (no edit ever dropped)",
          types.count("MEDICATION_REVIEW") == 3,
          f"count={types.count('MEDICATION_REVIEW')}")

    decs = audit.get_hitl_decisions(case_id)
    if decs:
        fin = json.loads(decs[0]["final_json"])
        check("gate2: final draft is the LATEST structured dose edit",
              fin.get("prescription_draft") == _expected_draft(latest_edit),
              f"final={fin.get('prescription_draft')}")
    case_row = audit.get_case(case_id)
    check("gate2: hitl_outcome is 'modified'",
          bool(case_row) and case_row.get("hitl_outcome") == "modified")


# ----------------------------------------------------------------------
# Scenario 3 — RED path skips reasoning/pharm
# ----------------------------------------------------------------------

def scenario_red_path() -> None:
    ckpt = os.path.join(os.path.dirname(os.environ["SMARTSEHAT_DB"]), "red_ckpt.db")
    case_id, thread_id, first = run_case("PT-0001", _actor(),
                                         force_triage="RED", checkpoint_db=ckpt)
    check("red: first invoke reached TRANSCRIPT gate",
          "__interrupt__" in first)

    r_after_confirm = _confirm_transcript(thread_id, ckpt)
    check("red: transcript confirm advances to HITL gate",
          "__interrupt__" in r_after_confirm)

    r_final = resume_case(thread_id, _actor(), action="approve", checkpoint_db=ckpt)
    check("red: approve completes graph",
          "__interrupt__" not in r_final)

    types = _event_types(case_id)
    check("red: emergency_node executed",
          "EMERGENCY_PROTOCOL" in types, str(types))
    check("red: reasoning_node did NOT run",
          "REASONING_RESULT" not in types, str(types))
    check("red: pharm_node did NOT run",
          "MEDICATION_REVIEW" not in types, str(types))
    check("red: referral issued at finalize",
          "REFERRAL_ISSUED" in types, str(types))
    case_row = audit.get_case(case_id)
    check("red: outcome is emergency_referral",
          bool(case_row) and case_row.get("outcome") == "emergency_referral")


# ----------------------------------------------------------------------
# Scenario 4 — Transcript gate captures paramedic corrections
# ----------------------------------------------------------------------

def scenario_transcript_correction() -> None:
    ckpt = os.path.join(os.path.dirname(os.environ["SMARTSEHAT_DB"]), "tc_ckpt.db")
    case_id, thread_id, _first = run_case("PT-0001", _actor(), checkpoint_db=ckpt)
    edited = "مجھے پچھلے تین ہفتوں سے کھانسی ہے"  # paramedic edited: 3 weeks
    _confirm_transcript(thread_id, ckpt, text=edited)
    resume_case(thread_id, _actor(), action="approve", checkpoint_db=ckpt)

    events = audit.get_case_timeline(case_id)
    confirms = [e for e in events if e["event_type"] == "VOICE_TRANSCRIPT_CONFIRMED"]
    check("transcript: exactly one confirmation event", len(confirms) == 1)
    if confirms:
        payload = json.loads(confirms[0]["payload_json"])
        check("transcript: confirmed text is the paramedic's edited version",
              payload.get("transcript") == edited,
              f"got={payload.get('transcript')}")
        check("transcript: paramedic_corrected flag is True",
              payload.get("paramedic_corrected") is True,
              f"got={payload.get('paramedic_corrected')}")


# ----------------------------------------------------------------------
# Scenario 5 — Triage floor: Urdu + Devanagari (both STT engines)
# ----------------------------------------------------------------------

def scenario_triage_floor() -> None:
    # --- Urdu script (Gemini STT output) ---
    red_state = {
        "symptoms": [],
        "transcript_confirmed": "مجھے آدھے گھنٹے سے سینے میں بہت درد ہے اور پسینہ آ رہا ہے",
    }
    r = evaluate(red_state)
    check("urdu-triage: chest pain transcript (no symptoms) → RED",
          r["level"] == "RED",
          f"level={r['level']} rules={r['triggered_rules']}")

    red_state_2 = {
        "symptoms": [],
        "transcript": "مجھے سینے میں درد ہے، بازو میں جا رہا ہے",
    }
    r2 = evaluate(red_state_2)
    check("urdu-triage: raw transcript only (unconfirmed) still → RED",
          r2["level"] == "RED",
          f"level={r2['level']}")

    yellow_state = {
        "symptoms": [],
        "transcript_confirmed": "مجھے دو ہفتوں سے کھانسی ہے اور رات کو پسینہ آتا ہے، وزن کم ہو رہا ہے",
    }
    ry = evaluate(yellow_state)
    check("urdu-triage: TB-symptom transcript (no symptoms) → YELLOW",
          ry["level"] == "YELLOW",
          f"level={ry['level']} rules={ry['triggered_rules']}")

    # --- Devanagari (Qwen ASR output — bake-off caveat #4) ---
    red_deva = {
        "symptoms": [],
        "transcript_confirmed": "मुझे आधे घंटे से सीने में बहुत दर्द है और पसीना आ रहा है",
    }
    rd = evaluate(red_deva)
    check("deva-triage: Devanagari chest-pain transcript → RED",
          rd["level"] == "RED",
          f"level={rd['level']} rules={rd['triggered_rules']}")

    green_state = {"symptoms": ["mild_headache"], "transcript_confirmed": "سر میں ہلکا درد ہے"}
    rg = evaluate(green_state)
    check("triage: mild complaint → GREEN",
          rg["level"] == "GREEN",
          f"level={rg['level']}")


def main() -> int:
    RESULTS.clear()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="smartsehat_flow_") as directory:
        database = os.path.join(directory, "smartsehat.db")
        with (patch.object(audit, "DB_PATH", database),
              patch.dict(os.environ, {"SMARTSEHAT_DB": database,
                                      "SMARTSEHAT_MEDIA_ROOT": os.path.join(directory, "private-media"),
                                      "SMARTSEHAT_AUTH_MODE": "demo"}),
              patch.object(agents, "_config", return_value=STUB_CONFIG),
              patch.object(agents, "_router", side_effect=_no_network),
              patch("socket.socket.connect", side_effect=_no_network),
              patch("socket.create_connection", side_effect=_no_network)):
            seed.seed()
            print("Synthetic workflow tests: stub mode forced; network disabled; temporary databases.")
            scenario_yellow_modify_then_approve()
            scenario_modify_at_second_gate()
            scenario_red_path()
            scenario_transcript_correction()
            scenario_triage_floor()
    fails = [n for n, ok, _ in RESULTS if not ok]
    print("\n===== SUMMARY =====")
    print(f"Total: {len(RESULTS)}  Passed: {len(RESULTS) - len(fails)}  Failed: {len(fails)}")
    if fails:
        for n in fails:
            print(f"  FAILED: {n}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
