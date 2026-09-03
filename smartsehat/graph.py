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
import uuid
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
    updates: dict[str, Any] = {"hitl_action": resume.get("action")}
    if resume.get("action") == "modify" and resume.get("modified_content"):
        if not state.get("original_prescription"):
            updates["original_prescription"] = state.get("prescription_draft")
        updates["prescription_draft"] = resume["modified_content"]
    if resume.get("reason"):
        updates["hitl_reason"] = resume["reason"]
    return updates


def _hitl_gate(state: PatientState) -> dict[str, Any]:
    """Primary HITL gate. Freezes graph, waits for paramedic decision."""
    resume = interrupt(
        {
            "gate": "primary",
            "case_id": state["case_id"],
            "triage_level": state.get("triage_level"),
            "ddx": state.get("ddx"),
            "prescription_draft": state.get("prescription_draft"),
            # PATCH 3: degraded + confidence must reach the UI card.
            "degraded": state.get("degraded"),
            "confidence": state.get("confidence"),
        }
    )
    return _apply_resume(state, resume)


def _route_after_hitl(state: PatientState) -> str:
    """Used by BOTH gates: every modification re-runs the pharmacy check."""
    if state.get("hitl_action") == "modify":
        return "pharm_rerun"
    return "log_and_finalize"


def _hitl_gate_post(state: PatientState) -> dict[str, Any]:
    """Second HITL gate — reached after a modification.

    Bug #1 fix: a further 'modify' here re-enters the same safety loop
    (pharm_rerun -> this gate again) instead of falling through to finalize
    and silently discarding the paramedic's edit.
    PATCH 3: payload now full (triage, ddx, confidence, degraded) so the
    HITL card stays complete after a modification.
    """
    resume = interrupt(
        {
            "gate": "post_modify",
            "case_id": state["case_id"],
            "prescription_draft": state.get("prescription_draft"),
            "triage_level": state.get("triage_level"),
            "ddx": state.get("ddx"),
            "confidence": state.get("confidence"),
            "degraded": state.get("degraded"),
        }
    )
    return _apply_resume(state, resume)


def _log_and_finalize(state: PatientState) -> dict[str, Any]:
    action = state.get("hitl_action", "approve")
    original_draft = state.get("original_prescription")
    final_draft = state.get("prescription_draft")
    edits_made = bool(original_draft and final_draft and original_draft != final_draft)

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
    }
    audit.log_event(
        state["case_id"],
        "HITL_DECISION",
        {
            "action": action,
            "hitl_status": hitl_status,
            "reason": state.get("hitl_reason"),
            "edits_made": edits_made,
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
        {"pharm_rerun": "pharm_rerun", "log_and_finalize": "log_and_finalize"},
    )
    g.add_edge("pharm_rerun", "hitl_gate_post")
    g.add_conditional_edges(
        "hitl_gate_post",
        _route_after_hitl,
        {"pharm_rerun": "pharm_rerun", "log_and_finalize": "log_and_finalize"},
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


def run_case(patient_id: str, paramedic_id: str, bhu_id: str = "BHU-0442",
             force_triage: str | None = None,
             audio_path: str | None = None,
             image_path: str | None = None,
             checkpoint_db: str = "smartsehat_checkpoints.db"):
    """Create a case row and start the graph. Returns (case_id, thread_id, first_result).

    Day-2: optional audio_path (fed to STT) and image_path (fed to Vision) are
    stashed on the initial state so real-mode agents can pick them up.
    """
    case_id = f"CASE-{uuid.uuid4().hex[:8]}"
    audit.create_case(case_id, patient_id, paramedic_id, bhu_id)
    thread_id = case_id
    initial: PatientState = {
        "case_id": case_id,
        "patient_id": patient_id,
        "has_image": True,
    }
    if force_triage:
        initial["force_triage"] = force_triage
    if audio_path:
        initial["audio_path"] = audio_path
    if image_path:
        initial["image_path"] = image_path
    with graph_with_checkpointer(checkpoint_db) as graph:
        config = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(initial, config)
    return case_id, thread_id, result


def resume_case(thread_id: str, action: str, modified_content: str | None = None,
                reason: str | None = None, transcript: str | None = None,
                checkpoint_db: str = "smartsehat_checkpoints.db"):
    """Resume a suspended graph.

    - Transcript gate uses {"action":"confirm","transcript": <edited text>}
    - HITL gates use {"action":"approve"|"modify"|"reject", ...}
    """
    payload: dict[str, Any] = {"action": action}
    if modified_content is not None:
        payload["modified_content"] = modified_content
    if reason is not None:
        payload["reason"] = reason
    if transcript is not None:
        payload["transcript"] = transcript
    with graph_with_checkpointer(checkpoint_db) as graph:
        config = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(Command(resume=payload), config)
    return result