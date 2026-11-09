"""Dawa Check LangGraph — a second, independent workflow.

Reuses the existing ModelRouter (vision) and the append-only audit ledger.
No changes to graph.py / agents.py — this is a parallel StateGraph that
lives beside them.

Topology:
   START -> extract (vision) -> confirm_gate (HITL) -> check_node
        -> dawa_hitl_gate (HITL, iterates for dismissals)
        -> finalize
"""
from __future__ import annotations
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import audit
from . import dawa
from .medicine_vision import MEDICINE_IMAGE_PROMPT, parse_medicine_image, vision_failure_message


# ----------------------------------------------------------------------
# State
# ----------------------------------------------------------------------

class DawaState(TypedDict, total=False):
    case_id: str
    paramedic_id: str
    bhu_id: str
    rx_image: str
    medicines: list[dict]        # after extract or paramedic confirmation
    patient: dict                # {pregnant, age, current_meds, indication}
    grid: list[dict]             # dawa.check_medicines() output
    dismissed: list[dict]        # audit trail of dismissals
    dawa_action: str             # 'dismiss' | 'finalize'
    _dismiss: dict               # transient payload for apply_dismiss
    provider_used: dict
    degraded: str


# ----------------------------------------------------------------------
# Router accessors (lazy so tests can monkeypatch the module-level function)
# ----------------------------------------------------------------------

_EXTRACT_PROMPT = MEDICINE_IMAGE_PROMPT


def _router():
    from .router import ModelRouter
    return ModelRouter()


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
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
# Nodes
# ----------------------------------------------------------------------

def extract_node(state: DawaState) -> dict[str, Any]:
    rx = state.get("rx_image")
    if not rx:
        audit.log_event(state["case_id"], "RX_EXTRACT_FAILED",
                        {"reason": "no_image_path"}, provider="none")
        return {"medicines": [], "degraded": "no_rx_image"}

    from .consultation.ai import _bounded_json
    provider = "none"
    try:
        def request():
            raw, source = _router().route_vision("vision_rx", rx, _EXTRACT_PROMPT)
            return parse_medicine_image(raw), source
        parsed, provider = _bounded_json("dawa-vision:" + state["case_id"], request)
    except Exception as exc:
        reason = vision_failure_message(exc)
        audit.log_event(state["case_id"], "RX_EXTRACT_ERROR",
                        {"error": reason, "error_type": type(exc).__name__}, provider=provider)
        audit.log_event(state["case_id"], "RX_EXTRACT_FAILED",
                        {"reason": reason}, provider=provider)
        return {"medicines": [], "degraded": reason,
                "provider_used": {"vision_rx": provider}}
    meds = parsed["items"]
    if not meds:
        audit.log_event(state["case_id"], "RX_EXTRACT_FAILED", {"reason": "No readable medicine names found."}, provider=provider)
        return {"medicines": [], "degraded": "No readable medicine names were found. Try a clearer photo or enter them manually.", "provider_used": {"vision_rx": provider}}
    audit.log_event(state["case_id"], "RX_EXTRACT_OK",
                    {"medicines": meds}, provider=provider)
    return {"medicines": meds, "provider_used": {"vision_rx": provider}}


def confirm_gate(state: DawaState) -> dict[str, Any]:
    """Paramedic reviews the extraction and adds patient context."""
    resume = interrupt({
        "gate": "dawa_confirm",
        "case_id": state["case_id"],
        "medicines": state.get("medicines") or [],
        "degraded": state.get("degraded"),
        "provider": (state.get("provider_used") or {}).get("vision_rx", "none"),
    }) or {}

    medicines = resume.get("medicines")
    if medicines is None:
        medicines = state.get("medicines") or []
    patient = resume.get("patient") or {}
    audit.log_event(state["case_id"], "RX_EXTRACTED",
                    {"medicines": medicines, "patient": patient,
                     "paramedic_edited": bool(resume.get("medicines"))},
                    provider="paramedic")
    return {"medicines": medicines, "patient": patient}


def check_node(state: DawaState) -> dict[str, Any]:
    grid = dawa.check_medicines(state.get("medicines") or [],
                                state.get("patient") or {})
    for row in grid:
        if row.get("light") != "GREEN":
            audit.log_event(state["case_id"], "MEDICINE_FLAGGED",
                            {"name": row.get("name"),
                             "generic": row.get("generic"),
                             "light": row.get("light"),
                             "flags": row.get("flags")},
                            provider="local-rules")
    return {"grid": grid}


def dawa_hitl_gate(state: DawaState) -> dict[str, Any]:
    """Single-interrupt gate. A 'dismiss' resume is applied by
    `apply_dismiss` and the conditional edge routes back into this gate
    for another decision. A 'finalize' resume falls through to finalize.
    """
    resume = interrupt({
        "gate": "dawa_review",
        "case_id": state["case_id"],
        "grid": state.get("grid") or [],
        "dismissed": state.get("dismissed") or [],
    }) or {}
    action = resume.get("action", "finalize")
    updates: dict[str, Any] = {"dawa_action": action}
    if action == "dismiss":
        updates["_dismiss"] = {
            "medicine": resume.get("medicine"),
            "flag_code": resume.get("flag_code"),
            "reason": resume.get("reason") or "",
        }
    return updates


