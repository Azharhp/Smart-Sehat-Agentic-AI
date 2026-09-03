"""Headless end-to-end test for SmartSehat — Day 2 stub mode (offline).

Covers the Day-2 node sequence:
  STT_RESULT -> VOICE_TRANSCRIPT_CONFIRMED -> STRUCTURE_EXTRACTED ->
  TRIAGE_DECISION + VISION_FINDING (parallel) ->
  REASONING_RESULT -> PHARM_CHECK_PASSED -> HITL_DECISION -> RX_FINALIZED
and preserves every Day-1 safety invariant (Bug #1: gate-2 modify
re-enters the pharmacy loop; Bug #3: HITL audit records true
original-vs-final; RED path skips reasoning/pharm).

Scenario 5: the deterministic triage floor — Urdu-script AND Devanagari
(Qwen ASR output script) chest-pain transcripts must trigger RED even
with NO structured symptoms. Safety law: triage must not depend on LLM
extraction, and must work for BOTH STT engines in the chain.

All scenarios run OFFLINE in stub mode (config.yaml: agents: stub).
"""
from __future__ import annotations
import json
import os
import sys
import tempfile

os.environ["SMARTSEHAT_DB"] = os.path.join(tempfile.mkdtemp(prefix="smartsehat_"), "smartsehat.db")

from smartsehat import audit                      # noqa: E402
from smartsehat.rules_triage import evaluate      # noqa: E402
from smartsehat.graph import resume_case, run_case  # noqa: E402
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


def _confirm_transcript(thread_id: str, ckpt: str, text: str = CANNED_URDU):
    """Advance through the transcript gate — must happen before the HITL gate."""
    return resume_case(thread_id, action="confirm", transcript=text, checkpoint_db=ckpt)


# ----------------------------------------------------------------------
# Scenario 1 — YELLOW: modify then approve
# ----------------------------------------------------------------------

def scenario_yellow_modify_then_approve() -> None:
    ckpt = os.path.join(os.path.dirname(os.environ["SMARTSEHAT_DB"]), "yellow_ckpt.db")
    case_id, thread_id, first = run_case("PT-0001", "PM-2213", checkpoint_db=ckpt)
    check("yellow: first invoke reached TRANSCRIPT gate",
          "__interrupt__" in first,
          f"result keys: {list(first.keys())}")

    r_after_confirm = _confirm_transcript(thread_id, ckpt)
    check("yellow: transcript confirm advances to HITL gate",
          "__interrupt__" in r_after_confirm,
          f"result keys: {list(r_after_confirm.keys())}")

    r_mod = resume_case(thread_id, action="modify",
                        modified_content="amoxicillin 500mg TDS 7 days",
                        checkpoint_db=ckpt)
    check("yellow: modify triggers second HITL interrupt",
          "__interrupt__" in r_mod,
          f"result keys: {list(r_mod.keys())}")

    r_final = resume_case(thread_id, action="approve", checkpoint_db=ckpt)
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
                "PHARM_CHECK_PASSED", "HITL_DECISION", "RX_FINALIZED"}
    check("yellow: all required events present",
          required.issubset(set(types)),
          f"missing: {required - set(types)}")
    check("yellow: pharm re-ran after modify (2x PHARM_CHECK_PASSED)",
          types.count("PHARM_CHECK_PASSED") == 2,
          f"count={types.count('PHARM_CHECK_PASSED')}")
    check("yellow: critical order stt→confirmed→structure→reasoning→hitl→final",
          _order_ok(types, "STT_RESULT", "VOICE_TRANSCRIPT_CONFIRMED",
                    "STRUCTURE_EXTRACTED", "REASONING_RESULT",
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
              "amoxicillin 500mg TDS 7 days" in fin.get("prescription_draft", ""))
        check("audit: original preserves the PRE-edit draft",
              "amoxicillin" not in (orig.get("prescription_draft") or ""))


# ----------------------------------------------------------------------
# Scenario 2 — Bug #1: modify at the SECOND gate must re-run pharm
# ----------------------------------------------------------------------

def scenario_modify_at_second_gate() -> None:
    ckpt = os.path.join(os.path.dirname(os.environ["SMARTSEHAT_DB"]), "gate2_ckpt.db")
    case_id, thread_id, _first = run_case("PT-0001", "PM-2213", checkpoint_db=ckpt)
    _confirm_transcript(thread_id, ckpt)

    resume_case(thread_id, action="modify", modified_content="EDIT-ONE", checkpoint_db=ckpt)
    r2 = resume_case(thread_id, action="modify", modified_content="EDIT-TWO", checkpoint_db=ckpt)
    check("gate2: second-gate modify re-enters HITL (pharm re-ran)",
          "__interrupt__" in r2,
          f"result keys: {list(r2.keys())}")

    r3 = resume_case(thread_id, action="approve", checkpoint_db=ckpt)
    check("gate2: final approve completes graph",
          "__interrupt__" not in r3)

    types = _event_types(case_id)
    check("gate2: pharm ran THREE times (no edit ever dropped)",
          types.count("PHARM_CHECK_PASSED") == 3,
          f"count={types.count('PHARM_CHECK_PASSED')}")

    decs = audit.get_hitl_decisions(case_id)
    if decs:
        fin = json.loads(decs[0]["final_json"])
        check("gate2: final draft is the LATEST edit (EDIT-TWO)",
              "EDIT-TWO" in fin.get("prescription_draft", ""),
              f"final={fin.get('prescription_draft')}")
    case_row = audit.get_case(case_id)
    check("gate2: hitl_outcome is 'modified'",
          bool(case_row) and case_row.get("hitl_outcome") == "modified")


# ----------------------------------------------------------------------
# Scenario 3 — RED path skips reasoning/pharm
# ----------------------------------------------------------------------

def scenario_red_path() -> None:
    ckpt = os.path.join(os.path.dirname(os.environ["SMARTSEHAT_DB"]), "red_ckpt.db")
    case_id, thread_id, first = run_case("PT-0001", "PM-2213",
                                         force_triage="RED", checkpoint_db=ckpt)
    check("red: first invoke reached TRANSCRIPT gate",
          "__interrupt__" in first)

    r_after_confirm = _confirm_transcript(thread_id, ckpt)
    check("red: transcript confirm advances to HITL gate",
          "__interrupt__" in r_after_confirm)

    r_final = resume_case(thread_id, action="approve", checkpoint_db=ckpt)
    check("red: approve completes graph",
          "__interrupt__" not in r_final)

    types = _event_types(case_id)
    check("red: emergency_node executed",
          "EMERGENCY_PROTOCOL" in types, str(types))
    check("red: reasoning_node did NOT run",
          "REASONING_RESULT" not in types, str(types))
    check("red: pharm_node did NOT run",
          "PHARM_CHECK_PASSED" not in types, str(types))
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
    case_id, thread_id, _first = run_case("PT-0001", "PM-2213", checkpoint_db=ckpt)
    edited = "مجھے پچھلے تین ہفتوں سے کھانسی ہے"  # paramedic edited: 3 weeks
    _confirm_transcript(thread_id, ckpt, text=edited)
    resume_case(thread_id, action="approve", checkpoint_db=ckpt)

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
    seed.seed()
    print(f"DB at: {os.environ['SMARTSEHAT_DB']}")
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