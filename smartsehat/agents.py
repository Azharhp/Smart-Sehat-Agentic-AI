"""SmartSehat agents — Day 2 (patched).

Patches in this version:
- PATCH 1 (stt_node): in real mode with NO audio, the node no longer
  falls through to the canned stub transcript (which would inject fake
  TB symptoms into a live consultation). Instead it logs STT_SKIPPED and
  the paramedic types the transcript at the confirmation gate.
- PATCH 4 (stub reasoning): stub-mode citations now point at REAL corpus
  chunk ids, so rehearsals/fallback demos show citations that resolve.

Split from Day-1: voice_intake -> {stt_node, transcript_gate, structure_node}.
Every agent honours config's `agents:` switch: `stub` keeps the Day-1
canned behaviour (offline, deterministic) so test_flow stays green; `real`
routes through the ModelRouter and logs the provider used.

Safety laws still in force:
- The router raises on total failure; agents catch that and enter an explicit
  DEGRADED mode instead of dressing an error up as a clinical answer.
- Triage is DETERMINISTIC RULES in real mode (rules_triage.evaluate), never
  an LLM decision.
"""
from __future__ import annotations
import json
import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from langgraph.types import interrupt

from . import audit, corpus
from .rules_triage import evaluate as triage_evaluate
from .state import PatientState
from .medication_review import (
    NO_MEDICINES, draft_to_medicines, format_medicines,
    normalize_medicines, review_medicines,
)
from .emergency import build_emergency_protocol

# ----------------------------------------------------------------------
# Stub/real mode helpers
# ----------------------------------------------------------------------

_STUB_TRANSCRIPT = "مجھے پچھلے دو ہفتوں سے کھانسی ہے، رات کو پسینہ آتا ہے"


@lru_cache(maxsize=1)
def _router():
    from .router import ModelRouter
    return ModelRouter()


@lru_cache(maxsize=1)
def _config() -> dict:
    from .router import load_config
    return load_config()


def _mode() -> str:
    return (_config().get("agents") or "stub").lower()


def _extract_json(text: str) -> dict | None:
    """Tolerant JSON extraction — find the outermost {...} block."""
    if not text:
        return None
    text = text.strip()
    # try direct parse first
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else None
    except Exception:
        return None

# ----------------------------------------------------------------------
# Voice: STT
# ----------------------------------------------------------------------

def stt_node(state: PatientState) -> dict[str, Any]:
    # PATCH 1: real mode must never fall through to the canned stub
    # transcript — that would inject fake TB symptoms into a live case.
    if _mode() == "real":
        if not state.get("audio_path"):
            # No audio in real mode: the paramedic types the transcript at
            # the confirmation gate. NEVER inject canned symptoms.
            audit.log_event(state["case_id"], "STT_SKIPPED",
                            {"reason": "no_audio_paramedic_will_type"}, provider="none")
            return {"transcript": None}
        try:
            text, spec = _router().route_transcribe("stt", state["audio_path"])
            audit.log_event(state["case_id"], "STT_RESULT",
                            {"transcript": text}, provider=spec, confidence=None)
            return {"transcript": text, "provider_used": {"stt": spec}}
        except Exception as e:
            audit.log_event(state["case_id"], "STT_FAILED",
                            {"error": str(e)}, provider="none")
            return {"transcript": None, "degraded": "stt_unavailable"}
    # Stub mode only — canned transcript, no API call.
    audit.log_event(state["case_id"], "STT_RESULT",
                    {"transcript": _STUB_TRANSCRIPT}, provider="stub", confidence=1.0)
    return {"transcript": _STUB_TRANSCRIPT}


# ----------------------------------------------------------------------
# Voice: transcript confirmation gate (HITL)
# ----------------------------------------------------------------------

def transcript_gate(state: PatientState) -> dict[str, Any]:
    original = state.get("transcript") or ""
    resume = interrupt({
        "gate": "transcript",
        "case_id": state["case_id"],
        "transcript": original,
    })
    resume_payload = resume if isinstance(resume, dict) else {}
    confirmed = resume_payload.get("transcript") or original
    decision_actor_id = resume_payload.get("_actor_id") or state.get("paramedic_id")
    corrected = bool(original) and (confirmed != original)
    audit.log_event(state["case_id"], "VOICE_TRANSCRIPT_CONFIRMED",
                    {"transcript": confirmed, "paramedic_corrected": corrected,
                     "actor_id": decision_actor_id},
                    provider="paramedic", confidence=1.0)
    return {"transcript_confirmed": confirmed, "paramedic_corrected": corrected,
            "decision_actor_id": decision_actor_id}


