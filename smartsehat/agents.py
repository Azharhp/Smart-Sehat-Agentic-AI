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
from .clinical_intake import (
    accept_history, build_observations, captured_history, question_definitions,
    recommend_pathway, relevant_questions, select_pathway,
)
from .state import PatientState
from .medication_review import (
    NO_MEDICINES, draft_to_medicines, format_medicines,
    normalize_medicines, review_medicines,
)
from .emergency import build_emergency_protocol

# ----------------------------------------------------------------------
# Stub/real mode helpers
# ----------------------------------------------------------------------

# Stub mode only: an explicitly-labeled demo transcript that keeps the
# offline/demo consultation flow deterministic. Real mode never uses this.
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
    typed = state.get("typed_complaint")
    if isinstance(typed, str) and typed.strip():
        history = captured_history(
            state["case_id"], source="typed", source_media_artifact_id=None,
            raw_text=typed, declared_language=state.get("stt_language"), provider="worker",
        )
        audit.log_event(state["case_id"], "HISTORY_CAPTURED",
                        {"source": "typed", "status": history["status"]}, provider="worker")
        return {"transcript": typed, "history": history}
    # Real mode with audio: route through the provider chain. A total ASR
    # failure produces an explicit degraded/unavailable history — never a
    # canned fallback that could inject symptoms the patient did not state.
    if state.get("audio_path") and _mode() == "real":
        try:
            text, spec = _router().route_transcribe("stt", state["audio_path"])
            history = captured_history(
                state["case_id"], source="audio",
                source_media_artifact_id=state.get("audio_artifact_id"), raw_text=text,
                declared_language=state.get("stt_language"), provider=spec,
            )
            audit.log_event(state["case_id"], "STT_RESULT",
                            {"transcript": text}, provider=spec, confidence=None)
            return {"transcript": text, "history": history,
                    "provider_used": {"stt": spec}}
        except Exception as e:
            history = captured_history(
                state["case_id"], source="audio",
                source_media_artifact_id=state.get("audio_artifact_id"), raw_text=None,
                declared_language=state.get("stt_language"), provider=None,
                degraded_reason="stt_unavailable",
            )
            audit.log_event(state["case_id"], "STT_FAILED",
                            {"error": str(e)}, provider="none")
            return {"transcript": None, "history": history, "degraded": "stt_unavailable"}
    # Stub mode only — a canned demo transcript, no API call. This is the
    # explicit demonstration path; it is never used in real mode where audio
    # is missing or ASR fails (that path returns an UNAVAILABLE history above).
    if _mode() != "real":
        history = captured_history(
            state["case_id"], source="audio" if state.get("audio_path") else "stub_demo",
            source_media_artifact_id=state.get("audio_artifact_id"),
            raw_text=_STUB_TRANSCRIPT, declared_language=state.get("stt_language"),
            provider="stub", confidence=1.0,
        )
        audit.log_event(state["case_id"], "STT_RESULT",
                        {"transcript": _STUB_TRANSCRIPT}, provider="stub", confidence=1.0)
        return {"transcript": _STUB_TRANSCRIPT, "history": history}
    # Real mode with no audio and no typed complaint: explicit degraded state.
    history = captured_history(
        state["case_id"], source="typed", source_media_artifact_id=None,
        raw_text=None, declared_language=state.get("stt_language"), provider=None,
        degraded_reason="typed_history_required",
    )
    audit.log_event(state["case_id"], "STT_SKIPPED",
                    {"reason": history["degraded_reason"]}, provider="none")
    return {"transcript": None, "history": history}


# ----------------------------------------------------------------------
# Voice: transcript confirmation gate (HITL)
# ----------------------------------------------------------------------

def transcript_gate(state: PatientState) -> dict[str, Any]:
    history = state.get("history") or captured_history(
        state["case_id"], source="typed", source_media_artifact_id=None,
        raw_text=state.get("transcript"), declared_language=state.get("stt_language"), provider=None,
    )
    resume = interrupt({
        "gate": "transcript",
        "case_id": state["case_id"],
        "history": history,
        "transcript": history.get("raw_text") or "",
        "raw_text": history.get("raw_text"),
        "corrected_text": history.get("corrected_text") or history.get("raw_text") or "",
        "validation_error": state.get("history_validation_error"),
        "critical_evidence_removed": state.get("critical_evidence_removed") or [],
    })
    resume_payload = resume if isinstance(resume, dict) else {}
    if resume_payload.get("action") not in {"confirm_history", "confirm"}:
        return {"history_validation_error": "Confirm the accepted history using the history confirmation action."}
    corrected = (resume_payload["corrected_text"] if "corrected_text" in resume_payload
                 else resume_payload.get("transcript"))
    decision_actor_id = resume_payload.get("_actor_id") or state.get("paramedic_id")
    accepted, error = accept_history(history, corrected, decision_actor_id)
    if error:
        return {"history_validation_error": error}

    raw_text = history.get("raw_text")
    removed: list[str] = []
    if raw_text and raw_text.strip() != accepted["accepted_text"]:
        raw_assessment = triage_evaluate({"case_id": state["case_id"], "history": {
            **history, "accepted_text": raw_text, "status": "ACCEPTED"}})
        accepted_assessment = triage_evaluate({"case_id": state["case_id"], "history": accepted})
        if raw_assessment.get("status") == "RED" and accepted_assessment.get("status") != "RED":
            removed = raw_assessment.get("triggered_rules") or []
            if (resume_payload.get("critical_evidence_removal_acknowledged") is not True
                    or not isinstance(resume_payload.get("correction_reason"), str)
                    or len(resume_payload["correction_reason"].strip()) < 10):
                return {
                    "history_validation_error": (
                        "The correction removes configured urgent evidence. Confirm that removal and record why."
                    ),
                    "critical_evidence_removed": removed,
                }
    audit.log_event(state["case_id"], "VOICE_TRANSCRIPT_CONFIRMED",
                    {"transcript": accepted["accepted_text"],
                     "raw_text": raw_text, "corrected_text": accepted["corrected_text"],
                     "accepted_text": accepted["accepted_text"],
                     "paramedic_corrected": raw_text != accepted["accepted_text"],
                     "critical_evidence_removed": removed,
                     "correction_reason": resume_payload.get("correction_reason"),
                     "actor_id": decision_actor_id},
                    provider="paramedic", confidence=1.0)
    return {"history": accepted, "transcript_confirmed": accepted["accepted_text"],
            "paramedic_corrected": raw_text != accepted["accepted_text"],
            "decision_actor_id": decision_actor_id, "history_validation_error": None,
            "critical_evidence_removed": removed}


