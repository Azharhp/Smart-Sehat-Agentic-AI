"""LangGraph workflow for SmartSehat Day 2 (patched).

Fixes carried from Day 1:
- Bug #1: a modification submitted at the SECOND (post-modify) HITL gate
  re-enters the pharmacy re-check loop instead of being silently dropped.
  The edit-recheck rule is enforced by GRAPH TOPOLOGY, not by UI discipline.
- Bug #3: the pre-modification draft is stashed as `original_prescription`,
  so hitl_decisions records a true original-vs-final pair for the audit trail.
- ai_confidence in the HITL audit comes from the reasoning agent's state.

PATCH 3 (this version): the HITL interrupt payloads now carry `degraded`
and `confidence` (both gates), and the post-modify gate also carries
`triage_level` + `ddx` — so the UI warning "AI reasoning unavailable"
actually reaches the screen, and the HITL card stays complete after a
modification instead of collapsing to just the draft.
"""
from __future__ import annotations
import sqlite3
import json
import uuid
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Iterator

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import audit
from .agents import (
    emergency_node,
    finalize_node,
    pharm_node,
    reasoning_node,
    stt_node,
    structure_node,
    transcript_gate,
    triage_node,
    vision_node,
)
from .state import PatientState
from .domain import ActorContext
from . import access
from .media import PrivateMediaStore
from .medication_review import (
    approval_error, draft_to_medicines, format_medicines,
    normalize_medicines, review_medicines,
)


def _post_parallel(state: PatientState) -> dict[str, Any]:
    """Barrier node: fans in triage + vision. No side effects."""
    return {}


def _route_after_triage(state: PatientState) -> str:
    return "emergency" if state.get("triage_level") == "RED" else "reasoning"


def _apply_resume(state: PatientState, resume: dict[str, Any]) -> dict[str, Any]:
    """Shared resume handling for BOTH HITL gates.

    An edit is never silently dropped (Bug #1 fix): any 'modify' with content
    updates the draft and the conditional edge routes back through the
    pharmacy re-check. The original draft is stashed exactly once (Bug #3 fix)
    so the audit ledger can show a true original-vs-final comparison.
    """
    def invalid(message: str) -> dict:
        return {"hitl_action": "invalid", "review_validation_error": message}

    if not isinstance(resume, dict):
        return invalid("A structured review decision is required.")
    action = resume.get("action")
    if action not in {"approve", "modify", "reject", "update_context"}:
        return invalid("Choose approve, modify, reject or update patient context.")
    decision_actor_id = resume.get("_actor_id") or state.get("paramedic_id")
    updates: dict[str, Any] = {"hitl_action": action, "review_validation_error": None,
                               "decision_actor_id": decision_actor_id,
                               "hitl_reason": resume.get("reason") or ""}
    if action == "reject":
        if not isinstance(resume.get("reason"), str) or not resume["reason"].strip():
            return invalid("Record a reason and referral/review plan when rejecting.")
        return updates
    if action == "update_context":
        if state.get("triage_level") == "RED":
            return invalid("Continue the emergency referral. Medication editing belongs in a separate clinician review.")
        if not isinstance(resume.get("medication_context"), dict):
            return invalid("Patient context must be a structured object.")
        return {**updates, "medication_context": resume["medication_context"],
                "medication_review_acknowledged": False}
    if action == "modify":
        if state.get("triage_level") == "RED":
            return invalid("This emergency workflow issues a referral, not a new prescription. Follow the approved local emergency protocol.")
        medicines = resume.get("proposed_medicines")
        if medicines is None:
            text = resume.get("modified_content")
            if not isinstance(text, str) or not text.strip():
                return invalid("Enter structured medicines, or explicitly submit an empty list for no medicines.")
            medicines = draft_to_medicines(text)
        normalized, errors = normalize_medicines(medicines)
        if errors:
            return invalid(" ".join(errors))
        if "original_prescription" not in state:
            updates["original_prescription"] = state.get("prescription_draft") or ""
        updates.update(proposed_medicines=normalized,
                       prescription_draft=format_medicines(normalized),
                       prescription_source="worker", medication_plan_error=None,
                       medication_review_acknowledged=False)
        return updates
    if state.get("triage_level") != "RED":
        report = state.get("medication_review")
        if not report:
            return {**updates, "hitl_action": "recheck"}
        current = review_medicines(state.get("proposed_medicines", []),
                                   state.get("medication_context", {}),
                                   allowlist=state.get("medication_allowlist"))
        if current["input_hash"] != report.get("input_hash"):
            return {**updates, "hitl_action": "recheck"}
        error = approval_error(report, resume.get("medication_review_acknowledged"), resume.get("reason"))
        if error:
            return invalid(error)
    updates["medication_review_acknowledged"] = resume.get("medication_review_acknowledged") is True
    return updates


