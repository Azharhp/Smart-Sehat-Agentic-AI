"""Review timings per handoff; wall-clock events are not clinical effort evidence."""
from datetime import datetime, timezone


def _stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def effort(case: dict, events: list[dict]) -> dict:
    anchors = []
    for event in events:
        data = event["data"]
        if event["type"] == "HANDOFF_STARTED":
            anchors.append({**data, "seq": event["seq"]})
        elif event["type"] == "PACKET_SENT" and not data.get("handoff_id"):
            # Legacy PACKET_SENT was written just after case.sent. The exact
            # current case timestamp is its anchor, rather than a second cycle.
            if not case.get("sent") or _stamp(event["ts"]) < _stamp(case["sent"]):
                anchors.append({"handoff_id": "legacy-" + str(event["seq"]), "sent_at": event["ts"], "seq": event["seq"], "legacy": True})
    if case.get("sent") and not any(a["sent_at"] == case["sent"] for a in anchors):
        anchors.append({"handoff_id": "legacy-current", "sent_at": case["sent"], "seq": 0, "legacy": True})
    anchors.sort(key=lambda a: (_stamp(a["sent_at"]), a["seq"]))
    history = []
    for index, anchor in enumerate(anchors):
        start = _stamp(anchor["sent_at"])
        end = _stamp(anchors[index + 1]["sent_at"]) if index + 1 < len(anchors) else None
        withdrawn = next((_stamp(e["ts"]) for e in events if e["type"] == "SHARING_WITHDRAWN" and _stamp(e["ts"]) >= start and (end is None or _stamp(e["ts"]) < end)), None)
        rows = []
        for event in events:
            stamp = _stamp(event["ts"])
            if stamp < start or end is not None and stamp >= end or withdrawn is not None and stamp >= withdrawn:
                continue
            eid = event["data"].get("handoff_id")
            if eid is None or eid == anchor["handoff_id"]:
                rows.append(event)
        ack = next((e for e in rows if e["type"] in ("DOCTOR_SEEN", "DOCTOR_REPLY")), None)
        reply = next((e for e in rows if e["type"] == "DOCTOR_REPLY"), None)
        history.append({"handoff_id": anchor["handoff_id"], "sent_at": anchor["sent_at"], "reason": anchor.get("reason", "legacy handoff"),
            "seconds_to_acknowledge": (_stamp(ack["ts"]) - start).total_seconds() if ack else None,
            "seconds_to_reply": (_stamp(reply["ts"]) - start).total_seconds() if reply else None,
            "doctor_tasks": sum(e["type"] == "TASK_CREATED" for e in rows), "tasks_completed": sum(e["type"] == "TASK_COMPLETED" for e in rows),
            "sharing_withdrawn": withdrawn is not None, "legacy": anchor.get("legacy", False)})
    current = next((row for row in reversed(history) if row["sent_at"] == case.get("sent")), None)
    return {"doctor_tasks": sum(e["type"] == "TASK_CREATED" for e in events),
            "tasks_completed": sum(e["type"] == "TASK_COMPLETED" for e in events),
            "seconds_to_acknowledge": current["seconds_to_acknowledge"] if current else None,
            "seconds_to_reply": current["seconds_to_reply"] if current else None,
            "current_handoff_id": current["handoff_id"] if current else None, "handoffs": history,
            "simulated_clock_minutes": case["clock_minutes"],
            "timing_note": "Wall-clock application events for simulated reviewers. Demonstration clock advances are separate; these figures do not measure clinical effort or patient outcomes."}
