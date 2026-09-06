"""Single source for emergency handoff content within the reviewed project scope."""
from __future__ import annotations
from typing import Any


def build_emergency_protocol(triage_level: str, triggered_rules: list[str] | None) -> dict[str, Any]:
    """Return escalation-only guidance; no scenario-specific drug protocols are approved yet."""
    rules = [str(rule) for rule in (triggered_rules or []) if str(rule).strip()]
    if triage_level != "RED":
        return {"status": "REQUIRES_CLINICAL_REVIEW", "triage_level": triage_level,
                "triggered_rules": rules,
                "instruction": "Obtain clinical review and follow the approved local pathway.",
                "medicine_orders": []}
    return {
        "status": "UNSUPPORTED_EMERGENCY_PROTOCOL",
        "triage_level": "RED",
        "triggered_rules": rules,
        "required_action": "REQUIRES_URGENT_REFERRAL",
        "instruction": (
            "Urgent clinical assessment and emergency referral required. Monitor and support the "
            "patient within trained scope, arrange immediate transport, and follow the approved "
            "local protocol for the actual presenting emergency. This prototype has no clinically "
            "reviewed scenario-specific emergency treatment or medicine order."
        ),
        "medicine_orders": [],
    }