# ----------------------------------------------------------------------
# Voice: structure the confirmed transcript into a clinical JSON
# ----------------------------------------------------------------------

_STRUCTURE_SYSTEM = (
    "You extract a structured clinical intake from an Urdu patient statement. "
    "Return STRICT JSON with keys: chief_complaint (str), duration_days (int), "
    "symptoms (list[str] English snake_case), missing_fields (list[str]), "
    "followup_question_urdu (str). No prose. No markdown fences."
)

_STUB_STRUCTURE = {
    "chief_complaint": "chronic_cough",
    "duration_days": 14,
    "symptoms": ["night_sweats", "evening_fever", "weight_loss", "streak_hemoptysis"],
    "missing_fields": [],
    "followup_question_urdu": "کیا آپ کو بلغم میں خون آ رہا ہے؟",
}


def structure_node(state: PatientState) -> dict[str, Any]:
    if _mode() != "real":
        payload = dict(_STUB_STRUCTURE)
        audit.log_event(state["case_id"], "STRUCTURE_EXTRACTED", payload, provider="stub")
        return {
            "symptoms": payload["symptoms"],
            "chief_complaint": payload["chief_complaint"],
            "duration_days": payload["duration_days"],
            "missing_fields": payload["missing_fields"],
            "followup_question_urdu": payload["followup_question_urdu"],
        }

    text_in = state.get("transcript_confirmed") or state.get("transcript") or ""
    if not text_in:
        audit.log_event(state["case_id"], "STRUCTURE_EXTRACTED",
                        {"parse_error": True, "reason": "no_transcript"}, provider="none")
        return {"symptoms": [], "parse_error": True}

    messages = [
        {"role": "system", "content": _STRUCTURE_SYSTEM},
        {"role": "user", "content": text_in},
    ]
    parsed: dict | None = None
    provider = "none"
    for _ in range(2):  # one retry
        try:
            raw, provider = _router().route_generate("reasoning", messages, temperature=0.1)
            parsed = _extract_json(raw)
            if parsed:
                break
        except Exception as e:
            audit.log_event(state["case_id"], "STRUCTURE_ERROR",
                            {"error": str(e)}, provider=provider)
            provider = "none"
            parsed = None
    if not parsed:
        audit.log_event(state["case_id"], "STRUCTURE_EXTRACTED",
                        {"parse_error": True}, provider=provider)
        return {"symptoms": [], "parse_error": True}
    audit.log_event(state["case_id"], "STRUCTURE_EXTRACTED", parsed, provider=provider)
    return {
        "symptoms": parsed.get("symptoms") or [],
        "chief_complaint": parsed.get("chief_complaint"),
        "duration_days": parsed.get("duration_days"),
        "missing_fields": parsed.get("missing_fields") or [],
        "followup_question_urdu": parsed.get("followup_question_urdu"),
    }


# ----------------------------------------------------------------------
# Triage — deterministic rules in real mode; canned stub in stub mode
# ----------------------------------------------------------------------

def triage_node(state: PatientState) -> dict[str, Any]:
    # The same deterministic rules run in demo and real modes.
    r = triage_evaluate(state)
    payload = {"triage_level": r["level"], "rules": r["triggered_rules"],
               "instruction": r["instruction"], "engine": "local-rules"}
    audit.log_event(state["case_id"], "TRIAGE_DECISION", payload,
                    provider="local-rules", confidence=None)
    return {"triage_level": r["level"], "triage_rules": r["triggered_rules"]}


# ----------------------------------------------------------------------
# Vision — real X-ray pattern read via router.route_vision
# ----------------------------------------------------------------------

VISION_XRAY_PROMPT = (
    "You are a radiology pattern-reading assistant at a rural health unit. "
    "Look at this chest X-ray. Describe only PATTERNS you see (opacities, "
    "cavitation, effusion). Do NOT state a disease as certain. ALWAYS "
    "recommend a confirmatory test. Output ONLY JSON: "
    "{\"findings\":[{\"pattern\":..,\"location\":..,\"score\":0.0}],"
    "\"interpretation\":..,\"recommend\":[..]}"
)


