"""Dawa Check: confirmed medicine reconciliation with the shared review engine."""
from __future__ import annotations
import json
import re
import sqlite3
import uuid
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from . import audit
from .medication_review import approval_error, normalize_medicines, review_medicines
from .domain import ActorContext
from . import access
from .media import PrivateMediaStore

class DawaState(TypedDict, total=False):
    case_id: str
    patient_id: str
    paramedic_id: str
    decision_actor_id: str
    bhu_id: str
    rx_image: str
    medicines: list[dict]
    patient: dict
    grid: list[dict]
    medication_review: dict
    reviewed_flags: list[dict]
    dawa_action: str
    review_validation_error: str | None
    review_acknowledged: bool
    review_note: str
    disposition: str
    provider_used: dict
    degraded: str | None
    clinical_context_snapshot: dict
    rx_artifact_id: str

_EXTRACT_PROMPT = (
    "List medicines visible in this prescription or medicine-bag photo, "
    "including partially legible ones (mark uncertain with ?). "
    "Copy name and pack strength only. Never infer administered dose from strength. "
    'Output ONLY JSON: {"medicines":[{"name":str,"strength":str|null}]}'
)

def _router():
    from .router import ModelRouter
    return ModelRouter()

def _extract_json(text: str) -> dict | None:
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        try:
            value = json.loads(match.group(0)) if match else None
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, dict) else None

def extract_node(state: DawaState) -> dict[str, Any]:
    if not state.get("rx_image"):
        return {"medicines": [], "degraded": "Manual entry: no image supplied"}
    from .router import load_config
    if load_config().get("agents", "stub") == "stub":
        return {"medicines": [], "degraded": "Demo mode: enter synthetic medicines manually"}
    provider = "none"
    for _ in range(2):
        try:
            raw, provider = _router().route_vision("vision_rx", state["rx_image"], _EXTRACT_PROMPT)
            parsed = _extract_json(raw)
            if parsed and isinstance(parsed.get("medicines"), list):
                medicines, errors = normalize_medicines(parsed["medicines"])
                if not errors:
                    audit.log_event(state["case_id"], "RX_EXTRACT_OK",
                                    {"medicines": medicines}, provider=provider)
                    return {"medicines": medicines, "provider_used": {"vision_rx": provider}}
        except Exception:
            # Provider exception text can contain URLs/credentials; do not persist it.
            pass
    audit.log_event(state["case_id"], "RX_EXTRACT_FAILED",
                    {"reason": "No valid medicine list; manual confirmation required"}, provider=provider)
    return {"medicines": [], "degraded": "Extraction unavailable; enter and confirm medicines manually"}

def _gate(state: DawaState, name: str) -> dict:
    return {"gate": name, **{key: state.get(key) for key in (
        "case_id", "patient_id", "medicines", "patient", "grid", "medication_review",
        "reviewed_flags", "degraded", "review_validation_error")}}

def _invalid(message: str) -> dict:
    return {"dawa_action": "invalid", "review_validation_error": message}

def confirm_gate(state: DawaState) -> dict[str, Any]:
    payload = interrupt(_gate(state, "dawa_confirm"))
    if not isinstance(payload, dict) or payload.get("action") != "confirm":
        return _invalid("Confirm the medicine list and patient context explicitly.")
    medicines, errors = normalize_medicines(payload.get("medicines"))
    if errors:
        return _invalid(" ".join(errors))
    if not medicines:
        return _invalid("Enter at least one medicine to perform Dawa Check. Empty extraction is not a completed review.")
    patient = payload.get("patient")
    if not isinstance(patient, dict):
        return _invalid("Provide structured patient context; leave unknown values unknown.")
    decision_actor_id = payload.get("_actor_id") or state["paramedic_id"]
    audit.log_event(state["case_id"], "RX_EXTRACTED",
                    {"medicines": medicines, "patient": patient,
                     "actor_id": decision_actor_id}, provider="paramedic")
    return {"medicines": medicines, "patient": patient,
            "decision_actor_id": decision_actor_id,
            "dawa_action": "confirmed", "review_validation_error": None}