def _gate_payload(state: PatientState, gate: str) -> dict:
    return {"gate": gate, "case_id": state["case_id"],
            **{key: state.get(key) for key in (
                "patient_id", "triage_level", "ddx", "prescription_draft", "degraded",
                "confidence", "management", "workup", "uncertainty_note", "proposed_medicines",
                "medication_context", "medication_review", "review_validation_error",
                "emergency_protocol")}}


def _hitl_gate(state: PatientState) -> dict[str, Any]:
    """Primary HITL gate. Freezes graph, waits for paramedic decision."""
    resume = interrupt(_gate_payload(state, "primary"))
    return _apply_resume(state, resume)


def _route_after_hitl(state: PatientState) -> str:
    """Used by BOTH gates: every modification re-runs the pharmacy check."""
    if state.get("hitl_action") in {"modify", "update_context", "recheck"}:
        return "pharm_rerun"
    if state.get("hitl_action") == "invalid":
        return "invalid"
    return "log_and_finalize"


def _hitl_gate_post(state: PatientState) -> dict[str, Any]:
    """Second HITL gate — reached after a modification.

    Bug #1 fix: a further 'modify' here re-enters the same safety loop
    (pharm_rerun -> this gate again) instead of falling through to finalize
    and silently discarding the paramedic's edit.
    PATCH 3: payload now full (triage, ddx, confidence, degraded) so the
    HITL card stays complete after a modification.
    """
    resume = interrupt(_gate_payload(state, "post_modify"))
    return _apply_resume(state, resume)


def _log_and_finalize(state: PatientState) -> dict[str, Any]:
    action = state.get("hitl_action", "approve")
    original_draft = state.get("original_prescription")
    final_draft = state.get("prescription_draft")
    edits_made = original_draft is not None and original_draft != final_draft

    if action == "reject":
        hitl_status = "rejected"
    elif edits_made:
        hitl_status = "modified"   # approved with edits
    else:
        hitl_status = "approved"

    # Bug #3 fix: record a TRUE original-vs-final pair.
    original = {
        "prescription_draft": original_draft if original_draft is not None else final_draft,
        "ddx": state.get("ddx"),
    }
    final = {
        "prescription_draft": final_draft,
        "ddx": state.get("ddx"),
        "proposed_medicines": state.get("proposed_medicines", []),
        "medication_review": state.get("medication_review"),
        "review_acknowledged": state.get("medication_review_acknowledged", False),
    }
    audit.log_event(
        state["case_id"],
        "HITL_DECISION",
        {
            "action": action,
            "hitl_status": hitl_status,
            "reason": state.get("hitl_reason"),
            "edits_made": edits_made,
            "actor_id": state.get("decision_actor_id") or state.get("paramedic_id"),
            "medication_review_hash": (state.get("medication_review") or {}).get("input_hash"),
            "review_acknowledged": state.get("medication_review_acknowledged", False),
        },
        provider="paramedic",
    )
    audit.log_hitl_decision(
        state["case_id"],
        gate_type="prescription_gate",
        action=action,
        original=original,
        final=final,
        reason=state.get("hitl_reason"),
        ai_confidence=state.get("confidence"),  # real confidence, not hardcoded
    )
    return finalize_node({**state, "hitl_status": hitl_status})


