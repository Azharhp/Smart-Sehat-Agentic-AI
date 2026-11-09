"""Checkpointed consultation review with worker and doctor interrupt boundaries.

All nodes read a confirmed ledger snapshot. Store mutations remain atomic in
the service layer; graph replay never appends duplicate facts or messages.
"""
from __future__ import annotations
from contextlib import contextmanager
import sqlite3
from typing import TypedDict, Any

from langgraph.graph import START, END, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command

from .clinical import Age, LEVELS, evaluate, validate_measurement, config
from .reasoning import reason
from .verify import verify, fact_sentence
from .narrative import sentence
from .packet import compose
from .questions import next_question
from .privacy import EncryptedSerializer


class State(TypedDict, total=False):
    case: dict
    facts: list[dict]
    urgency: dict
    medcheck: dict
    events: list[dict]
    version: int
    live: bool
    inject: bool
    analysis: dict
    validation_errors: list[dict]
    verification: dict
    packet: dict
    completeness: float
    emergency: bool
    confirmation: dict
    doctor_reply: dict
    next_question: dict | None
    references: list[dict]
    resolved_medicines: list[dict]
    routing: dict
    patient_card_data: dict
    followup_due: str | None
    reuse_analysis: dict | None
    reviewers: list[dict]
    assigned_reviewer_id: str | None


def _age(state: State) -> Age:
    age = next(f["value"] for f in state["facts"] if f["key"] == "age")
    return Age.from_input(age["value"], age["unit"])


def _intake(state: State) -> dict:
    if not state["case"].get("id") or not state["facts"]:
        raise ValueError("An identified case and confirmed age are required.")
    _age(state)
    return {"validation_errors": [], "confirmation": {}, "doctor_reply": {}}


def _facts(state: State) -> dict:
    facts = state["facts"]
    if any(not f.get("confirmed") for f in facts) or len({f["key"] for f in facts}) != len(facts):
        raise ValueError("Review requires one current confirmed fact per key.")
    return {"facts": facts}


def _plan(state: State) -> dict:
    question, completeness = next_question(state["facts"], _age(state))
    return {"next_question": question, "completeness": completeness}


def _medicines(state: State) -> dict:
    from .medicines import reconcile
    values = {f["key"]: f["value"] for f in state["facts"]}
    names = [m["name"] for m in values.get("medicine_records") or []] or values.get("medicines") or []
    checked = reconcile(names, state["facts"], state["case"]["data"]["pregnant"], _age(state))
    return {"resolved_medicines": checked["medicines"], "medcheck": checked}


def _references(state: State) -> dict:
    from smartsehat_workspace import corpus
    terms = [f["key"] for f in state["facts"] if f["value"] is True]
    return {"references": corpus.retrieve({"symptoms": terms})}


def _reason(state: State) -> dict:
    analysis = state.get("reuse_analysis") if not state.get("live") else None
    if analysis is None:
        analysis = reason(state["facts"], state["urgency"], state.get("live", False))
    from .epidemiology import context_for
    # Local reviewed context is rendered separately; the model cannot invent it
    # or turn it into a patient diagnosis or an urgency decision.
    analysis = {**analysis, "fever_context": context_for(state["facts"])}
    urgency = {**state["urgency"], "fired": list(state["urgency"]["fired"])}
    concern = analysis.get("ai_concern", "GREEN")
    if LEVELS[concern] > LEVELS[urgency["level"]]:
        urgency["level"] = concern
        urgency["fired"].append({"rule_id": "AI-CONCERN-01", "level": concern, "layer": "ai", "text": "AI concern raised urgency; doctor review required", "fact_ids": analysis["concern_fact_ids"]})
    return {"analysis": analysis, "urgency": urgency}


def _routing(state: State) -> dict:
    from .medicines import resources
    from .routing import select_reviewer
    # This is a graph preview. Store routing owns the current availability audit.
    return {"routing": {**select_reviewer(state["case"], _age(state), state.get("reviewers", resources()["doctors"]), preferred=state.get("assigned_reviewer_id")), "urgency": state["urgency"]["level"], "preview": True}}


def _patient_card(state: State) -> dict:
    reply = state["doctor_reply"].get("reply", state["doctor_reply"])
    return {"patient_card_data": {"case_id": state["case"]["id"], "doctor_authored": True, "reply": reply}}


def _validate(state: State) -> dict:
    errors = []
    for f in state["facts"]:
        if f["key"] in config()["measurements"] and f["value"] is not None:
            result = validate_measurement(f["key"], f["value"], _age(state), f.get("unit"))
            if not result["ok"]:
                errors.append({"fact_id": f["id"], "key": f["key"], "message": result["error"]})
    current = {f["key"]: f for f in state["facts"]}
    records = current.get("medicine_records")
    names = current.get("medicines")
    if records and names and [row["name"] for row in records["value"] or []] != names["value"]:
        errors.append({"fact_id": records["id"], "key": "medicine_records", "message": "Structured medicine records and the confirmed medicine list disagree. Reconfirm the medicine reconciliation."})
    return {"validation_errors": errors}