def check_node(state: DawaState) -> dict[str, Any]:
    report = review_medicines(state.get("medicines", []), state.get("patient", {}))
    audit.log_event(state["case_id"], "MEDICATION_REVIEW",
                    {"review": report, "patient_context": state.get("patient"),
                     "actor_id": state.get("decision_actor_id") or state["paramedic_id"],
                     "source": "dawa_confirmed"},
                    provider="local-rules")
    for row in report["medicines"]:
        if row.get("flags"):
            audit.log_event(state["case_id"], "MEDICINE_FLAGGED",
                            {"id": row["id"], "name": row["name"], "flags": row["flags"]},
                            provider="local-rules")
    return {"grid": report["medicines"], "medication_review": report,
            "review_acknowledged": False, "review_validation_error": None}

def dawa_hitl_gate(state: DawaState) -> dict[str, Any]:
    payload = interrupt(_gate(state, "dawa_review"))
    if not isinstance(payload, dict):
        return _invalid("Choose a review action explicitly.")
    action = payload.get("action")
    decision_actor_id = payload.get("_actor_id") or state["paramedic_id"]
    if action == "modify":
        return {"dawa_action": "modify", "decision_actor_id": decision_actor_id,
                "review_validation_error": None}
    report = state.get("medication_review") or {}
    fresh = review_medicines(state.get("medicines", []), state.get("patient", {}))
    if fresh["input_hash"] != report.get("input_hash"):
        return {"dawa_action": "recheck", "decision_actor_id": decision_actor_id,
                "review_validation_error": None}
    reason = payload.get("reason")
    reason = reason.strip() if isinstance(reason, str) else ""
    if action == "review_flag":
        flag_id = payload.get("flag_id")
        flag = next((f for f in report.get("flags", []) if f["id"] == flag_id), None)
        if not flag or len(reason) < 10:
            return _invalid("Select an existing finding and record a meaningful review note (at least 10 characters).")
        note = {"flag_id": flag_id, "code": flag["code"], "reason": reason,
                "review_hash": report["input_hash"], "actor_id": decision_actor_id}
        audit.log_event(state["case_id"], "FLAG_REVIEWED", note, provider="paramedic")
        return {"reviewed_flags": [*state.get("reviewed_flags", []), note],
                "decision_actor_id": decision_actor_id,
                "dawa_action": "review_flag", "review_validation_error": None}
    if action != "finalize":
        return _invalid("Choose edit, record a finding review, or complete the review.")
    disposition = payload.get("disposition")
    acknowledged = payload.get("review_acknowledged") is True
    if disposition == "refer":
        if not acknowledged or len(reason) < 10:
            return _invalid("Acknowledge the unresolved findings and record the referral/reconciliation plan (at least 10 characters).")
    elif disposition == "reviewed":
        error = approval_error(report, acknowledged, reason)
        if error:
            return _invalid(error)
    else:
        return _invalid("Choose reviewed or referred; completion does not authorize dispensing.")
    return {"dawa_action": "finalize", "decision_actor_id": decision_actor_id,
            "review_acknowledged": acknowledged,
            "review_note": reason, "disposition": disposition, "review_validation_error": None}

def finalize_node(state: DawaState) -> dict[str, Any]:
    referred = state.get("disposition") == "refer"
    audit.log_event(state["case_id"], "DAWA_FINALIZED",
                    {"review": state["medication_review"], "reviewed_flags": state.get("reviewed_flags", []),
                     "review_acknowledged": state.get("review_acknowledged"),
                     "review_note": state.get("review_note"), "disposition": state["disposition"],
                     "actor_id": state.get("decision_actor_id") or state["paramedic_id"],
                     "dispensing_authorized": False},
                    provider="paramedic")
    audit.update_case(state["case_id"], completed_at=datetime.now(timezone.utc).isoformat(),
                      outcome="dawa_referred" if referred else "dawa_check_completed",
                      hitl_outcome="referred" if referred else "reviewed")
    return {}