def build_graph(checkpointer=None):
    g = StateGraph(PatientState)
    # Day-2 voice pipeline
    g.add_node("stt", stt_node)
    g.add_node("transcript_gate", transcript_gate)
    g.add_node("structure", structure_node)
    # Parallel clinical assessment
    g.add_node("triage", triage_node)
    g.add_node("vision", vision_node)
    g.add_node("post_parallel", _post_parallel)
    g.add_node("reasoning", reasoning_node)
    g.add_node("pharm", pharm_node)
    g.add_node("emergency", emergency_node)
    g.add_node("hitl_gate", _hitl_gate)
    g.add_node("pharm_rerun", pharm_node)
    g.add_node("hitl_gate_post", _hitl_gate_post)
    g.add_node("log_and_finalize", _log_and_finalize)

    g.add_edge(START, "stt")
    g.add_edge("stt", "transcript_gate")
    g.add_edge("transcript_gate", "structure")
    # Parallel fan-out after structure
    g.add_edge("structure", "triage")
    g.add_edge("structure", "vision")
    # Fan-in barrier
    g.add_edge("triage", "post_parallel")
    g.add_edge("vision", "post_parallel")
    # Conditional after triage
    g.add_conditional_edges(
        "post_parallel",
        _route_after_triage,
        {"emergency": "emergency", "reasoning": "reasoning"},
    )
    g.add_edge("reasoning", "pharm")
    g.add_edge("pharm", "hitl_gate")
    g.add_edge("emergency", "hitl_gate")

    # Bug #1 fix: BOTH gates route a 'modify' back through the pharmacy
    # re-check. The loop is enforced by topology, not by the UI.
    g.add_conditional_edges(
        "hitl_gate",
        _route_after_hitl,
        {"pharm_rerun": "pharm_rerun", "log_and_finalize": "log_and_finalize", "invalid": "hitl_gate"},
    )
    g.add_edge("pharm_rerun", "hitl_gate_post")
    g.add_conditional_edges(
        "hitl_gate_post",
        _route_after_hitl,
        {"pharm_rerun": "pharm_rerun", "log_and_finalize": "log_and_finalize", "invalid": "hitl_gate_post"},
    )
    g.add_edge("log_and_finalize", END)

    return g.compile(checkpointer=checkpointer) if checkpointer else g.compile()


@contextmanager
def graph_with_checkpointer(db_path: str = "smartsehat_checkpoints.db") -> Iterator[Any]:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        yield build_graph(checkpointer=saver)
    finally:
        conn.close()


def start_case(encounter_id: str, actor: ActorContext,
             force_triage: str | None = None,
             audio_artifact_id: str | None = None,
             image_artifact_id: str | None = None,
             checkpoint_db: str = "smartsehat_checkpoints.db",
             media_store: PrivateMediaStore | None = None):
    """Start an already-owned consultation encounter."""
    encounter = access.get_encounter(actor, encounter_id, "encounter:resume")
    if encounter.workflow_type != "consultation":
        raise ValueError("Encounter is not a Consultation encounter.")
    context = access.medication_context(encounter.snapshot)
    patient = encounter.snapshot.patient
    store = media_store or PrivateMediaStore()
    audio_path = (store.authorized_path(actor, audio_artifact_id, expected_purpose="audio",
                                        expected_encounter_id=encounter_id)
                  if audio_artifact_id else None)
    image_path = (store.authorized_path(actor, image_artifact_id,
                                        expected_purpose="xray_image",
                                        expected_encounter_id=encounter_id)
                  if image_artifact_id else None)
    access.mark_encounter_active(actor, encounter_id)
    initial: PatientState = {
        "case_id": encounter_id,
        "patient_id": encounter.patient_id,
        "paramedic_id": actor.actor_id, "bhu_id": encounter.facility_id,
        "has_image": bool(image_path),
        "demographics": {"age": patient.age, "sex": patient.sex},
        "allergies": context.get("allergies"),
        "chronic_conditions": patient.chronic_conditions or [],
        "medication_context": context,
        "clinical_context_snapshot": encounter.snapshot.to_dict(),
    }
    if force_triage:
        initial["force_triage"] = force_triage
    if audio_path:
        initial["audio_path"] = audio_path
        initial["audio_artifact_id"] = audio_artifact_id
    if image_path:
        initial["image_path"] = image_path
        initial["image_artifact_id"] = image_artifact_id
    with graph_with_checkpointer(checkpoint_db) as graph:
        config = {"configurable": {"thread_id": encounter_id}}
        result = graph.invoke(initial, config)
    return result


