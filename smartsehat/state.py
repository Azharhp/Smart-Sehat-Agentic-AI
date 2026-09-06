"""Shared graph state."""
from __future__ import annotations
from typing import Any, TypedDict


class PatientState(TypedDict, total=False):
    case_id: str
    patient_id: str
    demographics: dict[str, Any]
    allergies: list[str]
    chronic_conditions: list[str]
    symptoms: list[str]
    vitals: dict[str, Any]
    triage_level: str
    imaging_findings: dict[str, Any]
    has_image: bool
    ddx: list[dict[str, Any]]
    confidence: float               # reasoning agent's own confidence (flows to audit ledger)
    management: str                 # reasoning agent's management line
    prescription_draft: str
    original_prescription: str      # pre-modification draft (audit fidelity — Bug #3 fix)
    paramedic_id: str
    decision_actor_id: str
    bhu_id: str
    proposed_medicines: list[dict[str, Any]]
    medication_context: dict[str, Any]
    medication_review: dict[str, Any]
    medication_allowlist: list[str]
    medication_plan_error: str | None
    medication_review_acknowledged: bool
    review_validation_error: str | None
    prescription_source: str
    workup: list[str]
    uncertainty_note: str
    emergency_protocol: dict[str, Any]
    clinical_context_snapshot: dict[str, Any]
    audio_artifact_id: str
    image_artifact_id: str
    hitl_status: str  # pending | approved | modified | rejected
    hitl_action: str  # approve | modify | reject
    hitl_reason: str
    provider_used: dict[str, str]
    audit_log: list[dict[str, Any]]
    force_triage: str  # test hook to force triage level
    # Day-2 additions
    audio_path: str
    image_path: str
    transcript: str                    # raw STT output
    transcript_confirmed: str          # paramedic-corrected final transcript
    paramedic_corrected: bool          # did the paramedic edit the STT text?
    chief_complaint: str
    duration_days: int
    missing_fields: list[str]
    followup_question_urdu: str
    parse_error: bool                  # structure_node soft failure flag
    triage_rules: list[str]            # deterministic rules that fired
    citation_warnings: list[str]       # reasoning citation-gate warnings
    valid_citation_count: int
    degraded: str                      # e.g. "reasoning_unavailable"
    # Language selector
    stt_language: str                  # ur | en | hi (speech-to-text language)