def vision_node(state: PatientState) -> dict[str, Any]:
    if _mode() != "real":
        if not state.get("has_image", True):
            return {}
        payload = {"findings": [{"pattern": "cavitation", "location": "right_upper_lobe",
                                 "score": 0.87}],
                   "interpretation": "TB-consistent pattern, confirmation required"}
        audit.log_event(state["case_id"], "VISION_FINDING", payload,
                        provider="stub", confidence=0.87)
        return {"imaging_findings": payload}
    image_path = state.get("image_path")
    if not image_path:
        return {}
    parsed: dict | None = None
    provider = "none"
    for _ in range(2):
        try:
            raw, provider = _router().route_vision("vision_xray", image_path, VISION_XRAY_PROMPT)
            parsed = _extract_json(raw)
            if parsed:
                break
        except Exception as e:
            audit.log_event(state["case_id"], "VISION_ERROR",
                            {"error": str(e)}, provider=provider)
            provider = "none"
            parsed = None
    if not parsed:
        audit.log_event(state["case_id"], "VISION_FAILED",
                        {"reason": "no_parseable_json"}, provider=provider)
        return {"imaging_findings": None}
    audit.log_event(state["case_id"], "VISION_FINDING", parsed, provider=provider)
    return {"imaging_findings": parsed}


# ----------------------------------------------------------------------
# Reasoning — RAG over corpus chunks + citation gate + degraded mode
# ----------------------------------------------------------------------

_REASONING_SYSTEM_TMPL = (
    "You are a clinical reasoning assistant at a rural BHU in Pakistan. "
    "Be conservative and state uncertainty. "
    "You may cite ONLY the provided chunk IDs (do not invent). "
    "You may mention ONLY medicines from this allowlist: {allow}. "
    "Return STRICT JSON with keys: differential_diagnosis "
    "(list of {diagnosis, probability, evidence:[], citations:[chunk_ids]}), "
    "recommended_workup (list[str]), initial_management (str), "
    "proposed_medicines (list of {name:str, strength:str|null, dose_mg:number|null, "
    "frequency_per_day:number|null, duration_days:number|null, route:str|null, indication:str|null}). "
    "dose_mg is administered milligrams per dose, NOT the package strength. "
    "Do not invent missing dose, frequency, duration or patient details. Use null. "
    "Return an empty proposed_medicines list when no medicine is proposed. "
    "All medicine proposals mentioned in management MUST also appear in proposed_medicines. "
    "These are proposals for human review, never approved prescriptions. "
    "confidence (float 0..1), uncertainty_note (str). No prose. No markdown fences."
)


def _mentions_off_allowlist(text: str, allowlist: list[str]) -> list[str]:
    """Very simple check — flag common drug-name tokens NOT in allowlist."""
    if not text:
        return []
    known_offenders = ["ciprofloxacin", "aspirin", "steroid", "prednisone",
                       "levofloxacin", "moxifloxacin", "ibuprofen",
                       "sulfamethoxazole", "trimethoprim"]
    allow = {a.lower() for a in allowlist}
    hits: list[str] = []
    low = text.lower()
    for name in known_offenders:
        if name in low and name not in allow:
            hits.append(name)
    return sorted(set(hits))


