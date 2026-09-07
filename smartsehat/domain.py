"""Typed identity, patient snapshot, and encounter contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


WorkflowType = Literal["consultation", "dawa"]
IdentityKind = Literal["registered", "walk_in"]
AssertionState = Literal["PRESENT", "ABSENT", "UNKNOWN", "UNASSESSED"]
VerificationState = Literal["CONFIRMED", "UNCONFIRMED", "UNAVAILABLE"]


@dataclass(frozen=True)
class ActorContext:
    actor_id: str
    display_name: str
    role: str
    facility_ids: tuple[str, ...]
    active_facility_id: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    auth_source: str = "unknown"

    def __post_init__(self) -> None:
        if not self.actor_id or not self.active_facility_id:
            raise ValueError("Authenticated actor and active facility are required.")
        if self.active_facility_id not in self.facility_ids:
            raise ValueError("Active facility must be one of the actor's memberships.")

    def with_active_facility(self, facility_id: str) -> "ActorContext":
        if facility_id not in self.facility_ids:
            raise ValueError("Actor is not a member of that facility.")
        return ActorContext(self.actor_id, self.display_name, self.role,
                            self.facility_ids, facility_id, self.permissions,
                            self.auth_source)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["facility_ids"] = list(self.facility_ids)
        value["permissions"] = sorted(self.permissions)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ActorContext":
        return cls(actor_id=value["actor_id"], display_name=value["display_name"],
                   role=value["role"], facility_ids=tuple(value["facility_ids"]),
                   active_facility_id=value["active_facility_id"],
                   permissions=frozenset(value["permissions"]),
                   auth_source=value.get("auth_source", "unknown"))


@dataclass(frozen=True)
class PatientContext:
    identity_kind: IdentityKind
    patient_id: str | None
    display_reference: str
    facility_id: str
    age: float | None = None
    age_unit: str | None = "years"
    weight_kg: float | None = None
    sex: str | None = None
    allergies: Any = None
    chronic_conditions: Any = None
    current_medicines: Any = None
    vitals: Any = None
    pregnant: bool | None = None
    gestational_weeks: float | None = None
    aspirin_prescribed: bool | None = None
    renal_impairment: bool | None = None
    liver_disease: bool | None = None
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TranscriptProvenance:
    encounter_id: str
    source: str
    source_media_artifact_id: str | None
    raw_text: str | None
    corrected_text: str | None
    accepted_text: str | None
    declared_language: str | None
    detected_language: str | None
    provider: str | None
    confidence: float | None
    normalization: dict[str, Any]
    confirming_actor_id: str | None
    confirmed_at: str | None
    version: int
    status: str
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClinicalObservation:
    observation_id: str
    encounter_id: str
    question_id: str
    concept: str
    value: Any
    unit: str | None
    normalized_value: Any
    normalized_unit: str | None
    assertion: AssertionState
    source: str
    entered_by: str
    observed_at: str
    verification_state: VerificationState
    snapshot_version: int
    definition_version: str
    source_reference: str | None = None
    evidence_span: dict[str, Any] | None = None
    experiencer: str = "patient"
    temporality: str = "current"
    confirmed_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PathwaySelection:
    pathway_id: str | None
    pathway_version: str
    status: str
    reason: str
    selection_method: str
    selected_by: str | None
    selected_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TriageAssessment:
    status: str
    rule_version: str
    findings: tuple[dict[str, Any], ...] = ()
    unresolved_evidence: tuple[dict[str, Any], ...] = ()
    missing_information: tuple[dict[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    assessed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClinicalContextSnapshot:
    snapshot_id: str
    encounter_id: str
    version: int
    captured_at: str
    captured_by: str
    patient: PatientContext
    history: TranscriptProvenance | None = None
    observations: tuple[ClinicalObservation, ...] = ()
    pathway: PathwaySelection | None = None
    triage: TriageAssessment | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value


@dataclass(frozen=True)
class EncounterContext:
    encounter_id: str
    workflow_type: WorkflowType
    patient_id: str | None
    identity_kind: IdentityKind
    actor_id: str
    facility_id: str
    created_at: str
    status: str
    version: int
    patient_context_version: int
    snapshot: ClinicalContextSnapshot
