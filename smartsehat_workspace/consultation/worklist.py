"""Worker actions projected from saved cases, events and scheduled follow-ups."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from .care import project, REFERRAL_ACTIVE, CALL_ACTIVE


def actions(case: dict, events: list[dict], urgency: dict, followup_job: dict | None, at: datetime) -> dict | None:
    if case["stage"] == "CLOSED":
        return None
    care = project(events)
    reply = care["latest_reply"]
    completed = {e["data"]["task_id"] for e in events if e["type"] in ("TASK_COMPLETED", "TASK_CANCELLED")}
    pending = [e for e in events if e["type"] == "TASK_CREATED" and e["data"]["task_id"] not in completed]
    due, scheduled = False, None
    if followup_job and followup_job["status"] in ("pending", "done"):
        scheduled = followup_job["due"]
        effective = at + timedelta(minutes=case.get("clock_minutes", 0))
        due = datetime.fromisoformat(scheduled) <= effective
        if followup_job["status"] == "done":
            last_due = max((e["seq"] for e in events if e["type"] == "FOLLOWUP_DUE"), default=0)
            last_followup = max((e["seq"] for e in events if e["type"] == "FOLLOWUP"), default=0)
            due = bool(last_due > last_followup)
    items = []
    level = urgency.get("level", "AMBER")
    if level == "RED":
        items.append("Urgent reassessment / referral")
    if pending:
        items.append(f"Return {len(pending)} doctor task(s)")
    if due:
        items.append("Follow-up due")
    if reply:
        receipt = next((g for g in care["guidance"] if g["reply_id"] == reply["reply_id"]), None)
        if not receipt or receipt.get("understood") is not True:
            items.append("Explain current doctor guidance / confirm understanding")
    if any(r["status"] in REFERRAL_ACTIVE for r in care["referrals"]):
        items.append("Update referral progress / transport")
    if any(c["status"] in CALL_ACTIVE for c in care["calls"]):
        items.append("Arrange or update doctor call")
    open_requests = sum(r["status"] == "open" for r in care["requests"])
    if open_requests:
        items.append(f"Awaiting doctor on {open_requests} question(s) / barrier(s)")
    sharing = bool(case.get("sent") and case["data"].get("consent"))
    if not sharing:
        items.append("Continue local collection / review sharing consent")
    elif not reply or case["stage"] in ("SENT", "EMERGENCY"):
        items.append("Awaiting current doctor review")
    if not items:
        items.append("Continue follow-up" if scheduled else "Continue saved consultation")
    return {"case_id": case["id"], "name": case["data"]["name"], "stage": case["stage"], "urgency": level,
            "actions": items, "pending_tasks": len(pending), "followup_due": due,
            "followup_at": scheduled, "sharing_active": sharing,
            "priority": (0 if level == "RED" else 1 if due else 2 if pending else 3, {"RED": 0, "AMBER": 1, "GREEN": 2}.get(level, 1), case["created"])}


def current_time() -> datetime:
    return datetime.now(timezone.utc)
