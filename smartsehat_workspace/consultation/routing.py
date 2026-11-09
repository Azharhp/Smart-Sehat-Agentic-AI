"""Audited handoffs and available-reviewer selection; all delivery is simulated."""
from __future__ import annotations
from datetime import datetime, timezone
import math
import uuid

from .clinical import Age, config
from .medicines import resources


def select_reviewer(case: dict, age: Age, reviewers: list[dict], *, preferred: str | None = None,
                    excluded: set[str] | None = None) -> dict:
    group = "maternal" if case["data"]["pregnant"] else age.pathway
    matches = [d for d in reviewers if group in d["groups"] and case["data"]["language"] in d["languages"]
               and d.get("availability", "available") == "available" and d["id"] not in (excluded or set())]
    matches.sort(key=lambda d: (d["id"] != preferred, len(d["groups"]), d["id"]))
    return {"group": group, "language": case["data"]["language"], "reviewer": matches[0] if matches else None,
            "eligible_reviewers": len(matches), "simulated": True}


def escalation_delays(level: str) -> list[float]:
    policy = config()["routing"]
    delays = policy.get("escalation_by_urgency", {}).get(level, [policy["escalation_minutes"] * n for n in (1, 2, 3)])
    if not isinstance(delays, list) or len(delays) != 3 or any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in delays) or delays != sorted(set(delays)):
        raise ValueError("Routing requires three increasing escalation delays in minutes.")
    return delays


class RoutingWorkflow:
    def _handoff(self, db, cid: str) -> dict:
        case = self._case(db, cid)
        row = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='HANDOFF_STARTED' ORDER BY seq DESC LIMIT 1", (cid,)).fetchone()
        data = self._decode(row["data"]) if row else {}
        if case["sent"] and data.get("sent_at") == case["sent"]:
            return data
        return {"handoff_id": None, "sent_at": case["sent"], "clock_minutes_at_start": case["clock_minutes"], "legacy": True}

    def handoff(self, cid: str) -> dict:
        with self.connect() as db:
            return self._handoff(db, cid)

    def routing_status(self, cid: str) -> dict:
        with self.connect() as db:
            self._case(db, cid)
            return self._latest_route(db, cid)

    def _reviewers(self, db) -> list[dict]:
        states = {row["id"]: {**self._decode(row["data"]), "updated": row["updated"]} for row in db.execute("SELECT * FROM consultation_reviewers")}
        return [{**d, "availability": "available", "notes": "", "updated": "configured", **states.get(d["id"], {})} for d in resources()["doctors"]]

    def reviewers(self) -> list[dict]:
        with self.connect() as db:
            return self._reviewers(db)

    def reviewer_availability(self, reviewer_id: str, status: str, notes: str = "") -> None:
        if status not in ("available", "busy", "offline") or reviewer_id not in {d["id"] for d in resources()["doctors"]}:
            raise ValueError("Choose a configured simulated reviewer and availability status.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            before = next(d for d in self._reviewers(db) if d["id"] == reviewer_id)
            if before["availability"] == status and before["notes"] == notes.strip():
                return
            stamp = datetime.now(timezone.utc).isoformat()
            data = {"availability": status, "notes": notes.strip(), "simulated": True}
            db.execute("INSERT INTO consultation_reviewers(id,data,updated) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated=excluded.updated", (reviewer_id, self._encode(data), stamp))
            db.execute("INSERT INTO consultation_roster_events(ts,data) VALUES(?,?)", (stamp, self._encode({"reviewer_id": reviewer_id, "before": before["availability"], **data})))
            for row in db.execute("SELECT id FROM consultation_cases WHERE sent IS NOT NULL AND stage!='CLOSED'").fetchall():
                cid = row["id"]
                case = self._case(db, cid)
                if case["data"]["consent"] and not self._handoff_acknowledged(db, cid):
                    self._route_case(db, cid, "reviewer availability changed")

    def _latest_route(self, db, cid: str) -> dict:
        row = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='ROUTED' ORDER BY seq DESC LIMIT 1", (cid,)).fetchone()
        return self._decode(row["data"]) if row else {}

    def _route_case(self, db, cid: str, reason: str, *, alternate: bool = False) -> dict:
        case, handoff = self._case(db, cid), self._handoff(db, cid)
        if not case["sent"] or not case["data"]["consent"]:
            return {}
        prior = self._latest_route(db, cid)
        previous = (prior.get("reviewer") or {}).get("id")
        excluded = {previous} if alternate and previous else set()
        age = next(f["value"] for f in self._facts(db, cid) if f["key"] == "age")
        route = select_reviewer(case, Age.from_record(age), self._reviewers(db), preferred=None if alternate else previous, excluded=excluded)
        route.update({"handoff_id": handoff["handoff_id"], "sent_revision": case["sent"], "reason": reason})
        if alternate and route["reviewer"] is None:
            self._event(db, cid, "ROUTING_UNAVAILABLE", {**route, "previous_reviewer_id": previous}, "rule")
            return prior
        if (prior.get("handoff_id"), (prior.get("reviewer") or {}).get("id"), prior.get("group"), prior.get("language")) != (route["handoff_id"], (route["reviewer"] or {}).get("id"), route["group"], route["language"]):
            self._event(db, cid, "ROUTED", route, "rule")
        return route

    def _handoff_acknowledged(self, db, cid: str, handoff: dict | None = None) -> bool:
        handoff = handoff or self._handoff(db, cid)
        if not handoff["sent_at"]:
            return False
        rows = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type IN ('DOCTOR_SEEN','DOCTOR_REPLY') AND ts>=?", (cid, handoff["sent_at"])).fetchall()
        return any(not handoff["handoff_id"] or self._decode(row["data"]).get("handoff_id") == handoff["handoff_id"] for row in rows)

    def _start_handoff(self, db, cid: str, reason: str) -> dict:
        case = self._case(db, cid)
        if not case["data"]["consent"]:
            raise ValueError("Sharing consent is required for a remote handoff.")
        stamp = datetime.now(timezone.utc).isoformat()
        handoff = {"handoff_id": uuid.uuid4().hex[:16], "sent_at": stamp, "clock_minutes_at_start": case["clock_minutes"], "reason": reason, "simulated": True}
        stage = "EMERGENCY" if self._output(db, cid, "urgency")["level"] == "RED" else "SENT"
        db.execute("UPDATE consultation_cases SET sent=?,stage=? WHERE id=?", (stamp, stage, cid))
        self._event(db, cid, "HANDOFF_STARTED", handoff)
        self._route_case(db, cid, reason)
        self._schedule_escalation(db, cid)
        return handoff