def reasoning_node(state: PatientState) -> dict[str, Any]:
    if _mode() != "real":
        # PATCH 4: stub citations now point at REAL corpus chunk ids, so
        # stub-mode rehearsals show citations that actually resolve.
        payload = {
            "ddx": [
                {"diagnosis": "Pulmonary tuberculosis", "probability": "high",
                 "citations": ["NTP_PK_2019_pulmonary_symptoms",
                               "NTP_PK_2019_firstline_regimen"]},
                {"diagnosis": "Community-acquired pneumonia", "probability": "moderate",
                 "citations": ["WHO_EML_amoxicillin", "WHO_IMCI_amox_dosing"]},
            ],
            "confidence": 0.72,
            "management": "Refer to NTP DOTs center — TB treatment is free under NTP",
        }
        audit.log_event(state["case_id"], "REASONING_RESULT", payload,
                        provider="stub", confidence=0.72)
        return {"ddx": payload["ddx"], "confidence": payload["confidence"],
                "management": payload["management"], "proposed_medicines": [],
                "prescription_draft": NO_MEDICINES, "prescription_source": "demo",
                "medication_plan_error": None, "workup": ["Clinician review and NTP referral"],
                "uncertainty_note": "Synthetic demonstration; not a clinical assessment."}

    allowlist = _config().get("allowlist_medicines") or []
    chunks = corpus.retrieve(dict(state))
    chunk_map = {chunk["id"]: chunk for chunk in chunks}
    context = {
        "demographics": state.get("demographics"),
        "chronic_conditions": state.get("chronic_conditions"),
        "symptoms": state.get("symptoms"),
        "imaging_findings": state.get("imaging_findings"),
        "triage_level": state.get("triage_level"),
        "medication_context": state.get("medication_context"),
        "confirmed_history": state.get("transcript_confirmed"),
        "vitals": state.get("vitals"),
    }
    # FIX: braces in the JSON schema made str.format() crash (KeyError on
    # 'diagnosis, probability, evidence'). Only substitute the allowlist —
    # leave every other brace untouched.
    system = _REASONING_SYSTEM_TMPL.replace("{allow}", ", ".join(allowlist))
    user = (f"PATIENT CONTEXT:\n{json.dumps(context, ensure_ascii=False)}\n\n"
            f"CORPUS CHUNKS (id -> text):\n"
            f"{json.dumps({c['id']: c['text'] for c in chunks}, ensure_ascii=False)}")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    parsed: dict | None = None
    provider = "none"
    warnings: list[str] = []
    try:
        raw, provider = _router().route_generate("reasoning", messages, temperature=0.2)
        parsed = _extract_json(raw)
    except Exception as e:
        audit.log_event(state["case_id"], "REASONING_DEGRADED",
                        {"error": str(e)}, provider=provider)
        return {"ddx": [], "confidence": 0.0, "degraded": "reasoning_unavailable",
                "proposed_medicines": [], "medication_plan_error": "Reasoning unavailable; no medication plan was established."}

    def valid_shape(p: dict | None) -> bool:
        if not isinstance(p, dict) or not isinstance(p.get("differential_diagnosis"), list):
            return False
        if not isinstance(p.get("initial_management"), str):
            return False
        for diagnosis in p["differential_diagnosis"]:
            if not isinstance(diagnosis, dict) or not isinstance(diagnosis.get("diagnosis"), str):
                return False
            if not isinstance(diagnosis.get("citations", []), list) or not all(isinstance(c, str) for c in diagnosis.get("citations", [])):
                return False
        confidence = p.get("confidence")
        return (not isinstance(confidence, bool) and isinstance(confidence, (int, float))
                and math.isfinite(confidence) and 0 <= confidence <= 1)

    if not valid_shape(parsed):
        parsed = None

    # Citation-gate: WARN mode with one regeneration retry.
    def _validate(p: dict) -> tuple[list[str], list[str]]:
        invalid: list[str] = []
        for d in p.get("differential_diagnosis") or []:
            for cid in d.get("citations") or []:
                if cid not in chunk_map:
                    invalid.append(cid)
        mgmt = p.get("initial_management") or ""
        offenders = _mentions_off_allowlist(mgmt, allowlist)
        return sorted(set(invalid)), offenders

    if parsed:
        invalid, offenders = _validate(parsed)
        if invalid or offenders:
            retry_user = (user + "\n\nCITATION_GATE_VIOLATIONS: "
                          f"invalid_citations={invalid}, off_allowlist_drugs={offenders}. "
                          "Regenerate obeying the rules.")
            try:
                raw2, provider = _router().route_generate(
                    "reasoning",
                    [{"role": "system", "content": system},
                     {"role": "user", "content": retry_user}],
                    temperature=0.1,
                )
                p2 = _extract_json(raw2)
                if valid_shape(p2):
                    parsed = p2
                    invalid, offenders = _validate(parsed)
            except Exception:
                pass  # keep first parsed; we'll strip below.
        if invalid or offenders:
            warnings = [f"invalid_citation:{c}" for c in invalid] + \
                       [f"off_allowlist:{d}" for d in offenders]
            # Strip invalid citations from the ddx we surface.
            for d in parsed.get("differential_diagnosis") or []:
                d["citations"] = [c for c in (d.get("citations") or []) if c in chunk_map]
            try:
                parsed["confidence"] = max(0.0, float(parsed.get("confidence", 0.0)) - 0.1)
            except Exception:
                parsed["confidence"] = 0.0

    if not parsed:
        audit.log_event(state["case_id"], "REASONING_DEGRADED",
                        {"reason": "no_parseable_json"}, provider=provider)
        return {"ddx": [], "confidence": 0.0, "degraded": "reasoning_unavailable",
                "proposed_medicines": [], "medication_plan_error": "No valid structured clinical plan was returned. Review or refer."}

    ddx = parsed.get("differential_diagnosis") or []
    valid_count = sum(len([c for c in (d.get("citations") or []) if c in chunk_map])
                      for d in ddx)
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    payload = {"ddx": ddx, "confidence": confidence,
               "management": parsed.get("initial_management"),
               "workup": parsed.get("recommended_workup"),
               "uncertainty_note": parsed.get("uncertainty_note"),
               "citation_warnings": warnings,
               "valid_citation_count": valid_count}
    proposed, medication_errors = normalize_medicines(parsed.get("proposed_medicines"))
    medication_errors.append(
        "Model medicine instructions have not been reconciled by a person. "
        "Review the assessment and workup, then submit the complete structured medicine plan "
        "(or explicitly confirm no medicines) before approval."
    )
    plan_error = " ".join(medication_errors)
    # A legacy/malformed model response must not be reported as 'no medicines'.
    payload.update(proposed_medicines=proposed, medication_plan_error=plan_error)
    audit.log_event(state["case_id"], "REASONING_RESULT", payload,
                    provider=provider, confidence=confidence)
    return {"ddx": ddx, "confidence": confidence,
            "management": parsed.get("initial_management"),
            "citation_warnings": warnings,
            "valid_citation_count": valid_count,
            "proposed_medicines": proposed, "prescription_source": "model",
            "prescription_draft": format_medicines(proposed),
            "medication_plan_error": plan_error,
            "workup": parsed.get("recommended_workup") if isinstance(parsed.get("recommended_workup"), list) else [],
            "uncertainty_note": str(parsed.get("uncertainty_note") or "")}