def apply_dismiss(state: DawaState) -> dict[str, Any]:
    """Mutate the grid by removing the requested flag, then log the dismissal.
    Runs as its own node so the change is captured in the checkpointer, then
    the graph routes back to `dawa_hitl_gate` for the next decision.
    """
    grid = [dict(r, flags=list(r.get("flags") or [])) for r in (state.get("grid") or [])]
    d = state.get("_dismiss") or {}
    code = d.get("flag_code")
    med_name = d.get("medicine")
    reason = d.get("reason") or ""
    dismissed_log = list(state.get("dismissed") or [])
    removed = False
    for row in grid:
        if med_name and row.get("name") != med_name:
            continue
        new_flags = []
        for f in row.get("flags") or []:
            if not removed and f.get("code") == code:
                removed = True
                dismissed_log.append({"medicine": row.get("name"),
                                      "code": code, "reason": reason})
                audit.log_event(state["case_id"], "FLAG_DISMISSED",
                                {"medicine": row.get("name"),
                                 "flag_code": code, "reason": reason},
                                provider="paramedic")
                continue
            new_flags.append(f)
        row["flags"] = new_flags
        if any(f.get("severity") == "RED" for f in new_flags):
            row["light"] = "RED"
        elif any(f.get("severity") == "YELLOW" for f in new_flags):
            row["light"] = "YELLOW"
        else:
            row["light"] = "GREEN"
        if removed:
            break
    return {"grid": grid, "dismissed": dismissed_log, "_dismiss": None,
            "dawa_action": None}


def _route_after_dawa_hitl(state: DawaState) -> str:
    return "apply_dismiss" if state.get("dawa_action") == "dismiss" else "finalize"


def finalize_node(state: DawaState) -> dict[str, Any]:
    grid = state.get("grid") or []
    kept = [r["name"] for r in grid if r.get("light") == "GREEN"]
    flagged = [{"name": r["name"], "light": r.get("light")}
               for r in grid if r.get("light") != "GREEN"]
    audit.log_event(state["case_id"], "DAWA_FINALIZED",
                    {"kept_green": kept, "flagged": flagged,
                     "dismissed": state.get("dismissed") or []},
                    provider="local-rules")
    audit.update_case(
        state["case_id"],
        completed_at=datetime.now(timezone.utc).isoformat(),
        outcome="dawa_check_completed",
        hitl_outcome="reviewed",
    )
    return {}


# ----------------------------------------------------------------------
# Graph assembly
# ----------------------------------------------------------------------

def build_dawa_graph(checkpointer=None):
    g = StateGraph(DawaState)
    g.add_node("extract", extract_node)
    g.add_node("confirm_gate", confirm_gate)
    g.add_node("check", check_node)
    g.add_node("dawa_hitl", dawa_hitl_gate)
    g.add_node("apply_dismiss", apply_dismiss)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "extract")
    g.add_edge("extract", "confirm_gate")
    g.add_edge("confirm_gate", "check")
    g.add_edge("check", "dawa_hitl")
    g.add_conditional_edges(
        "dawa_hitl",
        _route_after_dawa_hitl,
        {"apply_dismiss": "apply_dismiss", "finalize": "finalize"},
    )
    g.add_edge("apply_dismiss", "dawa_hitl")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer) if checkpointer else g.compile()


@contextmanager
def dawa_graph_with_checkpointer(
    db_path: str = "smartsehat_workspace_checkpoints.db",
) -> Iterator[Any]:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        yield build_dawa_graph(checkpointer=saver)
    finally:
        conn.close()


def run_dawa_case(rx_image_path: str, paramedic_id: str,
                  bhu_id: str = "BHU-0442",
                  checkpoint_db: str = "smartsehat_workspace_checkpoints.db"):
    """Create a case row and start the dawa graph. Returns (case_id, thread_id, first_result)."""
    case_id = f"DAWA-{uuid.uuid4().hex[:8]}"
    audit.create_case(case_id, patient_id="RX-WALKIN", paramedic_id=paramedic_id,
                      bhu_id=bhu_id)
    thread_id = case_id
    initial: DawaState = {
        "case_id": case_id,
        "paramedic_id": paramedic_id,
        "bhu_id": bhu_id,
        "rx_image": rx_image_path,
    }
    with dawa_graph_with_checkpointer(checkpoint_db) as graph:
        cfg = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(initial, cfg)
    return case_id, thread_id, result


def resume_dawa_case(thread_id: str,
                     checkpoint_db: str = "smartsehat_workspace_checkpoints.db",
                     **payload):
    """Generic resume — pass e.g. medicines=..., patient=... at confirm_gate,
    or action='dismiss'|'finalize' at dawa_hitl_gate."""
    with dawa_graph_with_checkpointer(checkpoint_db) as graph:
        cfg = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(Command(resume=payload), cfg)
    return result
