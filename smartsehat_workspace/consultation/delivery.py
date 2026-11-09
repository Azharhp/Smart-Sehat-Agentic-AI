"""Explicit, consented handoff to an administrator-configured HTTPS gateway.

The gateway handles actual SMS/WhatsApp/call services. Acknowledging an HTTP
request is not proof that a doctor, facility or patient received anything.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from urllib.parse import urlsplit
import uuid


def gateway_config() -> tuple[str, str]:
    url = os.environ.get("CONSULTATION_DELIVERY_URL", "").strip()
    token = os.environ.get("CONSULTATION_DELIVERY_TOKEN", "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query or not token:
        raise ValueError("Configure an HTTPS delivery gateway and token on the server. Keep credentials out of the URL.")
    return url, token


class DeliveryWorkflow:
    def _delivery_source(self, db, cid: str, kind: str) -> tuple[dict, dict]:
        case = self._case(db, cid)
        if not case["data"]["consent"] or not case["sent"]:
            raise ValueError("An actively shared, consented case is required for external delivery.")
        facts = self._facts(db, cid)
        source = {"context_revision": self._context_revision(db, cid), "fact_ids": [f["id"] for f in facts],
                  "media_ids": self._media_ids(db, cid), "care_revision": self._care_revision(db, cid),
                  "handoff_id": self._handoff(db, cid)["handoff_id"]}
        from .epidemiology import catalogue_hash
        source["fever_catalogue_hash"] = catalogue_hash()
        if kind == "case_packet":
            packet = self._output(db, cid, "packet")
            if not packet or packet["verification"]["status"] != "VERIFIED" or packet["fact_ids"] != source["fact_ids"]:
                raise ValueError("Regenerate and confirm the current case packet before delivery.")
            sent = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='PACKET_SENT' ORDER BY seq DESC LIMIT 1", (cid,)).fetchone()
            if not sent or self._decode(sent["data"]).get("version") != packet["version"] or self._decode(sent["data"]).get("handoff_id") != source["handoff_id"]:
                raise ValueError("Complete read-back and send this exact packet before external delivery.")
            source["packet_version"] = packet["version"]
            payload = {"compact": packet["text"], "full_sbar": packet["full_text"], "source_lines": packet["source_lines"],
                       "urgency": packet["urgency"], "media_included": False, "media_note": "Request the portable package for original attachments."}
        elif kind == "emergency_prealert":
            urgency = self._output(db, cid, "urgency")
            referral = next((row for row in reversed(self._care(db, cid)["referrals"]) if row["status"] in ("requested", "transport arranged", "departed", "unable")), None)
            if urgency["level"] != "RED" or not referral:
                raise ValueError("Create a current RED referral before preparing its pre-alert.")
            values = {f["key"]: f["value"] for f in facts}
            payload = {"urgency": urgency, "age": values["age"], "sex": case["data"]["sex"], "village": case["data"]["village"],
                       "resp_rate": values.get("resp_rate"), "spo2": values.get("spo2"), "facility": referral["facility"]}
        elif kind == "patient_guidance":
            reply = self._care(db, cid)["latest_reply"]
            if not reply or reply.get("fact_ids") != source["fact_ids"] or reply.get("media_ids", []) != source["media_ids"] or reply.get("fever_catalogue_hash") != source["fever_catalogue_hash"]:
                raise ValueError("The doctor must review the current case before its guidance is delivered.")
            source["reply_id"] = reply["reply_id"]
            fields = ("advice", "local_language", "prescription", "medication_schedule", "feeding", "ors", "counselling_script", "return_if", "referral", "local_tests", "lab_tests", "followup_due", "status")
            payload = {key: reply.get(key) for key in fields}
            payload["audio_included"] = False
        else:
            raise ValueError("Choose case_packet, emergency_prealert or patient_guidance.")
        return source, {"format": "smartsehat-gateway-1", "case_id": cid, "kind": kind, "research_prototype": True,
                        "clinical_identity_verified": False, "source": source, "content": payload}

    def _deliveries(self, db, cid: str) -> list[dict]:
        result = {}
        for row in db.execute("SELECT seq,ts,type,data FROM consultation_events WHERE case_id=? AND type IN ('DELIVERY_QUEUED','DELIVERY_ATTEMPT','DELIVERY_RESULT','DELIVERY_CANCELLED') ORDER BY seq", (cid,)):
            data = self._decode(row["data"])
            identity = data["delivery_id"]
            result[identity] = {**result.get(identity, {}), **data, "updated": row["ts"], "revision": row["seq"]}
        return list(result.values())

    def deliveries(self, cid: str) -> list[dict]:
        with self.connect() as db:
            self._case(db, cid)
            return self._deliveries(db, cid)

    def queue_delivery(self, cid: str, kind: str, recipient: str, channel: str) -> str:
        recipient = recipient.strip()
        if channel not in ("care_team", "sms", "whatsapp", "call") or not 2 <= len(recipient) <= 200 or any(ord(c) < 32 for c in recipient):
            raise ValueError("Record a recipient identifier and a supported gateway channel.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            source, payload = self._delivery_source(db, cid, kind)
            payload.update({"recipient": recipient, "channel": channel})
            identity = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            prior = next((row for row in self._deliveries(db, cid) if row["delivery_id"] == identity), None)
            if not prior:
                self._event(db, cid, "DELIVERY_QUEUED", {"delivery_id": identity, "kind": kind, "recipient": recipient,
                    "channel": channel, "source": source, "payload": payload, "status": "queued"})
            return identity

    def cancel_delivery(self, cid: str, identity: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = next((r for r in self._deliveries(db, cid) if r["delivery_id"] == identity), None)
            if not row or row["status"] == "gateway accepted":
                raise ValueError("An accepted request cannot be recalled. Contact the recipient through the agreed channel.")
            if row["status"] == "sending":
                raise ValueError("A request is in flight; its outcome may already be external. Wait for its result.")
            if row["status"] != "cancelled":
                self._event(db, cid, "DELIVERY_CANCELLED", {"delivery_id": identity, "status": "cancelled"})

    def dispatch_delivery(self, cid: str, identity: str, confirmed: bool = False) -> dict:
        if confirmed is not True:
            raise ValueError("Explicitly confirm the recipient and exact payload before external sending.")
        url, token = gateway_config()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = next((r for r in self._deliveries(db, cid) if r["delivery_id"] == identity), None)
            if not row or row["status"] == "cancelled":
                raise ValueError("Delivery is missing or cancelled.")
            source, _ = self._delivery_source(db, cid, row["kind"])
            if source != row["source"]:
                raise ValueError("The case changed. Review and prepare a new delivery; stale content was not sent.")
            if row["status"] == "gateway accepted":
                return {"status": row["status"], "receipt_id": row.get("receipt_id")}
            if row["status"] == "sending" and datetime.fromisoformat(row["updated"]) > datetime.now(timezone.utc) - timedelta(minutes=2):
                raise ValueError("This request is already in flight. Wait before retrying the same idempotency key.")
            attempt = uuid.uuid4().hex
            self._event(db, cid, "DELIVERY_ATTEMPT", {"delivery_id": identity, "attempt_id": attempt, "status": "sending"})
        # Do not hold a database lock across network I/O. Consent is checked
        # before dispatch; an already-started external request cannot be recalled.
        import httpx
        result = {"delivery_id": identity, "attempt_id": attempt, "status": "outcome unknown", "note": "Confirm gateway status before retrying; reuse the same idempotency key."}
        try:
            with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
                response = client.post(url, headers={"Authorization": "Bearer " + token, "Idempotency-Key": identity}, json={**row["payload"], "delivery_id": identity})
            result["http_status"] = response.status_code
            if 200 <= response.status_code < 300:
                # Require a bounded explicit receipt; HTTP success alone is not delivery.
                if len(response.content) <= 16_384:
                    receipt = response.json()
                    if receipt.get("accepted") is True and isinstance(receipt.get("receipt_id"), str) and 1 <= len(receipt["receipt_id"]) <= 200:
                        result.update({"status": "gateway accepted", "receipt_id": receipt["receipt_id"], "note": "Gateway accepted this request. Recipient receipt/read/care is not verified."})
            elif 400 <= response.status_code < 500 and response.status_code not in (408, 409, 429):
                result.update({"status": "gateway rejected", "note": "Gateway rejected the request. Correct server configuration; no raw provider message is stored."})
        except Exception:
            pass  # Never expose response bodies, credentials, URLs or patient data in errors.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result["consent_current"] = bool(self._case(db, cid)["data"]["consent"])
            self._event(db, cid, "DELIVERY_RESULT", result, "gateway adapter")
        return result
