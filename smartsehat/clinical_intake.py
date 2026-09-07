"""Versioned, deterministic Consultation intake contracts and validation.

This module collects one narrow respiratory/TB-oriented history pathway. It
does not diagnose TB and does not define treatment or vital-sign thresholds.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import unicodedata
import uuid
from typing import Any

from .domain import ClinicalObservation, PathwaySelection, TranscriptProvenance


INTAKE_DEFINITION_VERSION = "2026-09-07.1"
RESPIRATORY_PATHWAY_ID = "respiratory_tb_history"
RESPIRATORY_PATHWAY_VERSION = "2026-09-07.1"
SUPPORTED_LANGUAGES = ("ur", "en", "hi", "mixed", "unknown")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(original_text: str | None) -> dict[str, Any]:
    """Return derived matching text while retaining the immutable original."""
    original = original_text if isinstance(original_text, str) else ""
    try:
        normalized = unicodedata.normalize("NFKC", original).casefold()
        normalized = re.sub(r"\s+", " ", normalized).strip()
        status = "NOT_REQUIRED" if normalized == original else "SUCCESS"
        return {"original_text": original, "normalized_text": normalized,
                "normalization_method": "unicode_nfkc_casefold_whitespace_v1",
                "normalization_status": status}
    except Exception as exc:
        return {"original_text": original, "normalized_text": None,
                "normalization_method": "unicode_nfkc_casefold_whitespace_v1",
                "normalization_status": "FAILED", "error": str(exc)}


def detect_script_language(text: str | None) -> str:
    value = text or ""
    scripts = set()
    if re.search(r"[\u0600-\u06ff]", value):
        scripts.add("ur")
    if re.search(r"[\u0900-\u097f]", value):
        scripts.add("hi")
    if re.search(r"[A-Za-z]", value):
        scripts.add("en")
    if len(scripts) > 1:
        return "mixed"
    return next(iter(scripts), "unknown")


def captured_history(encounter_id: str, *, source: str,
                     source_media_artifact_id: str | None, raw_text: str | None,
                     declared_language: str | None, provider: str | None,
                     confidence: float | None = None,
                     degraded_reason: str | None = None) -> dict[str, Any]:
    raw = raw_text if isinstance(raw_text, str) and raw_text.strip() else None
    history = TranscriptProvenance(
        encounter_id=encounter_id, source=source,
        source_media_artifact_id=source_media_artifact_id, raw_text=raw,
        corrected_text=None, accepted_text=None,
        declared_language=declared_language,
        detected_language=detect_script_language(raw), provider=provider,
        confidence=confidence, normalization=normalize_text(raw),
        confirming_actor_id=None, confirmed_at=None, version=1,
        status="RAW" if raw else "UNAVAILABLE", degraded_reason=degraded_reason,
    )
    return history.to_dict()


def accept_history(history: dict[str, Any], corrected_text: Any, actor_id: str,
                   *, confirmed_at: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(corrected_text, str) or not corrected_text.strip():
        return None, "Accepted clinical history cannot be empty. Enter the patient statement before continuing."
    accepted = corrected_text.strip()
    updated = TranscriptProvenance(
        encounter_id=history["encounter_id"], source=history.get("source") or "typed",
        source_media_artifact_id=history.get("source_media_artifact_id"),
        raw_text=history.get("raw_text"), corrected_text=accepted,
        accepted_text=accepted, declared_language=history.get("declared_language"),
        detected_language=detect_script_language(accepted), provider=history.get("provider"),
        confidence=history.get("confidence"), normalization=normalize_text(accepted),
        confirming_actor_id=actor_id, confirmed_at=confirmed_at or _now(),
        version=int(history.get("version") or 1) + 1, status="ACCEPTED",
        degraded_reason=history.get("degraded_reason"),
    )
    return updated.to_dict(), None


QUESTION_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {"question_id": "resp_cough", "concept": "cough", "label": "Does the patient currently have a cough?",
     "input_type": "YES_NO_UNKNOWN", "required": True},
    {"question_id": "resp_cough_duration", "concept": "cough_duration", "label": "How long has the cough been present?",
     "input_type": "DURATION", "allowed_units": ["days", "weeks"], "important": True,
     "depends_on": {"question_id": "resp_cough", "assertion": "PRESENT"}},
    {"question_id": "resp_hemoptysis", "concept": "hemoptysis", "label": "Is there blood in the sputum?",
     "input_type": "YES_NO_UNKNOWN", "important": True},
    {"question_id": "resp_fever", "concept": "fever", "label": "Does the patient currently have fever?",
     "input_type": "YES_NO_UNKNOWN", "important": True},
    {"question_id": "resp_fever_duration", "concept": "fever_duration", "label": "How long has the fever been present?",
     "input_type": "DURATION", "allowed_units": ["days", "weeks"], "important": True,
     "depends_on": {"question_id": "resp_fever", "assertion": "PRESENT"}},
    {"question_id": "resp_night_sweats", "concept": "night_sweats", "label": "Does the patient have night sweats?",
     "input_type": "YES_NO_UNKNOWN", "important": False},
    {"question_id": "resp_weight_loss", "concept": "weight_loss", "label": "Has the patient had unintentional weight loss?",
     "input_type": "YES_NO_UNKNOWN", "important": False},
    {"question_id": "resp_severe_breathlessness", "concept": "severe_breathlessness", "label": "Is there severe breathlessness or inability to breathe?",
     "input_type": "YES_NO_UNKNOWN", "required": True},
    {"question_id": "safety_chest_pain", "concept": "chest_pain", "label": "Does the patient currently have chest pain?",
     "input_type": "YES_NO_UNKNOWN", "important": True},
    {"question_id": "safety_chest_pain_radiation", "concept": "chest_pain_radiation", "label": "With chest pain, does pain spread to the left arm or jaw?",
     "input_type": "YES_NO_UNKNOWN", "important": True,
     "depends_on": {"question_id": "safety_chest_pain", "assertion": "PRESENT"}},
    {"question_id": "safety_chest_pain_sweating", "concept": "chest_pain_sweating", "label": "With chest pain, is there current sweating or diaphoresis?",
     "input_type": "YES_NO_UNKNOWN", "important": True,
     "depends_on": {"question_id": "safety_chest_pain", "assertion": "PRESENT"}},
    {"question_id": "safety_unresponsive", "concept": "unresponsive", "label": "Is the patient currently unconscious or unresponsive?",
     "input_type": "YES_NO_UNKNOWN", "required": True},
    {"question_id": "safety_active_convulsion", "concept": "active_convulsion", "label": "Is the patient currently fitting or convulsing?",
     "input_type": "YES_NO_UNKNOWN", "required": True},
    {"question_id": "safety_pregnancy", "concept": "pregnancy", "label": "Is the patient currently pregnant?",
     "input_type": "YES_NO_UNKNOWN", "important": True,
     "patient_condition": {"field": "sex", "values": ["F", "female", "Female"]}},
    {"question_id": "safety_pregnancy_bleeding", "concept": "pregnancy_bleeding", "label": "If pregnant, is there current bleeding?",
     "input_type": "YES_NO_UNKNOWN", "important": True,
     "depends_on": {"question_id": "safety_pregnancy", "assertion": "PRESENT"}},
    {"question_id": "demographic_age", "concept": "age", "label": "Confirmed patient age",
     "input_type": "NUMBER_UNIT", "allowed_units": ["months", "years"], "important": True},
    {"question_id": "vital_temperature", "concept": "temperature", "label": "Measured temperature",
     "input_type": "NUMBER_UNIT", "allowed_units": ["C", "F"], "important": True},
    {"question_id": "vital_spo2", "concept": "spo2", "label": "Measured oxygen saturation (SpO₂)",
     "input_type": "NUMBER_UNIT", "allowed_units": ["%"], "important": True},
    {"question_id": "vital_respiratory_rate", "concept": "respiratory_rate", "label": "Measured respiratory rate",
     "input_type": "NUMBER_UNIT", "allowed_units": ["breaths/min"], "important": False},
    {"question_id": "vital_systolic_bp", "concept": "systolic_bp", "label": "Measured systolic blood pressure",
     "input_type": "NUMBER_UNIT", "allowed_units": ["mmHg"], "important": False},
    {"question_id": "vital_diastolic_bp", "concept": "diastolic_bp", "label": "Measured diastolic blood pressure",
     "input_type": "NUMBER_UNIT", "allowed_units": ["mmHg"], "important": False},
    {"question_id": "context_allergies", "concept": "allergies", "label": "Confirmed allergy history",
     "input_type": "LIST_OR_STATUS", "important": True},
    {"question_id": "context_current_medicines", "concept": "current_medicines", "label": "Confirmed current medicines",
     "input_type": "LIST_OR_STATUS", "important": True},
)


def question_definitions() -> list[dict[str, Any]]:
    return [{**item, "pathway": RESPIRATORY_PATHWAY_ID,
             "definition_version": INTAKE_DEFINITION_VERSION,
             "supported_languages": list(SUPPORTED_LANGUAGES)}
            for item in QUESTION_DEFINITIONS]


def _answer_assertion(answers: dict[str, Any], question_id: str) -> str:
    answer = answers.get(question_id)
    return answer.get("assertion", "UNASSESSED") if isinstance(answer, dict) else "UNASSESSED"


def relevant_questions(answers: dict[str, Any] | None,
                       patient: dict[str, Any] | None) -> list[dict[str, Any]]:
    answers, patient = answers or {}, patient or {}
    relevant = []
    for definition in question_definitions():
        dependency = definition.get("depends_on")
        if dependency and _answer_assertion(answers, dependency["question_id"]) != dependency["assertion"]:
            continue
        condition = definition.get("patient_condition")
        if condition and "values" in condition and patient.get(condition["field"]) not in condition["values"]:
            continue
        if condition and "value" in condition and patient.get(condition["field"]) is not condition["value"]:
            continue
        relevant.append(definition)
    return relevant


def sanitize_answers(answers: dict[str, Any] | None,
                     patient: dict[str, Any] | None) -> dict[str, Any]:
    value = dict(answers or {})
    active = {item["question_id"] for item in relevant_questions(value, patient)}
    return {key: answer for key, answer in value.items() if key in active}


_RESPIRATORY_TERMS = (
    "cough", "khansi", "کھانسی", "खांसी", "खाँसी", "hemoptysis",
    "sputum", "balgham", "بلغم", "बलगम",
)
_AMBIGUOUS_TERMS = (
    "fever", "bukhar", "بخار", "बुखार", "night sweats", "رات کو پسینہ",
    "weight loss", "وزن", "breathlessness", "سانس", "सांस",
)


def _term_spans(text: str, terms: tuple[str, ...]) -> list[dict[str, Any]]:
    spans = []
    for term in terms:
        flags = re.IGNORECASE if re.search(r"[A-Za-z]", term) else 0
        pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])" if flags else re.escape(term)
        match = re.search(pattern, text, flags)
        if match:
            spans.append({"text": match.group(0), "start": match.start(), "end": match.end()})
    return spans


def recommend_pathway(accepted_text: str) -> dict[str, Any]:
    respiratory = _term_spans(accepted_text, _RESPIRATORY_TERMS)
    ambiguous = _term_spans(accepted_text, _AMBIGUOUS_TERMS)
    if respiratory:
        return {"status": "SUPPORTED_CANDIDATE", "pathway_id": RESPIRATORY_PATHWAY_ID,
                "pathway_version": RESPIRATORY_PATHWAY_VERSION,
                "selection_method": "DETERMINISTIC_MATCH", "requires_worker_confirmation": True,
                "reason": "A bounded respiratory symptom term matched the accepted history.",
                "evidence": respiratory}
    if ambiguous:
        return {"status": "AMBIGUOUS", "pathway_id": RESPIRATORY_PATHWAY_ID,
                "pathway_version": RESPIRATORY_PATHWAY_VERSION,
                "selection_method": "WORKER_SELECTED", "requires_worker_confirmation": True,
                "reason": "Symptoms may be relevant, but cough/respiratory scope is not unambiguous.",
                "evidence": ambiguous}
    return {"status": "UNSUPPORTED_SCOPE", "pathway_id": None,
            "pathway_version": RESPIRATORY_PATHWAY_VERSION,
            "selection_method": "NO_SUPPORTED_MATCH", "requires_worker_confirmation": False,
            "reason": "The accepted complaint does not match the configured respiratory/TB history pathway.",
            "evidence": []}


def select_pathway(recommendation: dict[str, Any], payload: dict[str, Any], actor_id: str,
                   *, selected_at: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    if payload.get("action") == "acknowledge_unsupported":
        reason = payload.get("reason")
        if not isinstance(reason, str) or len(reason.strip()) < 10:
            return None, "Record a meaningful clinical review/referral reason for unsupported scope."
        return PathwaySelection(None, recommendation["pathway_version"], "UNSUPPORTED_SCOPE",
                                reason.strip(), "WORKER_CONFIRMED", actor_id,
                                selected_at or _now()).to_dict(), None
    if recommendation["status"] == "UNSUPPORTED_SCOPE":
        return None, "This complaint is outside the configured pathway. Record clinical review/referral."
    if payload.get("action") != "submit_intake":
        return None, "Submit the structured intake to continue."
    if payload.get("selected_pathway_id") != RESPIRATORY_PATHWAY_ID:
        return None, "Select only the configured respiratory/TB history pathway."
    if payload.get("pathway_confirmed") is not True:
        return None, "Worker confirmation of the selected pathway is required."
    return PathwaySelection(RESPIRATORY_PATHWAY_ID, RESPIRATORY_PATHWAY_VERSION,
                            "CONFIRMED", recommendation["reason"],
                            "WORKER_CONFIRMED", actor_id,
                            selected_at or _now()).to_dict(), None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalize_measurement(concept: str, value: float, unit: str) -> tuple[float, str]:
    if concept == "temperature" and unit == "F":
        return round((value - 32) * 5 / 9, 2), "C"
    if concept.endswith("_duration") and unit == "weeks":
        return value * 7, "days"
    return value, unit


def build_observations(answers: dict[str, Any] | None, patient: dict[str, Any],
                       actor_id: str, snapshot_version: int, encounter_id: str = "",
                       *, observed_at: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    supplied = answers or {}
    known = {item["question_id"] for item in question_definitions()}
    unknown = sorted(set(supplied) - known)
    answers = sanitize_answers(supplied, patient)
    definitions = relevant_questions(answers, patient)
    observations, errors = [], [f"{qid}: unknown question id." for qid in unknown]
    timestamp = observed_at or _now()
    allowed_assertions = {"PRESENT", "ABSENT", "UNKNOWN", "UNASSESSED"}
    for definition in definitions:
        qid = definition["question_id"]
        raw = answers.get(qid) if isinstance(answers.get(qid), dict) else {}
        assertion = raw.get("assertion", "UNASSESSED")
        if assertion not in allowed_assertions:
            errors.append(f"{qid}: invalid assertion state.")
            continue
        value = raw.get("value")
        unit = raw.get("unit")
        normalized_value, normalized_unit = value, unit
        if definition["input_type"] in {"DURATION", "NUMBER_UNIT"} and assertion == "PRESENT":
            number = _finite_number(value)
            if number is None or number < 0:
                errors.append(f"{qid}: enter a valid non-negative number.")
                continue
            if unit not in definition.get("allowed_units", []):
                errors.append(f"{qid}: select an allowed unit.")
                continue
            value = number
            normalized_value, normalized_unit = _normalize_measurement(definition["concept"], number, unit)
        elif definition["input_type"] == "LIST_OR_STATUS" and assertion == "PRESENT":
            if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
                errors.append(f"{qid}: enter one or more confirmed entries, or select none/unknown/unassessed.")
                continue
            value = [v.strip() for v in value]
            normalized_value = list(value)
            unit = normalized_unit = None
        elif assertion != "PRESENT":
            value = unit = normalized_value = normalized_unit = None
        observation = ClinicalObservation(
            observation_id=f"OBS-{uuid.uuid4().hex}", question_id=qid,
            encounter_id=encounter_id,
            concept=definition["concept"], value=value, unit=unit,
            normalized_value=normalized_value, normalized_unit=normalized_unit,
            assertion=assertion, source="WORKER_ENTERED", entered_by=actor_id,
            observed_at=timestamp, verification_state="CONFIRMED",
            snapshot_version=snapshot_version,
            definition_version=definition["definition_version"],
            source_reference=f"question:{qid}", confirmed_by=actor_id,
        )
        observations.append(observation.to_dict())
    return observations, errors


def missing_information(observations: list[dict[str, Any]], patient: dict[str, Any],
                        pathway_id: str | None) -> list[dict[str, Any]]:
    if pathway_id != RESPIRATORY_PATHWAY_ID:
        return []
    by_question = {item["question_id"]: item for item in observations}
    missing = []
    for definition in relevant_questions(by_question, patient):
        if not (definition.get("required") or definition.get("important")):
            continue
        observation = by_question.get(definition["question_id"])
        assertion = observation.get("assertion") if observation else "UNASSESSED"
        status = "COMPLETE" if assertion in {"PRESENT", "ABSENT"} else assertion
        if not observation:
            status = "UNASSESSED"
        if status != "COMPLETE":
            missing.append({"question_id": definition["question_id"],
                            "concept": definition["concept"], "label": definition["label"],
                            "status": status, "required": bool(definition.get("required"))})
    return missing


def observation_state(observations: list[dict[str, Any]], concept: str) -> dict[str, Any] | None:
    for observation in reversed(observations or []):
        if (observation.get("concept") == concept
                and observation.get("verification_state") == "CONFIRMED"):
            return observation
    return None