def build_dawa_graph(checkpointer=None):
    g = StateGraph(DawaState)
    for name, node in (("extract", extract_node), ("confirm_gate", confirm_gate),
                       ("check", check_node), ("dawa_hitl", dawa_hitl_gate), ("finalize", finalize_node)):
        g.add_node(name, node)
    g.add_edge(START, "extract")
    g.add_edge("extract", "confirm_gate")
    g.add_conditional_edges("confirm_gate", lambda s: s.get("dawa_action"),
                            {"invalid": "confirm_gate", "confirmed": "check"})
    g.add_edge("check", "dawa_hitl")
    g.add_conditional_edges("dawa_hitl", lambda s: s.get("dawa_action"),
                            {"invalid": "dawa_hitl", "review_flag": "dawa_hitl",
                             "modify": "confirm_gate", "recheck": "check", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)

@contextmanager
def dawa_graph_with_checkpointer(db_path: str = "smartsehat_checkpoints.db") -> Iterator[Any]:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        yield build_dawa_graph(checkpointer=saver)
    finally:
        conn.close()

def start_dawa_case(encounter_id: str, actor: ActorContext,
                    rx_artifact_id: str | None = None,
                    checkpoint_db: str = "smartsehat_checkpoints.db",
                    media_store: PrivateMediaStore | None = None):
    encounter = access.get_encounter(actor, encounter_id, "encounter:resume")
    if encounter.workflow_type != "dawa":
        raise ValueError("Encounter is not a Dawa encounter.")
    store = media_store or PrivateMediaStore()
    rx_path = (store.authorized_path(actor, rx_artifact_id,
                                     expected_purpose="prescription_image",
                                     expected_encounter_id=encounter_id)
               if rx_artifact_id else "")
    access.mark_encounter_active(actor, encounter_id)
    patient = access.medication_context(encounter.snapshot)
    initial = {"case_id": encounter_id, "patient_id": encounter.patient_id,
               "paramedic_id": actor.actor_id, "bhu_id": encounter.facility_id,
               "rx_image": rx_path, "rx_artifact_id": rx_artifact_id,
               "patient": patient, "clinical_context_snapshot": encounter.snapshot.to_dict()}
    with dawa_graph_with_checkpointer(checkpoint_db) as graph:
        return graph.invoke(initial, {"configurable": {"thread_id": encounter_id}})

def run_dawa_case(rx_image_path: str, actor: ActorContext,
                  checkpoint_db: str = "smartsehat_checkpoints.db", patient_id: str | None = None,
                  walk_in_reference: str | None = None, media_store: PrivateMediaStore | None = None):
    encounter = access.create_encounter(actor, "dawa", patient_id=patient_id,
                                        walk_in_reference=walk_in_reference)
    store = media_store or PrivateMediaStore()
    artifact_id = None
    if rx_image_path:
        artifact = store.store(actor, encounter.encounter_id, Path(rx_image_path).read_bytes(),
                               original_filename=rx_image_path, purpose="prescription_image",
                               modality_confirmed=True)
        artifact_id = artifact["artifact_id"]
    result = start_dawa_case(encounter.encounter_id, actor, artifact_id, checkpoint_db, store)
    return encounter.encounter_id, encounter.encounter_id, result

def resume_dawa_case(thread_id: str, actor: ActorContext,
                     checkpoint_db: str = "smartsehat_checkpoints.db", **payload):
    access.record_resume(actor, thread_id)
    payload["_actor_id"] = actor.actor_id
    with dawa_graph_with_checkpointer(checkpoint_db) as graph:
        config = {"configurable": {"thread_id": thread_id}}
        pending = set(graph.get_state(config).next)
        if (payload.get("action") == "confirm"
                and isinstance(payload.get("patient"), dict)
                and "confirm_gate" in pending):
            snapshot = access.update_patient_snapshot(actor, thread_id, payload["patient"])
            payload["patient"] = access.medication_context(snapshot)
        if "dawa_hitl" in pending:
            canonical = access.get_encounter(actor, thread_id).snapshot
            graph.update_state(config, {"patient": access.medication_context(canonical)})
        result = graph.invoke(Command(resume=payload), config)
    if "__interrupt__" not in result:
        access.mark_encounter_completed(actor, thread_id)
    return result
