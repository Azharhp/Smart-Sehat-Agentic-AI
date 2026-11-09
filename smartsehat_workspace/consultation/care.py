"""Reported care handoffs and counselling, projected from immutable events.

These statuses document human reports. They never assert external delivery,
facility verification, clinical comprehension or a change to medical advice.
"""
from __future__ import annotations
from datetime import datetime, timezone
import uuid

REFERRAL_ACTIVE = {"requested", "transport arranged", "departed", "unable"}
CALL_ACTIVE = {"requested", "planned", "unable"}
CARE_EVENTS = ("EMERGENCY_REFERRAL", "REFERRAL_PREALERT_CREATED", "REFERRAL_PLAN_UPDATED", "REFERRAL_STATUS",
               "CALL_REQUESTED", "CALL_STATUS", "GUIDANCE_RECEIPT", "WORKER_REQUEST", "WORKER_REQUEST_UPDATED", "WORKER_REQUEST_RESOLVED")


def project(events: list[dict]) -> dict:
    referrals, calls, guidance, requests = {}, {}, {}, {}
    latest = None
    for event in events:
        kind, data = event["type"], event["data"]
        stamp = {"updated": event["ts"], "revision": event["seq"]}
        if kind == "DOCTOR_REPLY":
            latest = {**data, "reply_id": data.get("reply_id") or f"legacy-reply-{event['seq']}"}
        elif kind in ("EMERGENCY_REFERRAL", "REFERRAL_PREALERT_CREATED"):
            rid = data.get("referral_id") or f"legacy-referral-{event['seq']}"
            referrals[rid] = {**data, "referral_id": rid, "status": "requested", "last_progress": "requested", **stamp}
        elif kind in ("REFERRAL_STATUS", "REFERRAL_PLAN_UPDATED") and data["referral_id"] in referrals:
            item = referrals[data["referral_id"]]
            item.update({**data, **stamp})
            if kind == "REFERRAL_STATUS" and data["status"] in ("requested", "transport arranged", "departed", "arrived", "admitted"):
                item["last_progress"] = data["status"]
        elif kind == "CALL_REQUESTED":
            call_id = data.get("call_id") or f"legacy-call-{event['seq']}"
            calls[call_id] = {**data, "call_id": call_id, "status": "requested", **stamp}
        elif kind == "CALL_STATUS" and data["call_id"] in calls:
            calls[data["call_id"]].update({**data, **stamp})
        elif kind == "GUIDANCE_RECEIPT":
            guidance[data["reply_id"]] = {**data, **stamp}
        elif kind == "WORKER_REQUEST":
            requests[data["request_id"]] = {**data, "status": "open", **stamp}
        elif kind in ("WORKER_REQUEST_UPDATED", "WORKER_REQUEST_RESOLVED") and data["request_id"] in requests:
            requests[data["request_id"]].update({**data, **stamp})
            if kind == "WORKER_REQUEST_RESOLVED":
                requests[data["request_id"]]["status"] = "resolved"
    return {"referrals": list(referrals.values()), "calls": list(calls.values()), "guidance": list(guidance.values()),
            "requests": list(requests.values()), "latest_reply": latest}