def intake_gate(state: PatientState) -> dict[str, Any]:
    history = state.get("history") or {}
    recommendation = recommend_pathway(history.get("accepted_text") or "")
    patient = (state.get("clinical_context_snapshot") or {}).get("patient") or {}
    current_answers = state.get("intake_answers") or {}
    resume = interrupt({
        "gate": "structured_intake", "case_id": state["case_id"],
        "recommendation": recommendation,
        "question_definitions": question_definitions(),
        "relevant_questions": relevant_questions(current_answers, patient),
        "answers": current_answers,
        "validation_error": state.get("intake_validation_error"),
        "early_triage": state.get("triage_assessment"),
    })
    payload = resume if isinstance(resume, dict) else {}
    actor_id = payload.get("_actor_id") or state.get("paramedic_id")
    selection, error = select_pathway(recommendation, payload, actor_id)
    if error:
        return {"intake_validation_error": error}
    if selection.get("status") == "UNSUPPORTED_SCOPE":
        observations: list[dict[str, Any]] = []
    else:
        if not isinstance(payload.get("answers"), dict):
            return {"intake_validation_error": "Structured question answers are required."}
        version = int((state.get("clinical_context_snapshot") or {}).get("version") or 0) + 1
        observations, errors = build_observations(
            payload["answers"], patient, actor_id, version, state["case_id"],
        )
        if errors:
            return {"intake_validation_error": " ".join(errors),
                    "intake_answers": payload["answers"]}
    audit.log_event(state["case_id"], "STRUCTURED_INTAKE_CONFIRMED",
                    {"pathway": selection, "observation_count": len(observations)},
                    provider="authenticated-worker")
    return {"pathway_selection": selection, "observations": observations,
            "intake_answers": payload.get("answers") or {}, "intake_validation_error": None}


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

def early_safety_node(state: PatientState) -> dict[str, Any]:
    """Deterministic early safety screen immediately after accepted history.

    A supported RED condition is routed straight to the emergency protocol,
    without waiting for structure extraction, image analysis or LLM reasoning
    (A05 / CLIN-005). Later model output can never downgrade this result —
    the emergency path is chosen here and the graph never reaches reasoning.
    """
    r = triage_evaluate(state)
    payload = {"triage_level": r.get("status"), "status": r.get("status"),
               "rules": r.get("triggered_rules") or [], "findings": r.get("findings") or [],
               "missing_information": r.get("missing_information") or [],
               "unresolved_evidence": r.get("unresolved_evidence") or [],
               "limitations": r.get("limitations") or [],
               "rule_version": r.get("rule_version"), "input_hash": r.get("input_hash"),
               "instruction": r.get("instruction", ""), "engine": "local-rules"}
    audit.log_event(state["case_id"], "EARLY_SAFETY_SCREEN", payload,
                    provider="local-rules", confidence=None)
    return {"triage_level": r.get("status"), "triage_assessment": r,
            "triage_rules": r.get("triggered_rules") or []}


def triage_node(state: PatientState) -> dict[str, Any]:
    # The same deterministic rules run in demo and real modes.
    r = triage_evaluate(state)
    payload = {"triage_level": r.get("status"), "status": r.get("status"),
               "rules": r.get("triggered_rules") or [], "findings": r.get("findings") or [],
               "missing_information": r.get("missing_information") or [],
               "unresolved_evidence": r.get("unresolved_evidence") or [],
               "limitations": r.get("limitations") or [],
               "rule_version": r.get("rule_version"), "input_hash": r.get("input_hash"),
               "instruction": r.get("instruction", ""), "engine": "local-rules"}
    audit.log_event(state["case_id"], "TRIAGE_DECISION", payload,
                    provider="local-rules", confidence=None)
    return {"triage_level": r.get("status"), "triage_rules": r.get("triggered_rules") or [],
            "triage_assessment": r}


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