def run_case(patient_id: str, actor: ActorContext,
             force_triage: str | None = None, audio_path: str | None = None,
             image_path: str | None = None, checkpoint_db: str = "smartsehat_checkpoints.db",
             medication_context: dict | None = None,
             media_store: PrivateMediaStore | None = None):
    """Compatibility helper: create, snapshot, privately copy fixtures, then start."""
    encounter = access.create_encounter(actor, "consultation", patient_id=patient_id)
    if medication_context is not None:
        access.update_patient_snapshot(actor, encounter.encounter_id, medication_context)
    store = media_store or PrivateMediaStore()
    audio_id = image_id = None
    if audio_path:
        artifact = store.store(actor, encounter.encounter_id, Path(audio_path).read_bytes(),
                               original_filename=audio_path, purpose="audio")
        audio_id = artifact["artifact_id"]
    if image_path:
        artifact = store.store(actor, encounter.encounter_id, Path(image_path).read_bytes(),
                               original_filename=image_path, purpose="xray_image", modality_confirmed=True)
        image_id = artifact["artifact_id"]
    result = start_case(encounter.encounter_id, actor, force_triage, audio_id, image_id,
                        checkpoint_db, store)
    return encounter.encounter_id, encounter.encounter_id, result


def resume_case(thread_id: str, actor: ActorContext, action: str, modified_content: str | None = None,
                reason: str | None = None, transcript: str | None = None,
                checkpoint_db: str = "smartsehat_checkpoints.db",
                proposed_medicines: list[dict] | None = None,
                medication_context: dict | None = None,
                medication_review_acknowledged: bool = False):
    """Resume a suspended graph.

    - Transcript gate uses {"action":"confirm","transcript": <edited text>}
    - HITL gates use {"action":"approve"|"modify"|"reject", ...}
    """
    access.record_resume(actor, thread_id)
    payload: dict[str, Any] = {"action": action, "_actor_id": actor.actor_id}
    if modified_content is not None:
        payload["modified_content"] = modified_content
    if reason is not None:
        payload["reason"] = reason
    if transcript is not None:
        payload["transcript"] = transcript
    if proposed_medicines is not None:
        payload["proposed_medicines"] = proposed_medicines
    if medication_context is not None and action != "update_context":
        raise ValueError("Patient context can only be changed with update_context.")
    payload["medication_review_acknowledged"] = medication_review_acknowledged
    with graph_with_checkpointer(checkpoint_db) as graph:
        config = {"configurable": {"thread_id": thread_id}}
        pending = set(graph.get_state(config).next)
        if action == "confirm" and "transcript_gate" not in pending:
            raise ValueError("Transcript confirmation is not pending for this encounter.")
        if action != "confirm" and "transcript_gate" in pending:
            raise ValueError("Confirm the transcript before submitting a clinical review action.")
        if medication_context is not None:
            if not pending.intersection({"hitl_gate", "hitl_gate_post"}):
                raise ValueError("Patient context can only change at a clinical review gate.")
            snapshot = access.update_patient_snapshot(actor, thread_id, medication_context)
            payload["medication_context"] = access.medication_context(snapshot)
        if pending.intersection({"hitl_gate", "hitl_gate_post"}):
            canonical = access.get_encounter(actor, thread_id).snapshot
            graph.update_state(config, {"medication_context": access.medication_context(canonical)})
        result = graph.invoke(Command(resume=payload), config)
    if "__interrupt__" not in result:
        access.mark_encounter_completed(actor, thread_id)
    return result