# ----------------------------------------------------------------------
# Shared medicine review, emergency referral, and decision recording
# ----------------------------------------------------------------------

def pharm_node(state: PatientState) -> dict[str, Any]:
    medicines = state.get("proposed_medicines")
    if medicines is None:
        medicines = draft_to_medicines(state.get("prescription_draft"))
    context = state.get("medication_context") or {}
    allowlist = _config().get("allowlist_medicines") or []
    report = review_medicines(medicines, context, allowlist=allowlist)
    if state.get("medication_plan_error"):
        report["status"] = "HOLD"
        report["approval_blocked"] = True
        report["not_assessed"].append(state["medication_plan_error"])
    audit.log_event(state["case_id"], "MEDICATION_REVIEW",
                    {"review": report, "patient_context": context,
                     "actor_id": state.get("decision_actor_id") or state.get("paramedic_id"),
                     "source": state.get("prescription_source")},
                    provider="local-rules", confidence=None)
    return {"proposed_medicines": medicines, "prescription_draft": format_medicines(report["medicines"]),
            "medication_review": report, "medication_allowlist": allowlist,
            "medication_review_acknowledged": False, "review_validation_error": None}


def emergency_node(state: PatientState) -> dict[str, Any]:
    payload = build_emergency_protocol(state.get("triage_level", "RED"),
                                       state.get("triage_rules", []))
    audit.log_event(state["case_id"], "EMERGENCY_PROTOCOL", payload,
                    provider="local-workflow", confidence=None)
    return {"prescription_draft": payload["instruction"], "proposed_medicines": [],
            "emergency_protocol": payload}


def finalize_node(state: PatientState) -> dict[str, Any]:
    hitl_status = state.get("hitl_status", "approved")
    if hitl_status == "rejected":
        etype, outcome = "REFERRAL_ISSUED", "rejected_by_paramedic"
    elif state.get("triage_level") == "RED":
        etype, outcome = "REFERRAL_ISSUED", "emergency_referral"
    else:
        etype = "RX_FINALIZED"
        outcome = "prescription_issued" if state.get("proposed_medicines") else "consultation_completed"
    payload = {"hitl_status": hitl_status,
               "prescription_draft": state.get("prescription_draft"),
               "triage_level": state.get("triage_level"),
               "ddx": state.get("ddx"), "outcome": outcome}
    payload.update(proposed_medicines=state.get("proposed_medicines", []),
                   medication_review=state.get("medication_review"),
                   review_acknowledged=state.get("medication_review_acknowledged", False),
                   review_note=state.get("hitl_reason"),
                   actor_id=state.get("decision_actor_id") or state.get("paramedic_id"))
    audit.log_event(state["case_id"], etype, payload, provider="local-workflow")
    audit.update_case(
        state["case_id"],
        completed_at=datetime.now(timezone.utc).isoformat(),
        triage_level=state.get("triage_level"),
        final_diagnosis=(state.get("ddx") or [{}])[0].get("diagnosis") if state.get("ddx") else None,
        outcome=outcome,
        hitl_outcome=hitl_status,
    )
    return {"hitl_status": hitl_status}