def _verify(state: State) -> dict:
    facts = state["facts"]
    claims = [sentence(f) for f in facts] + state["analysis"]["claims"]
    if state.get("inject"):
        f = next((f for f in facts if f["key"] == "fever"), facts[0])
        claims.append(f"fever = {str(not f['value']).lower()} [{f['id']}]")
    result = verify(claims, facts)
    from .analysis_verifier import verify_analysis
    interpretation = verify_analysis(state["analysis"], facts)
    result["interpretation"] = interpretation
    result["failures"].extend(interpretation["failures"])
    for error in state.get("validation_errors", []):
        result["failures"].append({"sentence": f"{error['key']} needs remeasurement", "conflicting_fact_ids": [error["fact_id"]], "message": error["message"]})
    if result["failures"]:
        result["status"] = "BLOCKED"
    return {"verification": result}


def _compose(state: State) -> dict:
    return {"packet": compose(state["case"], state["facts"], state["urgency"], state["analysis"], state["medcheck"], state["events"], state["version"], state["verification"])}


def _worker(state: State) -> dict:
    return {"confirmation": interrupt({"gate": "worker_confirmation", "packet_version": state["version"], "verification": state["verification"], "fact_ids": state["packet"]["fact_ids"]})}


def _doctor(state: State) -> dict:
    return {"doctor_reply": interrupt({"gate": "doctor_response", "case_id": state["case"]["id"], "fact_ids": state["packet"]["fact_ids"]})}


@contextmanager
def graph(path: str, *, persistent: bool = True):
    conn = sqlite3.connect(path + ".workflow.db" if persistent else ":memory:", check_same_thread=False, timeout=15)
    try:
        saver = SqliteSaver(conn, serde=EncryptedSerializer(path))
        saver.setup()
        with conn:
            for table, column in (("checkpoints", "checkpoint"), ("writes", "value")):
                legacy = conn.execute(f"SELECT rowid,type,{column} FROM {table} WHERE type NOT LIKE 'encrypted:%'").fetchall()
                for rowid, kind, payload in legacy:
                    kind, payload = saver.serde.dumps_typed(saver.serde.loads_typed((kind, payload)))
                    conn.execute(f"UPDATE {table} SET type=?,{column}=? WHERE rowid=?", (kind, payload, rowid))
        builder = StateGraph(State)
        builder.add_node("intake", _intake)
        builder.add_node("danger_screen", lambda state: {"emergency": state["urgency"]["level"] == "RED"})
        builder.add_node("emergency_lane", lambda state: {"emergency": True})
        builder.add_node("extract_facts", _facts)
        builder.add_node("plan_question", _plan)
        builder.add_node("validate_measurement", _validate)
        builder.add_node("resolve_medicines", _medicines)
        builder.add_node("med_safety", lambda state: {"medcheck": state["medcheck"]})
        builder.add_node("retrieve_evidence", _references)
        builder.add_node("reason", _reason)
        builder.add_node("verify_claims", _verify)
        builder.add_node("compose_packet", _compose)
        builder.add_node("worker_confirmation", _worker)
        builder.add_node("route", _routing)
        builder.add_node("await_doctor", _doctor)
        builder.add_node("patient_card", _patient_card)
        builder.add_node("followup", lambda state: {"followup_due": state["patient_card_data"]["reply"].get("followup_due")})
        builder.add_edge(START, "intake")
        builder.add_edge("intake", "danger_screen")
        builder.add_conditional_edges("danger_screen", lambda state: "emergency_lane" if state["emergency"] else "extract_facts")
        builder.add_edge("emergency_lane", "extract_facts")
        chain = ["extract_facts", "plan_question", "validate_measurement", "resolve_medicines", "med_safety", "retrieve_evidence", "reason", "verify_claims", "compose_packet", "worker_confirmation"]
        for start, end in zip(chain, chain[1:]):
            builder.add_edge(start, end)
        builder.add_conditional_edges("worker_confirmation", lambda state: "route" if state["verification"]["status"] == "VERIFIED" and state.get("confirmation", {}).get("confirmed") else END)
        builder.add_edge("route", "await_doctor")
        builder.add_edge("await_doctor", "patient_card")
        builder.add_conditional_edges("patient_card", lambda state: "await_doctor" if state["patient_card_data"]["reply"].get("status") == "awaiting_information" else "followup")
        builder.add_edge("followup", END)
        yield builder.compile(checkpointer=saver)
    finally:
        conn.close()


def review(path: str, snapshot: State, *, persistent: bool = True) -> dict:
    with graph(path, persistent=persistent) as workflow:
        result = workflow.invoke(snapshot, {"configurable": {"thread_id": snapshot["case"]["id"]}})
        return {**result["packet"], "_derived_analysis": result["analysis"]}


def resume(path: str, cid: str, gate: str, value: dict) -> None:
    with graph(path) as workflow:
        cfg = {"configurable": {"thread_id": cid}}
        snapshot = workflow.get_state(cfg)
        expected = "worker_confirmation" if gate == "worker" else "await_doctor"
        if expected in snapshot.next:
            workflow.invoke(Command(resume=value), cfg)
