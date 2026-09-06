"""Encounter-keyed Streamlit state helpers with no Streamlit dependency."""
from __future__ import annotations
from copy import deepcopy
from typing import Any, MutableMapping


def _key(workflow: str) -> str:
    if workflow not in {"consultation", "dawa"}:
        raise ValueError("Unsupported workflow.")
    return f"{workflow}_encounters"


def register(state: MutableMapping[str, Any], workflow: str, encounter_id: str,
             *, thread_id: str, patient_id: str | None, facility_id: str,
             actor_id: str, status: str = "prepared", gate: dict | None = None) -> dict:
    bucket = state.setdefault(_key(workflow), {})
    if encounter_id in bucket:
        raise ValueError("Encounter UI state already exists.")
    record = {"encounter_id": encounter_id, "thread_id": thread_id,
              "patient_id": patient_id, "facility_id": facility_id,
              "actor_id": actor_id, "stage": status, "gate": deepcopy(gate or {}),
              "drafts": {}}
    bucket[encounter_id] = record
    state[f"active_{workflow}_id"] = encounter_id
    # Applied before the selectbox is instantiated on the next Streamlit run.
    state[f"pending_{workflow}_selection"] = encounter_id
    return record


def current(state: MutableMapping[str, Any], workflow: str) -> dict | None:
    encounter_id = state.get(f"active_{workflow}_id")
    return state.get(_key(workflow), {}).get(encounter_id)


def select(state: MutableMapping[str, Any], workflow: str, encounter_id: str) -> dict:
    record = state.get(_key(workflow), {}).get(encounter_id)
    if not record:
        raise LookupError("Encounter UI state not found.")
    state[f"active_{workflow}_id"] = encounter_id
    state.pop(f"pending_{workflow}_selection", None)
    return record


def update(state: MutableMapping[str, Any], workflow: str, target_encounter_id: str, **fields) -> dict:
    record = state.get(_key(workflow), {}).get(target_encounter_id)
    if not record:
        raise LookupError("Encounter UI state not found.")
    immutable = {"encounter_id", "patient_id", "facility_id", "actor_id"}
    if immutable.intersection(fields):
        raise ValueError("Encounter ownership fields are immutable in UI state.")
    record.update(deepcopy(fields))
    return record


def set_draft(state: MutableMapping[str, Any], workflow: str, encounter_id: str,
              name: str, value: Any) -> None:
    record = state.get(_key(workflow), {}).get(encounter_id)
    if not record:
        raise LookupError("Encounter UI state not found.")
    record["drafts"][name] = deepcopy(value)


def reconcile_gate_drafts(state: MutableMapping[str, Any], workflow: str,
                          encounter_id: str, gate: dict,
                          sources: set[str] | None = None) -> None:
    """Replace editable composites with the backend-normalized gate values."""
    if workflow == "consultation":
        fields = (("proposed_medicines", "medicine_edits"),
                  ("medication_context", "patient_context"))
    elif workflow == "dawa":
        fields = (("medicines", "medicines"), ("patient", "patient_context"))
    else:
        raise ValueError("Unsupported workflow.")
    for source, draft_name in fields:
        if source in gate and (sources is None or source in sources):
            set_draft(state, workflow, encounter_id, draft_name, gate[source])