class CareWorkflow:
    """Store mixin; every mutation uses the Store's encrypted transaction log."""

    def _care(self, db, cid: str) -> dict:
        rows = db.execute("SELECT seq,ts,type,data FROM consultation_events WHERE case_id=? ORDER BY seq", (cid,)).fetchall()
        return project([{**dict(row), "data": self._decode(row["data"])} for row in rows])

    def care_status(self, cid: str) -> dict:
        with self.connect() as db:
            self._case(db, cid)
            return self._care(db, cid)

    def _care_revision(self, db, cid: str) -> int:
        placeholders = ",".join("?" for _ in CARE_EVENTS)
        return db.execute(f"SELECT COALESCE(MAX(seq),0) FROM consultation_events WHERE case_id=? AND type IN ({placeholders})", (cid, *CARE_EVENTS)).fetchone()[0]

    def _worker_request(self, db, cid: str, kind: str, source_id: str, text: str, *, refresh: bool = False) -> str:
        state = self._care(db, cid)
        prior = next((r for r in state["requests"] if r["kind"] == kind and r["source_id"] == source_id and r["status"] == "open"), None)
        if prior and prior["text"] == text and not refresh:
            return prior["request_id"]
        request_id = prior["request_id"] if prior else uuid.uuid4().hex[:12]
        self._event(db, cid, "WORKER_REQUEST_UPDATED" if prior else "WORKER_REQUEST",
                    {"request_id": request_id, "kind": kind, "source_id": source_id, "text": text})
        db.execute("UPDATE consultation_outputs SET stale=1 WHERE case_id=? AND kind='packet'", (cid,))
        case = self._case(db, cid)
        if case["sent"] and case["data"]["consent"]:
            self._start_handoff(db, cid, "worker request: " + kind)
            self._job(db, cid, "review", datetime.now(timezone.utc).isoformat(), {"reason": "worker communication request", "resume_for_request": request_id})
        else:
            self._job(db, cid, "review", datetime.now(timezone.utc).isoformat(), {"reason": "local worker communication request"})
            self._event(db, cid, "LOCAL_REVIEW_REQUIRED", {"request_id": request_id, "reason": "Remote sharing is not active; this request remains local."}, "rule")
        return request_id

    def _register_referral(self, db, cid: str, event_type: str, data: dict) -> str:
        facility = data["facility"]
        prior = next((r for r in reversed(self._care(db, cid)["referrals"]) if r["facility"].get("id", r["facility"]["name"]) == facility.get("id", facility["name"])), None)
        rid = prior["referral_id"] if prior else uuid.uuid4().hex[:12]
        self._event(db, cid, "REFERRAL_PLAN_UPDATED" if prior else event_type, {**data, "referral_id": rid}, "worker" if event_type == "EMERGENCY_REFERRAL" else "doctor (simulated)")
        return rid

    def referral_status(self, cid: str, referral_id: str, status: str, notes: str = "", *, correction: bool = False) -> None:
        self._referral_status(cid, referral_id, status, notes, doctor=False, correction=correction)

    def cancel_referral(self, cid: str, referral_id: str, reason: str) -> None:
        self._referral_status(cid, referral_id, "cancelled", reason, doctor=True)

    def reopen_referral(self, cid: str, referral_id: str, reason: str) -> None:
        self._referral_status(cid, referral_id, "requested", reason, doctor=True)

    def _referral_status(self, cid, referral_id, status, notes, *, doctor, correction=False):
        options = ("requested", "cancelled") if doctor else ("transport arranged", "departed", "arrived", "admitted", "unable")
        if status not in options or type(correction) is not bool:
            raise ValueError("Choose a supported referral status.")
        if (status in ("unable", "requested", "cancelled") or correction) and not notes.strip():
            raise ValueError("Record the reason or correction for this referral update.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            if doctor and (not case["sent"] or not case["data"]["consent"]):
                raise ValueError("An active consented handoff is required for a doctor referral change.")
            referral = next((r for r in self._care(db, cid)["referrals"] if r["referral_id"] == referral_id), None)
            if not referral:
                raise ValueError("Referral not found in this case.")
            if not doctor and referral["status"] == "cancelled":
                raise ValueError("The doctor cancelled this referral. Request clarification before restarting it.")
            ranks = {"requested": 0, "transport arranged": 1, "departed": 2, "arrived": 3, "admitted": 4}
            if not doctor and not correction and (ranks.get(status, 5) < ranks.get(referral["last_progress"], 0) or referral["status"] in ("arrived", "admitted") and status == "unable"):
                raise ValueError("This reverses recorded progress. Mark an explicit correction and provide the reason.")
            if referral["status"] == status and referral.get("notes", "") == notes.strip():
                return
            self._event(db, cid, "REFERRAL_STATUS", {"referral_id": referral_id, "status": status, "notes": notes.strip(), "correction": correction, "before": referral["status"], "reported": True}, "doctor (simulated)" if doctor else "worker")
            if status == "unable":
                self._worker_request(db, cid, "referral barrier", referral_id, notes.strip())

    def _request_call(self, db, cid: str, reply_id: str) -> str:
        existing = next((c for c in self._care(db, cid)["calls"] if c["status"] in CALL_ACTIVE), None)
        if existing:
            return existing["call_id"]
        call_id = uuid.uuid4().hex[:12]
        self._event(db, cid, "CALL_REQUESTED", {"call_id": call_id, "reply_id": reply_id, "simulated": True}, "doctor (simulated)")
        return call_id

    def call_status(self, cid: str, call_id: str, status: str, notes: str = "") -> None:
        self._call_status(cid, call_id, status, notes, doctor=False)

    def cancel_call(self, cid: str, call_id: str, reason: str) -> None:
        self._call_status(cid, call_id, "cancelled", reason, doctor=True)

    def _call_status(self, cid, call_id, status, notes, *, doctor):
        if status not in (("cancelled",) if doctor else ("planned", "completed", "unable")):
            raise ValueError("Choose a supported call status.")
        if status in ("unable", "cancelled") and not notes.strip():
            raise ValueError("Record why the call could not be completed or was cancelled.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            if doctor and (not case["sent"] or not case["data"]["consent"]):
                raise ValueError("An active consented handoff is required.")
            call = next((c for c in self._care(db, cid)["calls"] if c["call_id"] == call_id), None)
            if not call:
                raise ValueError("Call request not found in this case.")
            if call["status"] in ("completed", "cancelled"):
                if call["status"] == status:
                    return
                raise ValueError("This call request is already closed. The doctor can create a new request.")
            if call["status"] == status and call.get("notes", "") == notes.strip():
                return
            self._event(db, cid, "CALL_STATUS", {"call_id": call_id, "status": status, "notes": notes.strip(), "reported": True}, "doctor (simulated)" if doctor else "worker")
            if status == "unable":
                self._worker_request(db, cid, "call barrier", call_id, notes.strip())

    def guidance_receipt(self, cid: str, reply_id: str, understood: bool | None, channel: str, notes: str = "", *, worker_confirmed: bool = False) -> None:
        if worker_confirmed is not True or understood is not None and type(understood) is not bool:
            raise ValueError("The worker must confirm the counselling and record yes, no or unknown understanding.")
        if channel not in ("spoken read-back", "recorded audio", "printed card"):
            raise ValueError("Choose how the guidance was explained.")
        if understood is not True and not notes.strip():
            raise ValueError("Record what needs clarification or why understanding is unknown.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            care = self._care(db, cid)
            reply = care["latest_reply"]
            if not reply or reply["reply_id"] != reply_id:
                raise ValueError("The doctor updated the guidance. Open the current response before confirming.")
            if understood is True and (reply.get("fact_ids") != [f["id"] for f in self._facts(db, cid)] or reply.get("media_ids", []) != self._media_ids(db, cid)):
                raise ValueError("This guidance predates a case update. Ask the doctor to review the current case.")
            if channel == "recorded audio" and not (reply.get("guidance_audio_path") and reply.get("guidance_audio_confirmed") is True):
                raise ValueError("This response has no doctor-approved recording. Select the method actually used.")
            data = {"reply_id": reply_id, "understood": understood, "channel": channel, "notes": notes.strip(), "worker_confirmed": True, "reported": True}
            previous = next((g for g in care["guidance"] if g["reply_id"] == reply_id), None)
            if previous and all(previous.get(key) == value for key, value in data.items()):
                return
            self._event(db, cid, "GUIDANCE_RECEIPT", data)
            if understood is not True:
                self._worker_request(db, cid, "guidance clarification", reply_id, notes.strip())

    def cancel_task(self, cid: str, task_id: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("Record the doctor's reason for cancelling the task.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            if not case["sent"] or not case["data"]["consent"]:
                raise ValueError("An active consented handoff is required.")
            task = next((t for t in self.tasks(cid) if t["task_id"] == task_id and not t["done"]), None)
            if not task:
                raise ValueError("This task is already complete or cancelled.")
            if task.get("order_id"):
                self._event(db, cid, "TEST_ORDER_CANCELLED", {"order_id": task["order_id"], "reason": reason.strip()}, "doctor (simulated)")
            self._event(db, cid, "TASK_CANCELLED", {"task_id": task_id, "reason": reason.strip()}, "doctor (simulated)")

    def _resolve_worker_requests(self, db, cid: str, request_ids: list[str], reply_id: str) -> None:
        open_ids = {r["request_id"] for r in self._care(db, cid)["requests"] if r["status"] == "open"}
        if not isinstance(request_ids, list) or any(not isinstance(rid, str) or rid not in open_ids for rid in request_ids):
            raise ValueError("Review the current worker requests before marking them answered.")
        for request_id in dict.fromkeys(request_ids):
            self._event(db, cid, "WORKER_REQUEST_RESOLVED", {"request_id": request_id, "reply_id": reply_id}, "doctor (simulated)")
