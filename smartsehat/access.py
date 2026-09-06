"""Authorization and canonical encounter service layer."""
from __future__ import annotations
from dataclasses import replace
import json
import uuid
from typing import Any

from . import audit
from .domain import ActorContext, ClinicalContextSnapshot, EncounterContext, PatientContext


class AuthorizationError(PermissionError):
    pass


def require_permission(actor: ActorContext, permission: str, facility_id: str | None = None) -> None:
    if not isinstance(actor, ActorContext) or permission not in actor.permissions:
        raise AuthorizationError(f"Permission denied: {permission}")
    if facility_id is not None and facility_id not in actor.facility_ids:
        raise AuthorizationError("Cross-facility access is not authorized for this actor.")


def _loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def list_patients(actor: ActorContext) -> list[dict]:
    require_permission(actor, "patient:read", actor.active_facility_id)
    audit.init_db()
    conn = audit._connect()
    try:
        rows = conn.execute(
            "SELECT * FROM patients WHERE bhu_id=? ORDER BY name, patient_id",
            (actor.active_facility_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_patient(actor: ActorContext, patient_id: str) -> dict:
    audit.init_db()
    row = audit.get_patient(patient_id)
    if not row:
        raise LookupError("Patient not found.")
    facility = row.get("bhu_id")
    if not facility:
        raise AuthorizationError("Legacy patient has no verified facility ownership.")
    require_permission(actor, "patient:read", facility)
    return row


def _patient_context(row: dict, facility_id: str) -> PatientContext:
    return PatientContext(
        identity_kind="registered", patient_id=row["patient_id"],
        display_reference=row.get("name") or row["patient_id"], facility_id=facility_id,
        age=row.get("age"), weight_kg=None, sex=row.get("sex"),
        allergies=_loads(row.get("allergies_json")),
        chronic_conditions=_loads(row.get("chronic_conditions_json"), []), current_medicines=None,
        vitals=None, pregnant=None, gestational_weeks=None, aspirin_prescribed=None,
        renal_impairment=None, liver_disease=None,
        verification={"identity": "registered", "allergies": "recorded",
                      "current_medicines": "unknown", "vitals": "unknown",
                      "pregnancy": "unknown"},
    )


def create_encounter(actor: ActorContext, workflow_type: str, *, patient_id: str | None = None,
                     walk_in_reference: str | None = None) -> EncounterContext:
    if workflow_type not in {"consultation", "dawa"}:
        raise ValueError("Unsupported workflow type.")
    require_permission(actor, "encounter:create", actor.active_facility_id)
    if patient_id:
        patient = get_patient(actor, patient_id)
        if patient["bhu_id"] != actor.active_facility_id:
            raise AuthorizationError(
                "Select the patient's facility as the active facility before creating an encounter."
            )
        context = _patient_context(patient, patient["bhu_id"])
        identity_kind = "registered"
    else:
        if workflow_type != "dawa" or not isinstance(walk_in_reference, str) or not walk_in_reference.strip():
            raise ValueError("Dawa walk-ins require an explicit local reference.")
        context = PatientContext(
            identity_kind="walk_in", patient_id=None,
            display_reference=walk_in_reference.strip(), facility_id=actor.active_facility_id,
            verification={"identity": "walk_in_unregistered", "allergies": "unknown",
                          "current_medicines": "unknown", "vitals": "unknown",
                          "pregnancy": "unknown"},
        )
        identity_kind = "walk_in"
    prefix = "CASE" if workflow_type == "consultation" else "DAWA"
    encounter_id = f"{prefix}-{uuid.uuid4().hex[:12]}"
    captured_at = audit._now()
    snapshot_id = f"SNAP-{uuid.uuid4().hex}"
    snapshot = ClinicalContextSnapshot(snapshot_id, encounter_id, 1, captured_at,
                                       actor.actor_id, context)
    audit.init_db()
    conn = audit._connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO cases(case_id,patient_id,paramedic_id,bhu_id,started_at,workflow_type,"
            "actor_id,status,version,patient_context_version,patient_snapshot_json,identity_kind,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (encounter_id, patient_id, actor.actor_id, actor.active_facility_id, captured_at,
             workflow_type, actor.actor_id, "created", 1, 1,
             json.dumps(snapshot.to_dict(), ensure_ascii=False), identity_kind, captured_at),
        )
        conn.execute(
            "INSERT INTO patient_context_snapshots(snapshot_id,case_id,version,snapshot_json,captured_by,captured_at) "
            "VALUES(?,?,?,?,?,?)",
            (snapshot_id, encounter_id, 1, json.dumps(snapshot.to_dict(), ensure_ascii=False),
             actor.actor_id, captured_at),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return EncounterContext(encounter_id, workflow_type, patient_id, identity_kind,
                            actor.actor_id, actor.active_facility_id, captured_at,
                            "created", 1, 1, snapshot)


def _snapshot_from_json(value: str) -> ClinicalContextSnapshot:
    data = json.loads(value)
    patient = PatientContext(**data["patient"])
    return ClinicalContextSnapshot(data["snapshot_id"], data["encounter_id"],
                                   data["version"], data["captured_at"],
                                   data["captured_by"], patient)


def get_encounter(actor: ActorContext, encounter_id: str, permission: str = "encounter:read") -> EncounterContext:
    audit.init_db()
    row = audit.get_case(encounter_id)
    if not row:
        raise LookupError("Encounter not found.")
    if not row.get("workflow_type") or not row.get("patient_snapshot_json"):
        raise AuthorizationError("Legacy encounter has no canonical ownership snapshot and cannot be resumed here.")
    require_permission(actor, permission, row["bhu_id"])
    snapshot = _snapshot_from_json(row["patient_snapshot_json"])
    return EncounterContext(row["case_id"], row["workflow_type"], row.get("patient_id"),
                            row["identity_kind"], row["actor_id"], row["bhu_id"],
                            row["started_at"], row["status"], row["version"],
                            row["patient_context_version"], snapshot)


def list_encounters(actor: ActorContext, workflow_type: str | None = None) -> list[dict]:
    require_permission(actor, "encounter:read", actor.active_facility_id)
    conn = audit._connect()
    try:
        sql = "SELECT * FROM cases WHERE bhu_id=? AND patient_snapshot_json IS NOT NULL"
        params: list[Any] = [actor.active_facility_id]
        if workflow_type:
            sql += " AND workflow_type=?"
            params.append(workflow_type)
        sql += " ORDER BY started_at DESC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def mark_encounter_active(actor: ActorContext, encounter_id: str) -> None:
    encounter = get_encounter(actor, encounter_id, "encounter:resume")
    if encounter.status != "created":
        raise AuthorizationError("Only a newly created encounter can be started.")
    conn = audit._connect()
    try:
        conn.execute("UPDATE cases SET status='active', version=version+1, updated_at=? WHERE case_id=?",
                     (audit._now(), encounter.encounter_id))
        conn.commit()
    finally:
        conn.close()


def mark_encounter_completed(actor: ActorContext, encounter_id: str) -> None:
    encounter = authorize_mutation(actor, encounter_id)
    conn = audit._connect()
    try:
        conn.execute("UPDATE cases SET status='completed', version=version+1, updated_at=? WHERE case_id=?",
                     (audit._now(), encounter.encounter_id))
        conn.commit()
    finally:
        conn.close()


def record_resume(actor: ActorContext, encounter_id: str) -> None:
    encounter = get_encounter(actor, encounter_id, "encounter:resume")
    if encounter.status != "active":
        raise AuthorizationError("Only an active encounter can be resumed.")
    conn = audit._connect()
    try:
        conn.execute("UPDATE cases SET version=version+1,updated_at=? WHERE case_id=?",
                     (audit._now(), encounter_id))
        conn.commit()
    finally:
        conn.close()


def authorize_mutation(actor: ActorContext, encounter_id: str) -> EncounterContext:
    encounter = get_encounter(actor, encounter_id, "encounter:mutate")
    if encounter.status == "completed":
        raise AuthorizationError("Completed encounter context is immutable.")
    return encounter


def update_patient_snapshot(actor: ActorContext, encounter_id: str, medication_context: dict[str, Any]) -> ClinicalContextSnapshot:
    encounter = authorize_mutation(actor, encounter_id)
    if not isinstance(medication_context, dict):
        raise ValueError("Patient context must be structured.")
    old = encounter.snapshot.patient
    patient = replace(
        old, age=medication_context.get("age"),
        weight_kg=medication_context.get("weight_kg"),
        sex=medication_context.get("sex", old.sex),
        allergies=medication_context.get("allergies"),
        current_medicines=medication_context.get("current_meds"),
        vitals=medication_context.get("vitals"), pregnant=medication_context.get("pregnant"),
        gestational_weeks=medication_context.get("gestational_weeks"),
        aspirin_prescribed=medication_context.get("aspirin_prescribed"),
        renal_impairment=medication_context.get("renal_impairment"),
        liver_disease=medication_context.get("liver_disease"),
        verification={**old.verification,
                      "encounter_context_confirmed": medication_context.get("context_confirmed") is True},
    )
    version = encounter.patient_context_version + 1
    now, snapshot_id = audit._now(), f"SNAP-{uuid.uuid4().hex}"
    snapshot = ClinicalContextSnapshot(snapshot_id, encounter_id, version, now, actor.actor_id, patient)
    payload = json.dumps(snapshot.to_dict(), ensure_ascii=False)
    conn = audit._connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO patient_context_snapshots VALUES(?,?,?,?,?,?)",
                     (snapshot_id, encounter_id, version, payload, actor.actor_id, now))
        conn.execute("UPDATE cases SET patient_context_version=?,patient_snapshot_json=?,version=version+1,updated_at=? WHERE case_id=?",
                     (version, payload, now, encounter_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return snapshot


def medication_context(snapshot: ClinicalContextSnapshot) -> dict[str, Any]:
    p = snapshot.patient
    return {"age": p.age, "weight_kg": p.weight_kg, "sex": p.sex,
            "allergies": p.allergies,
            "current_meds": p.current_medicines, "vitals": p.vitals,
            "pregnant": p.pregnant, "gestational_weeks": p.gestational_weeks,
            "aspirin_prescribed": p.aspirin_prescribed,
            "renal_impairment": p.renal_impairment, "liver_disease": p.liver_disease,
            "context_confirmed": p.verification.get("encounter_context_confirmed", False)}


def get_timeline(actor: ActorContext, encounter_id: str) -> list[dict]:
    get_encounter(actor, encounter_id, "encounter:read")
    return audit.get_case_timeline(encounter_id)


def get_hitl_decisions(actor: ActorContext, encounter_id: str) -> list[dict]:
    get_encounter(actor, encounter_id, "encounter:read")
    return audit.get_hitl_decisions(encounter_id)


def log_authorized_event(actor: ActorContext, encounter_id: str, event_type: str, payload: dict,
                         provider: str = "authenticated-actor") -> int:
    # A new ledger event may be appended after clinical completion (for example,
    # delayed doctor advice), while the completed encounter context stays frozen.
    get_encounter(actor, encounter_id, "encounter:mutate")
    return audit.log_event(encounter_id, event_type,
                           {**payload, "actor_id": actor.actor_id}, provider=provider)


def authorize_export(actor: ActorContext, encounter_id: str) -> EncounterContext:
    return get_encounter(actor, encounter_id, "encounter:export")


def authorize_district_view(actor: ActorContext) -> None:
    require_permission(actor, "district:view")
