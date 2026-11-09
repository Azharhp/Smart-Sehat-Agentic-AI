"""SQLite event-sourced consultation store. Facts and audit events are immutable.

Uses separate tables so existing Dawa/District data is preserved.
Each public mutation commits its facts, state, derived-output invalidation and audit together.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from typing import Any

from .clinical import Age, ROOT, evaluate, validate_measurement, danger_questions, config
from .medicines import reconcile, resources
from .verify import verify, fact_sentence
from .ai import call_json, Reasoning
from .care import CareWorkflow, REFERRAL_ACTIVE, CALL_ACTIVE, CARE_EVENTS
from .routing import RoutingWorkflow, escalation_delays
from .readback import ReadbackWorkflow
from .content_review import ContentReviewWorkflow
from .delivery import DeliveryWorkflow
from .safety_capture import SafetyCaptureWorkflow
from .voice import VoiceWorkflow
from .speech_evaluation import SpeechEvaluationWorkflow

STAGES = ["IDENTIFIED", "DANGER_SCREEN", "EMERGENCY", "STORY", "QUESTIONS", "MEASURE", "MEDS", "REVIEW", "READBACK", "SENT", "DOCTOR_REPLIED", "PATIENT_CARD", "FOLLOWUP", "CLOSED"]
WORKER_MEDIA = ("PHOTO_ATTACHED", "AUDIO_ATTACHED", "COUGH_AUDIO_ATTACHED", "MEDICINE_IMAGE_READ")
CONTEXT_EVENTS = ("CASE_CREATED", "DEMOGRAPHICS_CORRECTED", "CONSENT_RECORDED")
SCHEMA = """
CREATE TABLE IF NOT EXISTS consultation_cases(id TEXT PRIMARY KEY, data TEXT NOT NULL, stage TEXT NOT NULL, created TEXT NOT NULL, sent TEXT, clock_minutes INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS consultation_facts(seq INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES consultation_cases(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS consultation_outputs(seq INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES consultation_cases(id), kind TEXT NOT NULL, data TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS consultation_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES consultation_cases(id), ts TEXT NOT NULL, actor TEXT NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL, payload_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS consultation_facts_case ON consultation_facts(case_id);
CREATE INDEX IF NOT EXISTS consultation_outputs_case ON consultation_outputs(case_id);
CREATE INDEX IF NOT EXISTS consultation_events_case ON consultation_events(case_id);
CREATE TABLE IF NOT EXISTS consultation_jobs(id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES consultation_cases(id), kind TEXT NOT NULL, due TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS consultation_jobs_due ON consultation_jobs(status,due);
CREATE TABLE IF NOT EXISTS consultation_drafts(case_id TEXT NOT NULL REFERENCES consultation_cases(id), section TEXT NOT NULL, data TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(case_id,section));
CREATE TABLE IF NOT EXISTS consultation_reviewers(id TEXT PRIMARY KEY, data TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS consultation_roster_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS consultation_content_reviews(seq INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS consultation_speech_evaluations(seq INTEGER PRIMARY KEY AUTOINCREMENT, dataset_hash TEXT NOT NULL UNIQUE, data TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS consultation_speech_evaluations_no_update BEFORE UPDATE ON consultation_speech_evaluations BEGIN SELECT RAISE(ABORT, 'Speech evaluations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS consultation_speech_evaluations_no_delete BEFORE DELETE ON consultation_speech_evaluations BEGIN SELECT RAISE(ABORT, 'Speech evaluations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS consultation_content_reviews_no_update BEFORE UPDATE ON consultation_content_reviews BEGIN SELECT RAISE(ABORT, 'Content reviews are append-only'); END;
CREATE TRIGGER IF NOT EXISTS consultation_content_reviews_no_delete BEFORE DELETE ON consultation_content_reviews BEGIN SELECT RAISE(ABORT, 'Content reviews are append-only'); END;
CREATE TRIGGER IF NOT EXISTS consultation_facts_no_update BEFORE UPDATE ON consultation_facts BEGIN SELECT RAISE(ABORT, 'Facts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS consultation_facts_no_delete BEFORE DELETE ON consultation_facts BEGIN SELECT RAISE(ABORT, 'Facts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS consultation_events_no_update BEFORE UPDATE ON consultation_events BEGIN SELECT RAISE(ABORT, 'Audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS consultation_events_no_delete BEFORE DELETE ON consultation_events BEGIN SELECT RAISE(ABORT, 'Audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS consultation_roster_no_update BEFORE UPDATE ON consultation_roster_events BEGIN SELECT RAISE(ABORT, 'Roster audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS consultation_roster_no_delete BEFORE DELETE ON consultation_roster_events BEGIN SELECT RAISE(ABORT, 'Roster audit is append-only'); END;
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class Store(CareWorkflow, RoutingWorkflow, ReadbackWorkflow, ContentReviewWorkflow, DeliveryWorkflow, SafetyCaptureWorkflow, VoiceWorkflow, SpeechEvaluationWorkflow):
    def __init__(self, path: str | Path | None = None):
        self.path = str(path or os.environ.get("CONSULTATION_DB", str(ROOT / "consultation.db")))
        from .privacy import Cipher
        self.cipher = Cipher(self.path)
        with self.connect() as db:
            db.executescript(SCHEMA)
            self._encrypt_existing(db)

    def _encode(self, data: Any) -> str:
        return self.cipher.encode(dump(data))

    def _decode(self, data: str) -> Any:
        return json.loads(self.cipher.decode(data))

    def _encrypt_existing(self, db) -> None:
        # Storage-only migration preserves every fact/event ID and plaintext
        # payload hash. It never changes the logical content of audit records.
        from .privacy import PREFIX
        tables = ("consultation_cases", "consultation_facts", "consultation_outputs", "consultation_events", "consultation_jobs", "consultation_drafts", "consultation_reviewers", "consultation_roster_events", "consultation_content_reviews", "consultation_speech_evaluations")
        db.execute("BEGIN IMMEDIATE")
        pending = {table: db.execute(f"SELECT rowid AS storage_row,data FROM {table} WHERE data NOT LIKE ?", (PREFIX + "%",)).fetchall() for table in tables}
        if not any(pending.values()):
            return
        triggers = db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'consultation_%'").fetchall()
        for trigger in triggers:
            db.execute('DROP TRIGGER "' + trigger["name"] + '"')
        for table, rows in pending.items():
            for row in rows:
                db.execute(f"UPDATE {table} SET data=? WHERE rowid=?", (self.cipher.encode(row["data"]), row["storage_row"]))
        for trigger in triggers:
            db.execute(trigger["sql"])

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _event(self, db, cid: str, event: str, data: dict, actor: str = "worker") -> None:
        if event in ("DOCTOR_SEEN", "DOCTOR_REPLY", "TASK_CREATED", "TASK_COMPLETED", "PACKET_SENT", "CASE_UPDATE_AVAILABLE"):
            handoff = self._handoff(db, cid)
            data = {**data, "handoff_id": handoff["handoff_id"], "clock_minutes": self._case(db, cid)["clock_minutes"]}
        encoded = self._encode(data)
        db.execute("INSERT INTO consultation_events(case_id,ts,actor,type,data,payload_hash) VALUES(?,?,?,?,?,?)",
                   (cid, now(), actor, event, encoded, hashlib.sha256(dump(data).encode()).hexdigest()))

    def _case(self, db, cid: str) -> dict:
        row = db.execute("SELECT * FROM consultation_cases WHERE id=?", (cid,)).fetchone()
        if not row:
            raise ValueError("Case not found.")
        return {**dict(row), "data": self._decode(row["data"])}

    def case(self, cid: str) -> dict:
        with self.connect() as db:
            return self._case(db, cid)

    def list_cases(self) -> list[dict]:
        with self.connect() as db:
            return [{**dict(row), "data": self._decode(row["data"])} for row in db.execute("SELECT * FROM consultation_cases ORDER BY created DESC")]

    def dashboard_cases(self) -> list[dict]:
        """One current summary per encounter, without loading its full history."""
        with self.connect() as db:
            db.execute("BEGIN")
            rows = db.execute("""
                SELECT c.*, u.data AS urgency_data, r.data AS reply_data,
                       f.data AS followup_data
                FROM consultation_cases c
                LEFT JOIN consultation_outputs u ON u.seq = (
                    SELECT MAX(seq) FROM consultation_outputs WHERE case_id=c.id AND kind='urgency')
                LEFT JOIN consultation_events r ON r.seq = (
                    SELECT MAX(seq) FROM consultation_events WHERE case_id=c.id AND type='DOCTOR_REPLY')
                LEFT JOIN consultation_events f ON f.seq = (
                    SELECT MAX(seq) FROM consultation_events WHERE case_id=c.id AND type='FOLLOWUP')
                ORDER BY c.created DESC
            """).fetchall()
            result = []
            for row in rows:
                data = self._decode(row["data"])
                urgency = self._decode(row["urgency_data"]) if row["urgency_data"] else {}
                reply = self._decode(row["reply_data"]) if row["reply_data"] else {}
                followup = self._decode(row["followup_data"]) if row["followup_data"] else {}
                result.append({"case_id": row["id"], "name": data.get("name", row["id"]),
                    "created": row["created"], "stage": row["stage"], "sent": row["sent"],
                    "village": data.get("village", ""), "synthetic": data.get("synthetic", False),
                    "urgency": urgency.get("level", "UNKNOWN"), "diagnosis": reply.get("diagnosis", ""),
                    "doctor_replied": bool(reply), "outcome": followup.get("outcome", ""),
                    "referral": reply.get("referral", "")})
            return result

    def worker_worklist(self) -> list[dict]:
        from .worklist import actions, current_time
        rows = []
        with self.connect() as db:
            db.execute("BEGIN")
            at = current_time()
            for row in db.execute("SELECT * FROM consultation_cases WHERE stage!='CLOSED'").fetchall():
                case = {**dict(row), "data": self._decode(row["data"])}
                events = [{**dict(e), "data": self._decode(e["data"])} for e in db.execute("SELECT seq,ts,type,data FROM consultation_events WHERE case_id=? ORDER BY seq", (case["id"],))]
                job = db.execute("SELECT due,status FROM consultation_jobs WHERE case_id=? AND kind='followup'", (case["id"],)).fetchone()
                item = actions(case, events, self._output(db, case["id"], "urgency") or {}, dict(job) if job else None, at)
                if item:
                    rows.append(item)
        return sorted(rows, key=lambda item: item["priority"])

    def _facts(self, db, cid: str, confirmed: bool = True) -> list[dict]:
        rows = db.execute("SELECT data FROM consultation_facts WHERE case_id=? ORDER BY seq", (cid,)).fetchall()
        facts = [self._decode(row["data"]) for row in rows]
        # A proposal cannot supersede confirmed evidence until explicitly confirmed.
        superseded = {f["supersedes"] for f in facts if f.get("confirmed") and f.get("supersedes")}
        return [f for f in facts if f["id"] not in superseded and (f["confirmed"] if confirmed else True)]

    def facts(self, cid: str, confirmed: bool = True) -> list[dict]:
        with self.connect() as db:
            return self._facts(db, cid, confirmed)

    def age(self, cid: str) -> Age:
        fact = next(f for f in self.facts(cid) if f["key"] == "age")
        return Age.from_record(fact["value"])

    def _append(self, db, cid: str, key: str, value: Any, source: str = "worker", confirmed: bool = True,
                unit: str | None = None, supersedes: str | None = None, reason_missing: str | None = None,
                confidence: float | None = None, quote: str | None = None, evidence: dict | None = None) -> dict:
        self._case(db, cid)
        if evidence is not None:
            evidence = self._validate_story_evidence(db, cid, evidence, quote, key)
        if source not in ("patient", "worker", "device", "ai_proposed", "doctor", "rule"):
            raise ValueError("Unknown fact source.")
        if confirmed:
            if key == "pregnant":
                if type(value) is not bool:
                    raise ValueError("Pregnancy status must be confirmed yes or no.")
                case = self._case(db, cid)
                case["data"]["pregnant"] = value
                db.execute("UPDATE consultation_cases SET data=? WHERE id=?", (self._encode(case["data"]), cid))
            if value is None and (not isinstance(reason_missing, str) or not reason_missing.strip()):
                raise ValueError("A missing fact requires an explicit reason.")
            if isinstance(reason_missing, str):
                reason_missing = reason_missing.strip()
            if key in config()["measurements"] and value is not None:
                age_value = next(f["value"] for f in self._facts(db, cid) if f["key"] == "age")
                check = validate_measurement(key, value, Age.from_input(age_value["value"], age_value["unit"]), unit)
                if not check["ok"]:
                    raise ValueError(check["error"])
                value, unit = check["value"], check["unit"]
            from .questions import QUESTIONS
            from .poc import tests as poc_tests, validate as validate_test
            if key in poc_tests() and value is not None:
                if not isinstance(value, dict):
                    raise ValueError("Use the typed point-of-care result form, including its unit.")
                value = validate_test(key, value.get("result", value.get("value")), value.get("unit", ""))
            bool_keys = {q["asks_key"] for q in QUESTIONS if q["kind"] == "bool"} | {q["key"] for group in config()["danger_signs"].values() for q in group} | {"followup_worse", "followup_danger"}
            if key in bool_keys and value is not None and type(value) is not bool:
                raise ValueError("A yes/no fact must be true, false or explicitly unknown.")
            if value is not None and key in ("cough_days", "fever_days") and (type(value) not in (int, float) or value < 0):
                raise ValueError("Duration must be a non-negative number of days.")
            question = next((q for q in QUESTIONS if q["asks_key"] == key), None)
            if question and question["kind"] == "choice" and value == "unknown":
                value, reason_missing = None, reason_missing or "Unknown to patient/worker"
            if question and value is not None:
                if question["kind"] == "choice" and value not in question["choices"]:
                    raise ValueError("Choose an answer from the question's approved options.")
                if question["kind"] == "number" and (type(value) not in (int, float) or value < 0):
                    raise ValueError("Enter a non-negative number.")
                if question["kind"] == "text" and not isinstance(value, str):
                    raise ValueError("Enter text for this question.")
            if key == "age":
                value = Age.from_record(value).json()
            if key == "fever_context" and value is not None:
                from .epidemiology import FeverContext
                value = FeverContext.model_validate(value).model_dump(mode="json")
            if key == "reported_doses" and value is not None:
                from .reported_doses import ReportedDose, record_hash
                if not isinstance(value, list) or len(value) > 40:
                    raise ValueError("Use a list of up to 40 confirmed reported medicine amounts.")
                records = next((f["value"] for f in self._facts(db, cid) if f["key"] == "medicine_records"), [])
                by_hash = {record_hash(record): record for record in records or []}
                value = [ReportedDose.model_validate(row).model_dump() for row in value]
                if len({row["record_hash"] for row in value}) != len(value) or any(row["record_hash"] not in by_hash or row["medicine"] != by_hash[row["record_hash"]]["name"] or sum(record_hash(record) == row["record_hash"] for record in records) != 1 for row in value):
                    raise ValueError("Reported amounts must match unique current confirmed medicine rows.")
            if key in ("medicine_records", "allergy_records") and value is not None:
                from .ai import MedicineRecord, AllergyRecord
                if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                    raise ValueError("Use the structured medicine or allergy record fields.")
                schema = MedicineRecord if key == "medicine_records" else AllergyRecord
                value = [schema.model_validate(row).model_dump() for row in value]
            if key == "medicines" and value is not None:
                if not isinstance(value, list) or any(not isinstance(name, str) or not name.strip() for name in value):
                    raise ValueError("Medicine names must be a list of non-empty names.")
        old = next((f for f in self._facts(db, cid) if f["key"] == key), None)
        if supersedes and (not old or old["id"] != supersedes):
            raise ValueError("This fact has changed. Reload before correcting it.")
        next_id = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM consultation_facts").fetchone()[0]
        fact = {"id": f"F{next_id}", "case_id": cid, "key": key, "value": value, "unit": unit,
                "source": source, "confirmed": confirmed, "negated": value is False, "confidence": confidence,
                "ts": now(), "supersedes": (supersedes or (old["id"] if old else None)) if confirmed else None,
                "reason_missing": reason_missing, "quote": quote}
        if evidence is not None:
            fact["evidence"] = evidence
        db.execute("INSERT INTO consultation_facts(seq,case_id,data) VALUES(?,?,?)", (next_id, cid, self._encode(fact)))
        if confirmed:
            # Conservatively invalidate all dependent case outputs; no stale packet can send.
            rows = db.execute("SELECT seq,kind,data FROM consultation_outputs WHERE case_id=? AND stale=0", (cid,)).fetchall()
            invalidated = []
            for output in rows:
                derived = self._decode(output["data"])
                dependency_keys = derived.get("dependencies", {}).get("keys", ["*"])
                ids = derived.get("fact_ids", [])
                if "*" in dependency_keys or key in dependency_keys or old and old["id"] in ids:
                    db.execute("UPDATE consultation_outputs SET stale=1 WHERE seq=?", (output["seq"],))
                    invalidated.append(output["kind"])
            if "packet" in invalidated:
                self._job(db, cid, "review", now(), {"reason": "confirmed fact changed", "changed_key": key})
            self._event(db, cid, "FACT_CONFIRMED" if not old else "FACT_SUPERSEDED", {"fact": fact, "before": old})
        else:
            self._event(db, cid, "FACT_PROPOSED", {"fact": fact}, "ai")
        return fact

    def add_fact(self, cid: str, key: str, value: Any, **kwargs) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            fact = self._append(db, cid, key, value, **kwargs)
            if fact["confirmed"]:
                self._recompute(db, cid)
            return fact

    def confirm_fever_context(self, cid: str, value: dict, expected_fact_id: str | None = None) -> dict:
        from .epidemiology import FeverContext
        value = FeverContext.model_validate(value).model_dump(mode="json")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = next((f for f in self._facts(db, cid) if f["key"] == "fever_context"), None)
            if (current["id"] if current else None) != expected_fact_id:
                raise ValueError("Illness location context changed. Reload before confirming it.")
            if current and current["value"] == value:
                return current
            fact = self._append(db, cid, "fever_context", value, supersedes=expected_fact_id)
            self._recompute(db, cid)
            return fact

    def add_facts(self, cid: str, items: list[dict]) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in items:
                self._append(db, cid, **item)
            if any(item.get("confirmed", True) for item in items):
                self._recompute(db, cid)

    def confirm(self, cid: str, fid: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            fact = next((f for f in self._facts(db, cid, False) if f["id"] == fid and not f["confirmed"]), None)
            if not fact:
                raise ValueError("Proposal unavailable.")
            resolved = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='PROPOSAL_RESOLVED'", (cid,)).fetchall()
            if any(self._decode(row["data"])["proposal_id"] == fid for row in resolved):
                raise ValueError("Proposal already resolved.")
            # Link confirmation to proposal and previous confirmed evidence using the event.
            new = self._append(db, cid, fact["key"], fact["value"], source="device" if fact["key"] in config()["measurements"] else "worker", quote=fact.get("quote"), unit=fact.get("unit"), reason_missing=fact.get("reason_missing"), evidence=fact.get("evidence"))
            self._event(db, cid, "PROPOSAL_RESOLVED", {"proposal_id": fid, "confirmed_id": new["id"]})
            self._recompute(db, cid)

    def dismiss(self, cid: str, fid: str) -> None:
        with self.connect() as db:
            self._event(db, cid, "PROPOSAL_RESOLVED", {"proposal_id": fid, "dismissed": True})

    def pending(self, cid: str) -> list[dict]:
        resolved = {e["data"]["proposal_id"] for e in self.events(cid) if e["type"] == "PROPOSAL_RESOLVED"}
        return [f for f in self.facts(cid, False) if not f["confirmed"] and f["id"] not in resolved]

    def create(self, name: str, age: Age, sex: str, pregnant: bool, village: str, language: str, consent: bool) -> str:
        if type(pregnant) is not bool or type(consent) is not bool:
            raise ValueError("Pregnancy and sharing consent require explicit yes/no answers.")
        cid = "C-" + uuid.uuid4().hex[:10]
        data = {"name": name.strip() or cid, "sex": sex, "pregnant": pregnant, "village": village,
                "language": language, "consent": consent, "synthetic": True}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO consultation_cases(id,data,stage,created) VALUES(?,?,?,?)", (cid, self._encode(data), "DANGER_SCREEN", now()))
            self._event(db, cid, "CASE_CREATED", {"demographics": data, "age": age.json()})
            self._append(db, cid, "age", age.json())
            self._append(db, cid, "pregnant", pregnant)
            self._recompute(db, cid)
        return cid

    def _recompute(self, db, cid: str) -> dict:
        case = self._case(db, cid)
        facts = self._facts(db, cid)
        age_data = next(f["value"] for f in facts if f["key"] == "age")
        concern = self._output(db, cid, "ai_concern")
        urgency = evaluate(facts, Age.from_input(age_data["value"], age_data["unit"]), case["data"]["pregnant"], concern["level"] if concern else "GREEN")
        if concern:
            for rule in urgency["fired"]:
                if rule["rule_id"] == "AI-CONCERN-01":
                    rule["fact_ids"] = concern["fact_ids"]
        for fact in facts:
            if fact["key"] in config()["measurements"] and fact["value"] is not None:
                validated = validate_measurement(fact["key"], fact["value"], Age.from_input(age_data["value"], age_data["unit"]), fact.get("unit"))
                if not validated["ok"]:
                    if urgency["level"] == "GREEN":
                        urgency["level"] = "AMBER"
                    urgency["fired"].append({"rule_id": "MEAS-INVALID", "level": "AMBER", "layer": "validation", "text": validated["error"], "fact_ids": [fact["id"]]})
        previous = self._output(db, cid, "urgency", include_stale=True)
        urgency["fact_ids"] = [f["id"] for f in facts]
        urgency["diff"] = {"before": previous["level"] if previous else None, "after": urgency["level"]}
        self._save_output(db, cid, "urgency", urgency)
        self._event(db, cid, "URGENCY_EVALUATED", urgency, "rule")
        if urgency["level"] == "RED" and case["stage"] != "EMERGENCY":
            self._event(db, cid, "EMERGENCY_INTERRUPT", {"previous_stage": case["stage"], "rule_ids": [r["rule_id"] for r in urgency["fired"]]}, "rule")
            db.execute("UPDATE consultation_cases SET stage='EMERGENCY' WHERE id=?", (cid,))
        return urgency

    def _output(self, db, cid: str, kind: str, include_stale: bool = False) -> dict | None:
        row = db.execute("SELECT * FROM consultation_outputs WHERE case_id=? AND kind=? ORDER BY seq DESC LIMIT 1", (cid, kind)).fetchone()
        if not row:
            return None
        data = self._decode(row["data"])
        stale = bool(row["stale"]) or kind == "packet" and any(key not in data for key in ("care_revision", "context_revision", "media_ids", "fever_context"))
        if kind == "packet":
            from .epidemiology import catalogue_hash
            stale = stale or data.get("fever_context", {}).get("catalogue_hash") != catalogue_hash() or data.get("verification_contract") != "ledger-and-considerations-v1"
        if stale and not include_stale:
            return None
        return {**data, "stale": stale, "output_id": row["seq"]}

    def output(self, cid: str, kind: str, include_stale: bool = False) -> dict | None:
        with self.connect() as db:
            return self._output(db, cid, kind, include_stale)

    def _save_output(self, db, cid: str, kind: str, data: dict) -> None:
        db.execute("UPDATE consultation_outputs SET stale=1 WHERE case_id=? AND kind=?", (cid, kind))
        db.execute("INSERT INTO consultation_outputs(case_id,kind,data) VALUES(?,?,?)", (cid, kind, self._encode(data)))

    def stage(self, cid: str, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError("Unknown stage.")
        with self.connect() as db:
            case = self._case(db, cid)
            if stage in ("SENT", "DOCTOR_REPLIED", "CLOSED"):
                raise ValueError("Use the guarded send, reply and follow-up actions.")
            if stage != "EMERGENCY" and stage not in ("PATIENT_CARD", "FOLLOWUP") and not self.danger_complete(cid):
                raise ValueError("Complete the danger screen first.")
            db.execute("UPDATE consultation_cases SET stage=? WHERE id=?", (stage, cid))
            self._event(db, cid, "STAGE_CHANGED", {"before": case["stage"], "after": stage})

    def danger_complete(self, cid: str) -> bool:
        current = {f["key"]: f for f in self.facts(cid)}
        return all(q["key"] in current and (type(current[q["key"]]["value"]) is bool or current[q["key"]].get("reason_missing")) for q in danger_questions(self.age(cid), self.case(cid)["data"]["pregnant"]))

    def measurement(self, cid: str, key: str, value: Any, unit: str | None = None, reason: str = "", **kwargs) -> None:
        result = validate_measurement(key, value, self.age(cid), unit)
        if not result["ok"]:
            if result.get("missing") and reason.strip():
                self.add_fact(cid, key, None, reason_missing=reason.strip(), **kwargs)
                return
            raise ValueError(result["error"])
        self.add_fact(cid, key, result["value"], unit=result["unit"], **kwargs)

    def test_result(self, cid: str, key: str, value, unit: str = "", reason: str = "") -> None:
        from .poc import validate
        if value is None or str(value).strip() == "":
            if not reason.strip():
                raise ValueError("An unavailable test needs a reason.")
            self.add_fact(cid, key, None, reason_missing=reason)
            return
        result = validate(key, value, unit)
        self.add_fact(cid, key, result, unit=result.get("unit"), source="device")

    def events(self, cid: str) -> list[dict]:
        with self.connect() as db:
            return [{**dict(row), "data": self._decode(row["data"])} for row in db.execute("SELECT * FROM consultation_events WHERE case_id=? ORDER BY seq", (cid,))]

    def _media_ids(self, db, cid: str) -> list[int]:
        return [row[0] for row in db.execute("SELECT seq FROM consultation_events WHERE case_id=? AND type IN (?,?,?,?) ORDER BY seq", (cid, *WORKER_MEDIA))]

    def _context_revision(self, db, cid: str) -> int:
        return db.execute("SELECT COALESCE(MAX(seq),0) FROM consultation_events WHERE case_id=? AND type IN (?,?,?)", (cid, *CONTEXT_EVENTS)).fetchone()[0]

    def review_snapshot(self, cid: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN")
            self._case(db, cid)
            from .epidemiology import catalogue_hash
            return {"fact_ids": [f["id"] for f in self._facts(db, cid)], "media_ids": self._media_ids(db, cid), "care_revision": self._care_revision(db, cid), "handoff_id": self._handoff(db, cid)["handoff_id"], "fever_catalogue_hash": catalogue_hash()}

    def record(self, cid: str, event: str, data: dict, actor: str = "worker") -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            if event in ("DOCTOR_SEEN", "DOCTOR_AUDIO_ATTACHED") and (not case["sent"] or not case["data"]["consent"]):
                raise ValueError("An active, consented handoff is required for doctor activity.")
            if event == "DOCTOR_SEEN" and "handoff_id" in data and data["handoff_id"] != self._handoff(db, cid)["handoff_id"]:
                raise ValueError("A new worker handoff arrived. Review it before acknowledging.")
            self._event(db, cid, event, data, actor)
            if event in WORKER_MEDIA:
                self._worker_media_changed(db, cid)

    def _worker_media_changed(self, db, cid: str) -> None:
        db.execute("UPDATE consultation_outputs SET stale=1 WHERE case_id=? AND kind='packet'", (cid,))
        if self._output(db, cid, "packet", True):
            self._job(db, cid, "review", now(), {"reason": "media changed"})

    def consent(self, cid: str, consent: bool) -> None:
        if type(consent) is not bool:
            raise ValueError("Record an explicit consent choice.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            data = {**case["data"], "consent": consent}
            db.execute("UPDATE consultation_cases SET data=? WHERE id=?", (self._encode(data), cid))
            self._event(db, cid, "CONSENT_RECORDED", {"consent": consent})
            if not consent:
                db.execute("UPDATE consultation_jobs SET status='cancelled' WHERE case_id=? AND kind LIKE 'escalation-%' AND status IN ('pending','running')", (cid,))
                if case["sent"]:
                    db.execute("UPDATE consultation_cases SET sent=NULL,stage=? WHERE id=?", ("EMERGENCY" if case["stage"] == "EMERGENCY" else "REVIEW", cid))
                    self._event(db, cid, "SHARING_WITHDRAWN", {"previous_sent": case["sent"], "previous_stage": case["stage"], "resend_required": True})
            # Consent appears in the packet snapshot; regranting consent must
            # never restore a previously withdrawn remote handoff silently.
            if case["data"]["consent"] != consent:
                db.execute("UPDATE consultation_outputs SET stale=1 WHERE case_id=? AND kind='packet'", (cid,))

    def demographics(self, cid: str, sex: str, pregnant: bool, village: str, language: str) -> None:
        if type(pregnant) is not bool or language not in ("Urdu", "English"):
            raise ValueError("Choose a supported language and confirmed pregnancy status.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            after = {**case["data"], "sex": sex, "pregnant": pregnant, "village": village, "language": language}
            db.execute("UPDATE consultation_cases SET data=? WHERE id=?", (self._encode(after), cid))
            self._append(db, cid, "pregnant", pregnant)
            self._event(db, cid, "DEMOGRAPHICS_CORRECTED", {"before": case["data"], "after": after})
            db.execute("UPDATE consultation_outputs SET stale=1 WHERE case_id=? AND kind='packet'", (cid,))
            self._recompute(db, cid)

    def medcheck(self, cid: str) -> dict:
        facts = self.facts(cid)
        dependency_keys = ["medicines", "medicine_records", "reported_doses", "allergies", "allergy_records", "allergy_penicillin", "breastfeeding", "age", "weight", "pregnant"]
        ids = [f["id"] for f in facts if f["key"] in dependency_keys]
        cached = self.output(cid, "medcheck")
        if cached and cached.get("fact_ids") == ids:
            return cached
        medfact = next((f for f in facts if f["key"] == "medicines"), None)
        result = reconcile((medfact["value"] if medfact else []) or [], facts, self.case(cid)["data"]["pregnant"], self.age(cid))
        previous = self.output(cid, "medcheck", True)
        result.update({"fact_ids": ids, "dependencies": {"keys": dependency_keys}, "version": (previous or {}).get("version", 0) + 1,
            "diff": {"before": [f["rule_id"] for f in (previous or {}).get("flags", [])], "after": [f["rule_id"] for f in result["flags"]]}})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if [f["id"] for f in self._facts(db, cid) if f["key"] in dependency_keys] == ids:
                self._save_output(db, cid, "medcheck", result)
                self._event(db, cid, "MEDCHECK_UPDATED", {"version": result["version"], "fact_ids": ids, "diff": result["diff"]}, "rule")
        return result

    def medicines(self, cid: str, records: list[dict], history: str, allergy: bool | None, other_allergies: str, allergy_records: list[dict] | None = None) -> None:
        from .ai import MedicineRecord, AllergyRecord
        normalized = [MedicineRecord.model_validate(record).model_dump() for record in records if str(record.get("name", "")).strip()]
        items = [{"key": "medicine_records", "value": normalized},
            {"key": "medicines", "value": [m["name"] for m in normalized]},
            {"key": "medicine_history", "value": history},
            {"key": "allergy_penicillin", "value": allergy, "reason_missing": "Patient/worker unsure" if allergy is None else None},
            {"key": "allergies", "value": other_allergies}]
        if allergy_records is not None:
            items.append({"key": "allergy_records", "value": [AllergyRecord.model_validate(row).model_dump() for row in allergy_records if row.get("substance")]})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = next((f["value"] for f in self._facts(db, cid) if f["key"] == "reported_doses"), None)
            for item in items:
                self._append(db, cid, **item)
            if previous is not None:
                from .reported_doses import record_hash
                hashes = {record_hash(row) for row in normalized}
                current = [row for row in previous if row["record_hash"] in hashes]
                self._append(db, cid, "reported_doses", current)
            self._recompute(db, cid)

    def reported_medicine_amount(self, cid: str, index: int, expected_hash: str, dose_mg, route: str, regimen: dict, unknown_reason: str = "") -> None:
        from .reported_doses import ReportedDose, record_hash
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            facts = self._facts(db, cid)
            records = next((f["value"] for f in facts if f["key"] == "medicine_records"), [])
            if type(index) is not int or not 0 <= index < len(records) or record_hash(records[index]) != expected_hash or sum(record_hash(row) == expected_hash for row in records) != 1:
                raise ValueError("The medicine details changed. Recheck the current row before recording its amount.")
            record = records[index]
            entry = ReportedDose.model_validate({"record_hash": expected_hash, "medicine": record["name"], "dose_mg": dose_mg,
                "product": {"formulation": record["formulation"], "strength": record["strength"], "route": route}, "regimen": regimen, "unknown_reason": unknown_reason}).model_dump()
            previous = next((f["value"] for f in facts if f["key"] == "reported_doses"), [])
            if entry in previous:
                return
            updated = [row for row in previous if row["record_hash"] != expected_hash] + [entry]
            self._append(db, cid, "reported_doses", updated)
            self._recompute(db, cid)

    def acknowledge_medcheck(self, cid: str, version: int) -> None:
        checked = self.medcheck(cid)
        if checked["version"] != version:
            raise ValueError("The medicine check changed. Review the current flags before acknowledging.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._output(db, cid, "medcheck")
            if not current or current["version"] != version:
                raise ValueError("The medicine facts changed. Review the current flags.")
            self._event(db, cid, "MEDCHECK_ACKNOWLEDGED", {"version": version, "fact_ids": current["fact_ids"], "rule_ids": [flag["rule_id"] for flag in current["flags"]]})

    def review(self, cid: str, live: bool = False, inject: bool = False, *, persist_workflow: bool = True) -> dict:
        from .pipeline import review
        # Older cases retained the human-entered pregnancy answer only in the
        # intake event. Recover that exact answer before composing a new packet.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            facts = self._facts(db, cid)
            if not any(f["key"] == "pregnant" for f in facts) and type(case["data"].get("pregnant")) is bool:
                sources = db.execute("SELECT seq,type,data FROM consultation_events WHERE case_id=? AND type IN ('CASE_CREATED','DEMOGRAPHICS_CORRECTED') ORDER BY seq DESC", (cid,)).fetchall()
                if sources:
                    original = self._decode(sources[0]["data"])
                    saved = original.get("after", original.get("demographics", {})).get("pregnant")
                    if type(saved) is bool and saved == case["data"]["pregnant"]:
                        fact = self._append(db, cid, "pregnant", saved, quote=f"Confirmed intake event E{sources[0]['seq']}")
                        self._event(db, cid, "INTAKE_FACT_RECOVERED", {"key": "pregnant", "fact_id": fact["id"], "source_event_id": sources[0]["seq"]}, "migration")
                        self._recompute(db, cid)
        checked = self.medcheck(cid)
        with self.connect() as db:
            db.execute("BEGIN")
            case, facts = self._case(db, cid), self._facts(db, cid)
            previous = self._output(db, cid, "packet", True)
            events = [{**dict(row), "data": self._decode(row["data"])} for row in db.execute("SELECT * FROM consultation_events WHERE case_id=? ORDER BY seq", (cid,))]
            urgency = self._output(db, cid, "urgency")
            reuse_analysis = (self._output(db, cid, "considerations") or {}).get("analysis")
            care_revision, context_revision = self._care_revision(db, cid), self._context_revision(db, cid)
            source_handoff = self._handoff(db, cid)["handoff_id"]
            reviewers = self._reviewers(db)
            assigned_reviewer = (self._latest_route(db, cid).get("reviewer") or {}).get("id")
        version = previous["version"] + 1 if previous else 1
        packet = review(self.path, {"case": case, "facts": facts,
            "urgency": urgency, "medcheck": checked,
            "events": events, "version": version, "live": live, "inject": inject,
            "reviewers": reviewers, "assigned_reviewer_id": assigned_reviewer,
            "reuse_analysis": reuse_analysis}, persistent=persist_workflow)
        derived_analysis = packet.pop("_derived_analysis")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if [f["id"] for f in self._facts(db, cid)] != packet["fact_ids"]:
                raise ValueError("Facts changed during review. Generate a new packet.")
            media_types = ("PHOTO_ATTACHED", "AUDIO_ATTACHED", "COUGH_AUDIO_ATTACHED", "MEDICINE_IMAGE_READ")
            latest_media = db.execute("SELECT COALESCE(MAX(seq),0) FROM consultation_events WHERE case_id=? AND type IN (?,?,?,?)", (cid, *media_types)).fetchone()[0]
            if latest_media != max((e["seq"] for e in events if e["type"] in media_types), default=0):
                raise ValueError("Media changed during review. Generate a new packet.")
            if self._care_revision(db, cid) != care_revision:
                raise ValueError("Worker questions or care handoffs changed during review. Generate a new packet.")
            if self._context_revision(db, cid) != context_revision or self._case(db, cid)["data"] != case["data"]:
                raise ValueError("Intake or sharing details changed during review. Generate a new packet.")
            if self._handoff(db, cid)["handoff_id"] != source_handoff or self._case(db, cid)["sent"] != case["sent"]:
                raise ValueError("A new handoff started during review. Generate a new packet.")
            from .epidemiology import catalogue_hash
            if packet["fever_context"]["catalogue_hash"] != catalogue_hash():
                raise ValueError("Local fever reference content changed during review. Generate a new packet.")
            current = self._output(db, cid, "packet", True)
            if current and current["version"] >= version:
                raise ValueError("Another review completed first. Reload the latest packet.")
            packet["created"] = now()
            packet.update({"care_revision": care_revision, "context_revision": context_revision, "media_ids": [e["seq"] for e in events if e["type"] in WORKER_MEDIA]})
            if packet["verification"]["status"] == "VERIFIED":
                from .questions import QUESTIONS
                danger_keys = {q["key"] for questions in config()["danger_signs"].values() for q in questions}
                clinical_keys = sorted({q["asks_key"] for q in QUESTIONS} | danger_keys | set(config()["measurements"]) | {"age", "pregnant", "medicines", "medicine_records", "reported_doses", "allergy_records", "free_text", "glucose", "haemoglobin", "malaria_rdt", "pregnancy_test", "fever_context"})
                clinical_keys = [k for k in clinical_keys if k != "worker_request"]
                self._save_output(db, cid, "considerations", {"analysis": derived_analysis, "fact_ids": [f["id"] for f in facts if f["key"] in clinical_keys], "dependencies": {"keys": clinical_keys}})
            from .clinical import LEVELS
            proposed_concern = packet.get("ai_concern", {"level": "GREEN", "fact_ids": []})
            previous_concern = self._output(db, cid, "ai_concern")
            if LEVELS[proposed_concern["level"]] > LEVELS[(previous_concern or {}).get("level", "GREEN")]:
                by_id = {f["id"]: f for f in facts}
                proposed_concern["dependencies"] = {"keys": ["age", "pregnant", *[by_id[fid]["key"] for fid in proposed_concern["fact_ids"]]]}
                self._save_output(db, cid, "ai_concern", proposed_concern)
                self._event(db, cid, "AI_CONCERN_RECORDED", proposed_concern, "ai")
            current_urgency = self._output(db, cid, "urgency")
            if LEVELS[packet["urgency"]["level"]] > LEVELS[current_urgency["level"]]:
                self._save_output(db, cid, "urgency", packet["urgency"])
                self._event(db, cid, "URGENCY_RAISED_BY_AI", packet["urgency"], "rule")
                if packet["urgency"]["level"] == "RED":
                    db.execute("UPDATE consultation_cases SET stage='EMERGENCY' WHERE id=?", (cid,))
            self._save_output(db, cid, "packet", packet)
            self._event(db, cid, "PACKET_BLOCKED" if packet["verification"]["status"] == "BLOCKED" else "PACKET_COMPOSED",
                {"version": version, "verification": packet["verification"], "fact_ids": packet["fact_ids"], "mode": packet["mode"]}, "rule")
        return packet

    def readback(self, cid: str, patient_yes: bool, worker_yes: bool, language: str | None = None) -> None:
        packet = self.output(cid, "packet")
        if not packet or packet["verification"]["status"] != "VERIFIED" or not patient_yes or not worker_yes:
            raise ValueError("A verified current packet and patient/worker confirmation are required.")
        rejected = [e for e in self.events(cid) if e["type"] == "READBACK_REJECTED" and e["data"].get("packet_version") == packet["version"]]
        if rejected:
            raise ValueError("The patient rejected this version. Correct the facts and generate a new packet before confirming.")
        from .speech_audio import text_identity
        language = language or self.case(cid)["data"]["language"]
        view = self.readback_view(cid, packet, language)
        if not view:
            raise ValueError("Confirm the complete interpreter translation before patient read-back.")
        self.record(cid, "READBACK_CONFIRMED", {"packet_version": packet["version"], "fact_ids": packet["fact_ids"], "language": language, "text_hash": text_identity(view["readbacks"][language]["text"], language)})

    def send(self, cid: str) -> None:
        checked = self.medcheck(cid)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case, packet = self._case(db, cid), self._output(db, cid, "packet")
            if not case["data"]["consent"]:
                raise ValueError("Consent is required to send.")
            if not packet or packet["verification"]["status"] != "VERIFIED":
                raise ValueError("Sending blocked: review the current confirmed facts first.")
            if packet.get("care_revision") != self._care_revision(db, cid) or packet.get("context_revision") != self._context_revision(db, cid) or packet.get("media_ids") != self._media_ids(db, cid):
                raise ValueError("Case details, media or worker requests changed. Generate and read back the current packet before sending.")
            current_medcheck = self._output(db, cid, "medcheck")
            if not current_medcheck or current_medcheck["version"] != checked["version"]:
                raise ValueError("Medicine facts changed. Review the current check before sending.")
            if current_medcheck["flags"]:
                rows = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='MEDCHECK_ACKNOWLEDGED'", (cid,)).fetchall()
                if not any(self._decode(row["data"]).get("version") == current_medcheck["version"] and self._decode(row["data"]).get("fact_ids") == current_medcheck["fact_ids"] for row in rows):
                    raise ValueError("Review and acknowledge the current medicine safety flags before sending.")
            readback = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='READBACK_CONFIRMED' ORDER BY seq DESC LIMIT 1", (cid,)).fetchone()
            if not readback or self._decode(readback["data"])["fact_ids"] != packet["fact_ids"] or self._decode(readback["data"])["packet_version"] != packet["version"]:
                raise ValueError("Read back and confirm this version before sending.")
            from .speech_audio import text_identity
            readback_data = self._decode(readback["data"])
            language = readback_data.get("language", case["data"]["language"])
            view = self.readback_view(cid, packet, language)
            if not view or readback_data.get("text_hash") != text_identity(view["readbacks"][language]["text"], language):
                raise ValueError("The read-back wording changed. Confirm the exact current language version before sending.")
            rejections = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='READBACK_REJECTED'", (cid,)).fetchall()
            if any(self._decode(row["data"]).get("packet_version") == packet["version"] for row in rejections):
                raise ValueError("The patient rejected this packet. Correct the facts and read back a new version.")
            if not self.danger_complete(cid) or self.pending(cid):
                raise ValueError("Finish the danger screen and confirm or dismiss proposed facts.")
            if any(f["key"] == "age" and f["value"].get("warning") for f in self._facts(db, cid)):
                age_events = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='AGE_CONFIRMED'", (cid,)).fetchall()
                age_id = next(f["id"] for f in self._facts(db, cid) if f["key"] == "age")
                if not any(self._decode(e["data"]).get("fact_id") == age_id for e in age_events):
                    raise ValueError("Confirm the age plausibility warning first.")
            self._start_handoff(db, cid, "worker sent confirmed packet")
            self._event(db, cid, "PACKET_SENT", {"version": packet["version"], "simulated": True}, "worker")
            from .pipeline import resume
            resume(self.path, cid, "worker", {"confirmed": True, "fact_ids": packet["fact_ids"], "version": packet["version"]})

    def emergency_referral(self, cid: str, facility_id: str) -> None:
        facility = next((f for f in resources()["facilities"] if f["id"] == facility_id and "oxygen" in f["capabilities"]), None)
        if not facility:
            raise ValueError("Choose an oxygen-capable referral facility.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            urgency = self._output(db, cid, "urgency")
            if urgency["level"] != "RED":
                raise ValueError("Emergency referral requires a RED rule.")
            if not case["data"]["consent"]:
                raise ValueError("Consent to share is required for the simulated pre-alert.")
            self._register_referral(db, cid, "EMERGENCY_REFERRAL", {"facility": facility, "urgency": urgency, "facts": self._facts(db, cid), "simulated": True})
            if not case["sent"]:
                self._start_handoff(db, cid, "emergency referral")

    def task(self, cid: str, key: str, text: str) -> None:
        from .questions import QUESTIONS
        from .poc import tests
        if key not in config()["measurements"] and key not in {q["asks_key"] for q in QUESTIONS} | {"photo", "audio", "free_text"} | set(tests()):
            raise ValueError("Unsupported task key.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            if not case["sent"] or not case["data"]["consent"] or not text.strip():
                raise ValueError("A sent case and clear doctor instructions are required.")
            self._event(db, cid, "TASK_CREATED", {"task_id": uuid.uuid4().hex[:10], "key": key, "text": text.strip()}, "doctor (simulated)")

    def _orders(self, db, cid: str) -> list[dict]:
        rows = db.execute("SELECT seq,ts,type,data FROM consultation_events WHERE case_id=? AND type IN ('TEST_ORDERED','TEST_RESULT_RETURNED','TEST_UNAVAILABLE','TEST_ORDER_CANCELLED') ORDER BY seq", (cid,)).fetchall()
        orders = {}
        for row in rows:
            data = self._decode(row["data"])
            oid = data["order_id"]
            if row["type"] == "TEST_ORDERED":
                orders[oid] = {**data, "status": "pending", "created": row["ts"]}
            elif oid in orders:
                status = {"TEST_RESULT_RETURNED": "result returned", "TEST_UNAVAILABLE": "unavailable", "TEST_ORDER_CANCELLED": "cancelled"}[row["type"]]
                orders[oid].update({**data, "status": status, "updated": row["ts"]})
        responses = [self._decode(row["data"]) for row in db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='DOCTOR_REPLY'", (cid,))]
        reviewed_ids = {fid for response in responses for fid in response.get("fact_ids", [])}
        reviewed_orders = {oid for response in responses for oid in response.get("reviewed_order_ids", [])}
        return [{**order, "doctor_reviewed": order["order_id"] in reviewed_orders or bool(order.get("fact_id") in reviewed_ids)} for order in orders.values()]

    def orders(self, cid: str) -> list[dict]:
        with self.connect() as db:
            self._case(db, cid)
            return self._orders(db, cid)

    def _create_orders(self, db, cid: str, reply_id: str, selected: list[dict], repeat: bool = False) -> list[str]:
        previous = {order["test_id"]: order for order in self._orders(db, cid)}
        ids = []
        for spec in selected:
            old = previous.get(spec["test_id"])
            if old and (old["status"] == "pending" or not repeat):
                ids.append(old["order_id"])
                continue
            order_id, task_id = uuid.uuid4().hex[:12], uuid.uuid4().hex[:10]
            data = {**spec, "order_id": order_id, "task_id": task_id, "reply_id": reply_id}
            self._event(db, cid, "TEST_ORDERED", data, "doctor (simulated)")
            self._event(db, cid, "TASK_CREATED", {"task_id": task_id, "key": spec["key"], "text": spec["instructions"], "order_id": order_id, "test_id": spec["test_id"], "test_label": spec["label"]}, "doctor (simulated)")
            ids.append(order_id)
        return ids

    def cancel_order(self, cid: str, order_id: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("Record the doctor's reason for cancelling this test.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            if not case["sent"] or not case["data"]["consent"]:
                raise ValueError("An active consented handoff is required.")
            order = next((o for o in self._orders(db, cid) if o["order_id"] == order_id and o["status"] == "pending"), None)
            if not order:
                raise ValueError("This order is no longer pending.")
            self._event(db, cid, "TEST_ORDER_CANCELLED", {"order_id": order_id, "reason": reason.strip()}, "doctor (simulated)")
            self._event(db, cid, "TASK_CANCELLED", {"task_id": order["task_id"], "reason": reason.strip()}, "doctor (simulated)")

    def _media_path(self, cid: str, value: str, extensions: tuple[str, ...]) -> Path:
        path = Path(value).resolve()
        root = (ROOT / "assets/uploaded/consultation" / cid).resolve()
        suffix = Path(path.name.removesuffix(".enc")).suffix.lower()
        if not path.is_relative_to(root) or not path.is_file() or suffix not in extensions:
            raise ValueError("Use a supported file uploaded to this case.")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Use a file smaller than 20 MB before encryption.")
        return path

    def tasks(self, cid: str) -> list[dict]:
        events = self.events(cid)
        done = {e["data"]["task_id"] for e in events if e["type"] in ("TASK_COMPLETED", "TASK_CANCELLED")}
        cancelled = {e["data"]["task_id"] for e in events if e["type"] == "TASK_CANCELLED"}
        responses = {e["data"]["task_id"]: e["data"] for e in events if e["type"] == "TASK_COMPLETED"}
        return [{**e["data"], "done": e["data"]["task_id"] in done, "cancelled": e["data"]["task_id"] in cancelled,
                 "response": responses.get(e["data"]["task_id"])} for e in events if e["type"] == "TASK_CREATED"]

    def complete_task(self, cid: str, task_id: str, value: Any, reason: str = "") -> None:
        from .poc import tests as poc_tests, validate as validate_test
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, cid)
            if not case["sent"] or not case["data"]["consent"]:
                raise ValueError("Sharing consent and an active handoff are required to return a doctor task.")
            task = next((t for t in self.tasks(cid) if t["task_id"] == task_id and not t["done"]), None)
            if not task:
                raise ValueError("Task is already complete or unavailable.")
            key = task["key"]
            order = next((o for o in self._orders(db, cid) if o["order_id"] == task.get("order_id")), None)
            fact_id = None
            unavailable = value is None
            if key == "lab_result" and order:
                from .orders import LabReport
                raw = dict(value) if isinstance(value, dict) else {}
                attachment = raw.pop("attachment_path", None)
                if not str(raw.get("result", "")).strip():
                    if not reason.strip():
                        raise ValueError("Transcribe the laboratory report or explain why it is unavailable.")
                    unavailable = True
                    fact_id = self._append(db, cid, "lab_result_" + order["order_id"], None, reason_missing=reason.strip())["id"]
                else:
                    report = LabReport.model_validate(raw).model_dump(mode="json")
                    report.update({"test": order["label"], "order_id": order["order_id"]})
                    if attachment:
                        path = self._media_path(cid, attachment, (".pdf", ".jpg", ".jpeg", ".png"))
                        self._event(db, cid, "PHOTO_ATTACHED", {"path": str(path), "site": order["label"] + " laboratory report", "order_id": order["order_id"]})
                        report["media_event_id"] = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                    fact_id = self._append(db, cid, "lab_result_" + order["order_id"], report)["id"]
                self._recompute(db, cid)
            elif key in ("photo", "audio"):
                if not value:
                    if not reason.strip():
                        raise ValueError("Attach the requested file or explain why it is unavailable.")
                    value, unavailable = None, True
                    fact_id = self._append(db, cid, "requested_media_" + task_id, None, reason_missing=reason.strip())["id"]
                    self._recompute(db, cid)
                else:
                    extensions = (".png", ".jpg", ".jpeg") if key == "photo" else (".wav", ".mp3", ".webm", ".m4a", ".ogg")
                    path = self._media_path(cid, value, extensions)
                    self._event(db, cid, "PHOTO_ATTACHED" if key == "photo" else "AUDIO_ATTACHED", {"path": str(path), "experimental": True})
                    db.execute("UPDATE consultation_outputs SET stale=1 WHERE case_id=? AND kind='packet'", (cid,))
            elif key in config()["measurements"]:
                from .measurements import task_measurement
                checked = task_measurement(key, value, self.age(cid))
                if checked["ok"]:
                    fact_id = self._append(db, cid, key, checked["value"], unit=checked["unit"], source="device", quote=checked["quote"])["id"]
                    value = checked["recorded"]
                elif checked.get("missing") and reason.strip():
                    value, unavailable = None, True
                    fact_id = self._append(db, cid, key, None, reason_missing=reason.strip())["id"]
                else:
                    raise ValueError(checked["error"])
                self._recompute(db, cid)
            elif key in poc_tests():
                if not isinstance(value, dict):
                    raise ValueError("Return the test result and unit together.")
                raw = value.get("result", value.get("value"))
                if raw in (None, "") and reason:
                    unavailable = True
                    fact_id = self._append(db, cid, key, None, reason_missing=reason)["id"]
                else:
                    result = validate_test(key, raw, value.get("unit", ""))
                    fact_id = self._append(db, cid, key, result, unit=result.get("unit"), source="device")["id"]
                self._recompute(db, cid)
            else:
                if isinstance(value, str) and not value.strip():
                    if not reason.strip():
                        raise ValueError("Enter the requested answer or give a reason it is unavailable.")
                    value = None
                fact_id = self._append(db, cid, key, value, reason_missing=reason if value is None else None)["id"]
                self._recompute(db, cid)
            self._event(db, cid, "TASK_COMPLETED", {"task_id": task_id, "value": value, "reason": reason, "fact_id": fact_id})
            if order:
                fact = next((f for f in self._facts(db, cid) if f["id"] == fact_id), None)
                unavailable = unavailable or fact is not None and fact["value"] is None
                self._event(db, cid, "TEST_UNAVAILABLE" if unavailable else "TEST_RESULT_RETURNED", {"order_id": order["order_id"], "task_id": task_id, "fact_id": fact_id, "reason": reason, "value": fact["value"] if fact else value})
            self._start_handoff(db, cid, "worker returned doctor task")
        if self.case(cid)["sent"] and self.case(cid)["data"]["consent"]:
            packet = self.review(cid, live=False)
            if packet["verification"]["status"] == "VERIFIED":
                from .pipeline import resume
                resume(self.path, cid, "worker", {"confirmed": True, "source": "worker-confirmed doctor task", "fact_ids": packet["fact_ids"]})
                self.record(cid, "CASE_UPDATE_AVAILABLE", {"packet_version": packet["version"], "task_id": task_id}, "worker")

    def reply(self, cid: str, reply: dict) -> None:
        reply = dict(reply)
        if not self.case(cid)["sent"]:
            raise ValueError("Send the case before recording a doctor response.")
        if not self.case(cid)["data"]["consent"]:
            raise ValueError("Sharing consent was withdrawn. No new doctor response can be recorded through this channel.")
        if not reply.get("advice", "").strip():
            raise ValueError("Doctor advice is required.")
        if reply.get("ai_guidance_used") and not reply.get("guidance_confirmed"):
            raise ValueError("Doctor confirmation of AI-drafted patient wording is required.")
        status = reply.get("status", "final")
        if status not in ("final", "awaiting_information"):
            raise ValueError("Choose final guidance or awaiting information/results.")
        from .orders import selected
        test_orders = selected(reply.get("local_tests", []), reply.get("lab_tests", []))
        if type(reply.get("repeat_tests", False)) is not bool:
            raise ValueError("Confirm explicitly when repeating previously requested tests.")
        audio_path = reply.get("guidance_audio_path")
        if audio_path:
            if reply.get("guidance_audio_confirmed") is not True:
                raise ValueError("Doctor confirmation of the recorded patient guidance is required.")
            audio_path = str(self._media_path(cid, audio_path, (".wav", ".mp3", ".webm", ".m4a", ".ogg")))
        from .patient import Schedule, guidance_confirmation_id, complete_guidance
        from .dosing import PrescriptionProduct, PrescriptionRegimen
        reply["prescription_product"] = PrescriptionProduct.model_validate(reply.get("prescription_product") or {}).model_dump()
        reply["prescription_regimen"] = PrescriptionRegimen.model_validate(reply.get("prescription_regimen") or {}).model_dump()
        reply["medication_schedule"] = [Schedule.model_validate(row).model_dump() for row in reply.get("medication_schedule", []) if row.get("medicine")]
        from .medicines import check_plan
        with self.connect() as db:
            db.execute("BEGIN")
            reviewed_facts = self._facts(db, cid)
            reviewed_media = self._media_ids(db, cid)
            reviewed_care = self._care_revision(db, cid)
            reviewed_handoff = self._handoff(db, cid)["handoff_id"]
            from .epidemiology import catalogue_hash
            reviewed_fever_catalogue = catalogue_hash()
            draft_row = db.execute("SELECT data FROM consultation_drafts WHERE case_id=? AND section='doctor_reply'", (cid,)).fetchone()
            draft_data = self._decode(draft_row["data"]) if draft_row else {}
            proposal = draft_data.get("guidance_proposal")
            audio_proposal = draft_data.get("guidance_audio_proposal")
            generated_audio = None
            if audio_path:
                # Immutable generation provenance survives removal of a draft
                # flag. The caller cannot relabel known generated audio as a
                # new manual recording to avoid the exact-text guard.
                for row in db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='DOCTOR_AUDIO_ATTACHED' ORDER BY seq", (cid,)):
                    data = self._decode(row["data"])
                    if data.get("text_hash") and Path(data.get("path", "")).resolve() == Path(audio_path).resolve():
                        generated_audio = data
                if generated_audio is None and isinstance(audio_proposal, dict) and Path(audio_proposal.get("path", "")).resolve() == Path(audio_path).resolve():
                    generated_audio = audio_proposal
        reply.pop("guidance_audio_provenance", None)
        if generated_audio:
            audio_language = generated_audio.get("language")
            if audio_language not in ("English", "Urdu") or hashlib.sha256(complete_guidance(reply, audio_language).encode()).hexdigest() != generated_audio.get("text_hash"):
                raise ValueError("The written patient instructions changed after this audio was generated. Generate a new recording or supply and confirm a complete human recording.")
            reply["guidance_audio_provenance"] = {"origin": "generated from complete doctor-written instructions", "language": audio_language,
                "text_hash": generated_audio["text_hash"], "confirmed_by": "doctor (simulated)", "independent_language_validation": "pending"}
        local_text = reply.get("local_language", "")
        assisted = bool(reply.get("ai_guidance_used") or proposal) and bool(local_text.strip())
        reply["ai_guidance_used"] = assisted
        if local_text.strip():
            approval = guidance_confirmation_id(cid, reply["advice"], local_text,
                {"fact_ids": [f["id"] for f in reviewed_facts], "media_ids": reviewed_media, "care_revision": reviewed_care, "handoff_id": reviewed_handoff, "fever_catalogue_hash": reviewed_fever_catalogue})
            if reply.get("guidance_confirmed") is not True or reply.get("guidance_confirmation_id") != approval:
                raise ValueError("Verify this exact patient wording against the current advice and case before saving.")
            reply["guidance_provenance"] = {"assistance": "AI-assisted draft" if assisted else "doctor-authored wording",
                "proposal_source_hash": proposal.get("source_hash") if isinstance(proposal, dict) else None,
                "confirmed_source_hash": hashlib.sha256(reply["advice"].encode()).hexdigest(),
                "confirmed_text_hash": hashlib.sha256(local_text.encode()).hexdigest(), "confirmation_id": approval,
                "confirmed_by": "doctor (simulated)"}
        else:
            reply.pop("guidance_provenance", None)
        age_value = next(f["value"] for f in reviewed_facts if f["key"] == "age")
        checks = check_plan(reply.get("medicine", ""), reply.get("dose_mg"), reply["medication_schedule"], reply.get("prescription", ""), reviewed_facts,
                            Age.from_record(age_value), self.case(cid)["data"]["pregnant"], reply["prescription_product"], reply["prescription_regimen"])
        if (reply.get("medicine") or reply.get("prescription") or reply["medication_schedule"]) and checks["flags"] and not reply.get("safety_acknowledged"):
            raise ValueError("Review and acknowledge the prescription safety flags before saving.")
        reply = {**reply, "reply_id": uuid.uuid4().hex[:12], "status": status, "guidance_audio_path": audio_path,
                 "fact_ids": [f["id"] for f in reviewed_facts], "media_ids": reviewed_media, "fever_catalogue_hash": reviewed_fever_catalogue, "prescription_checks": checks, "simulated": True, "followup_due": None}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current_case = self._case(db, cid)
            if not current_case["sent"] or not current_case["data"]["consent"]:
                raise ValueError("Sharing was withdrawn while this response was being prepared.")
            if reviewed_handoff != self._handoff(db, cid)["handoff_id"] or "reviewed_handoff_id" in reply and reply["reviewed_handoff_id"] != reviewed_handoff:
                raise ValueError("A new worker handoff arrived. Review it before replying.")
            if reviewed_fever_catalogue != catalogue_hash() or "reviewed_fever_catalogue_hash" in reply and reply["reviewed_fever_catalogue_hash"] != reviewed_fever_catalogue:
                raise ValueError("Local fever reference content changed. Review the current context before replying.")
            if reply["fact_ids"] != [f["id"] for f in self._facts(db, cid)]:
                raise ValueError("Facts changed during the prescription check. Review the latest case before replying.")
            if "reviewed_fact_ids" in reply and reply["reviewed_fact_ids"] != [f["id"] for f in self._facts(db, cid)]:
                raise ValueError("The worker updated this case while you were replying. Review the latest facts and resubmit.")
            if reviewed_media != self._media_ids(db, cid) or "reviewed_media_ids" in reply and reply["reviewed_media_ids"] != reviewed_media:
                raise ValueError("The worker added or changed case media while you were replying. Review the latest photos/audio before saving.")
            if reviewed_care != self._care_revision(db, cid) or "reviewed_care_revision" in reply and reply["reviewed_care_revision"] != reviewed_care:
                raise ValueError("Worker requests or handoff statuses changed. Review the latest care updates before replying.")
            current_draft = db.execute("SELECT data FROM consultation_drafts WHERE case_id=? AND section='doctor_reply'", (cid,)).fetchone()
            if proposal != (self._decode(current_draft["data"]) if current_draft else {}).get("guidance_proposal"):
                raise ValueError("A new patient-wording draft was generated while saving. Review the current wording first.")
            if audio_proposal != (self._decode(current_draft["data"]) if current_draft else {}).get("guidance_audio_proposal"):
                raise ValueError("A new guidance-audio draft was generated while saving. Review the current recording first.")
            self._resolve_worker_requests(db, cid, reply.get("resolved_request_ids", []), reply["reply_id"])
            known_orders = {order["order_id"]: order for order in self._orders(db, cid)}
            reviewed_orders = reply.get("reviewed_order_ids", [])
            if not isinstance(reviewed_orders, list) or any(oid not in known_orders or known_orders[oid]["status"] not in ("result returned", "unavailable") for oid in reviewed_orders):
                raise ValueError("Review the current returned results before acknowledging them.")
            if reply.get("diagnosis", "").strip():
                self._append(db, cid, "doctor_diagnosis", reply["diagnosis"].strip(), source="doctor")
            self._append(db, cid, "doctor_plan", {"advice": reply["advice"], "status": status, "prescription": reply.get("prescription", ""), "referral": reply.get("referral", ""),
                "prescription_product": reply["prescription_product"], "prescription_regimen": reply["prescription_regimen"], "medicine": reply.get("medicine", ""), "dose_mg": reply.get("dose_mg"),
                "local_language": reply.get("local_language", ""), "medication_schedule": reply["medication_schedule"], "feeding": reply.get("feeding", ""),
                "ors": reply.get("ors", ""), "return_if": reply.get("return_if", ""), "counselling_script": reply.get("counselling_script", ""),
                "request_call": reply.get("request_call", False), "tests_local": reply.get("local_tests", []), "tests_lab": reply.get("lab_tests", [])}, source="doctor")
            self._recompute(db, cid)
            reply["fact_ids"] = [f["id"] for f in self._facts(db, cid)]
            effective = datetime.now(timezone.utc) + timedelta(minutes=self._case(db, cid)["clock_minutes"])
            if status == "final":
                reply["followup_due"] = (effective + timedelta(hours=config()["routing"]["followup_hours"])).isoformat()
            reply["order_ids"] = self._create_orders(db, cid, reply["reply_id"], test_orders, reply.get("repeat_tests", False))
            if reply.get("request_call"):
                reply["call_id"] = self._request_call(db, cid, reply["reply_id"])
            if reply.get("referral"):
                facility = next((f for f in resources()["facilities"] if f["name"] == reply["referral"]), {"name": reply["referral"], "capabilities": [], "verified": False})
                reply["referral_id"] = self._register_referral(db, cid, "REFERRAL_PREALERT_CREATED", {"facility": facility, "fact_ids": reply["fact_ids"], "urgency": self._output(db, cid, "urgency"), "doctor_advice": reply["advice"], "reply_id": reply["reply_id"], "simulated": True})
            self._event(db, cid, "DOCTOR_REPLY", reply, "doctor (simulated)")
            if audio_path:
                self._event(db, cid, "GUIDANCE_AUDIO_APPROVED", {"reply_id": reply["reply_id"], "path": audio_path}, "doctor (simulated)")
            stage = "DOCTOR_REPLIED" if status == "final" else "EMERGENCY" if self._output(db, cid, "urgency")["level"] == "RED" else "SENT"
            db.execute("UPDATE consultation_cases SET stage=? WHERE id=?", (stage, cid))
            if reply["followup_due"]:
                self._job(db, cid, "followup", reply["followup_due"], {"reply_fact_ids": reply["fact_ids"], "reply_id": reply["reply_id"]})
            else:
                db.execute("UPDATE consultation_jobs SET status='cancelled' WHERE case_id=? AND kind='followup' AND status='pending'", (cid,))
        from .pipeline import resume
        resume(self.path, cid, "doctor", {"reply": reply})

    def latest_reply(self, cid: str) -> dict | None:
        return self.care_status(cid)["latest_reply"]

    def advance_clock(self, cid: str, minutes: int = 10) -> None:
        if minutes <= 0:
            raise ValueError("Advance time by a positive interval.")
        with self.connect() as db:
            case = self._case(db, cid)
            total = case["clock_minutes"] + minutes
            db.execute("UPDATE consultation_cases SET clock_minutes=? WHERE id=?", (total, cid))
            self._event(db, cid, "CLOCK_ADVANCED", {"minutes": minutes, "total": total, "simulated": True}, "demo")
        self.tick()

    def safety_update(self, cid: str, *, worse: bool = False, new_danger: bool = False, unable_drink: bool = False,
                      notes: str = "", worker_confirmed: bool = False) -> None:
        """Record urgent worker observations independently of the final-plan gate."""
        if worker_confirmed is not True or any(type(v) is not bool for v in (worse, new_danger, unable_drink)):
            raise ValueError("The worker must confirm the reported change using yes/no selections.")
        if not any((worse, new_danger, unable_drink)):
            raise ValueError("Select the worsening or danger actually reported.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._case(db, cid)
            data = {"worse": worse, "new_danger": new_danger, "unable_drink": unable_drink, "notes": notes.strip(), "worker_confirmed": True}
            self._event(db, cid, "SAFETY_UPDATE", data)
            if worse:
                self._append(db, cid, "followup_worse", True)
            if new_danger or unable_drink:
                self._append(db, cid, "followup_danger", True)
            urgency = self._recompute(db, cid)
            db.execute("UPDATE consultation_cases SET stage=? WHERE id=?", ("EMERGENCY" if urgency["level"] == "RED" else "REVIEW", cid))
            report = "; ".join(label for flag, label in ((worse, "Condition reported worse"), (new_danger, "New danger sign reported"), (unable_drink, "Unable to drink/eat reported")) if flag)
            self._worker_request(db, cid, "urgent reassessment", "safety_update", report + (": " + notes.strip() if notes.strip() else ""), refresh=True)

    def followup(self, cid: str, answers: dict) -> None:
        latest = self.latest_reply(cid)
        if not latest:
            raise ValueError("Doctor response required before follow-up.")
        if latest.get("status", "final") != "final":
            raise ValueError("The doctor is awaiting information/results. Return the requested tasks before recording recovery follow-up.")
        answers = {"condition": "unknown", "drinking": "unknown", "attended": None, "new_danger": None, "outcome": "ongoing", **answers}
        if answers["condition"] not in ("better", "same", "worse", "unknown") or answers["drinking"] not in ("normal", "less", "unable", "unknown"):
            raise ValueError("Choose a supported follow-up answer or record unknown.")
        if any(answers[key] is not None and type(answers[key]) is not bool for key in ("attended", "new_danger")):
            raise ValueError("Follow-up yes/no answers must be true, false or unknown.")
        if answers["outcome"] not in ("ongoing", "recovered", "referred", "admitted", "lost to follow-up"):
            raise ValueError("Choose a supported follow-up outcome.")
        if answers["outcome"] == "recovered" and (answers["condition"] != "better" or answers["drinking"] != "normal" or answers["new_danger"] is not False):
            raise ValueError("Recovered requires reported improvement, normal drinking/eating and no new danger. Otherwise record an ongoing or referred outcome.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current_response = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='DOCTOR_REPLY' ORDER BY seq DESC LIMIT 1", (cid,)).fetchone()
            latest = self._decode(current_response["data"])
            if latest.get("status", "final") != "final":
                raise ValueError("The doctor is awaiting information/results. Return the requested tasks before recording recovery follow-up.")
            unresolved_red = self._output(db, cid, "urgency")["level"] == "RED"
            if answers["outcome"] == "recovered" and unresolved_red:
                raise ValueError("A current RED warning is unresolved. Update the observations and obtain reassessment before closing as recovered.")
            if answers["outcome"] == "recovered" and latest.get("fact_ids") != [f["id"] for f in self._facts(db, cid)]:
                raise ValueError("The case was updated after the doctor's response. Obtain review of the current information before recording recovery.")
            if answers["outcome"] == "recovered" and latest.get("media_ids", []) != self._media_ids(db, cid):
                raise ValueError("New case photos/audio need doctor review before recording recovery.")
            if answers["outcome"] == "recovered" and any(o["status"] == "pending" or o["status"] in ("unavailable", "result returned") and not o["doctor_reviewed"] for o in self._orders(db, cid)):
                raise ValueError("Doctor-requested tests or returned results still require review or explicit cancellation before closing as recovered.")
            care = self._care(db, cid)
            pending_transfer = any(r["status"] in REFERRAL_ACTIVE for r in care["referrals"])
            if answers["outcome"] == "recovered" and (any(not task["done"] for task in self.tasks(cid)) or any(r["status"] == "open" for r in care["requests"]) or any(c["status"] in CALL_ACTIVE for c in care["calls"]) or pending_transfer):
                raise ValueError("Outstanding doctor tasks, clarification, call or referral follow-through must be completed or explicitly resolved before recording recovery.")
            self._event(db, cid, "FOLLOWUP", answers)
            db.execute("UPDATE consultation_jobs SET status='done' WHERE case_id=? AND kind='followup'", (cid,))
            worsening = answers.get("condition") == "worse" or answers.get("new_danger") is True or answers.get("drinking") == "unable"
            transfer_followup = answers["outcome"] == "referred" and pending_transfer
            needs_reassessment = worsening or unresolved_red and (answers["outcome"] not in ("referred", "admitted") or transfer_followup)
            db.execute("UPDATE consultation_cases SET stage=? WHERE id=?", ("SENT" if needs_reassessment else "FOLLOWUP" if answers["outcome"] == "ongoing" or transfer_followup else "CLOSED", cid))
            if needs_reassessment:
                if worsening:
                    self._append(db, cid, "followup_worse", True)
                if answers.get("new_danger") is True or answers.get("drinking") == "unable":
                    self._append(db, cid, "followup_danger", True)
                urgency = self._recompute(db, cid)
                self._event(db, cid, "FOLLOWUP_REESCALATED", urgency, "rule")
                case = self._case(db, cid)
                if not (case["sent"] and case["data"]["consent"]):
                    if urgency["level"] != "RED":
                        db.execute("UPDATE consultation_cases SET stage='REVIEW' WHERE id=?", (cid,))
                self._worker_request(db, cid, "urgent reassessment", "followup", f"Follow-up requires reassessment: condition {answers['condition']}; drinking/eating {answers['drinking']}; new danger {answers['new_danger']}; reported outcome {answers['outcome']}.", refresh=True)
            elif answers["outcome"] == "ongoing" or transfer_followup:
                effective = datetime.now(timezone.utc) + timedelta(minutes=self._case(db, cid)["clock_minutes"])
                self._job(db, cid, "followup", (effective + timedelta(hours=config()["routing"]["followup_hours"])).isoformat(), {"reason": "ongoing symptoms"})

    def _job(self, db, cid: str, kind: str, due: str, data: dict) -> None:
        data = {**data, "revision": [db.execute("SELECT COALESCE(MAX(seq),0) FROM consultation_facts WHERE case_id=?", (cid,)).fetchone()[0],
                                     db.execute("SELECT COALESCE(MAX(seq),0) FROM consultation_events WHERE case_id=?", (cid,)).fetchone()[0]]}
        ident = f"{cid}:{kind}"
        db.execute("INSERT INTO consultation_jobs(id,case_id,kind,due,status,data) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET due=excluded.due,status='pending',data=excluded.data,attempts=0", (ident, cid, kind, due, "pending", self._encode(data)))

    def _schedule_escalation(self, db, cid: str) -> None:
        case = self._case(db, cid)
        if not case["sent"] or not case["data"]["consent"]:
            return
        effective = datetime.now(timezone.utc) + timedelta(minutes=case["clock_minutes"])
        level = self._output(db, cid, "urgency")["level"]
        for index, (action, delay) in enumerate(zip(("Next doctor", "Automated call", "SMS to supervisor"), escalation_delays(level)), 1):
            due = effective + timedelta(minutes=delay)
            self._job(db, cid, f"escalation-{index}", due.isoformat(), {"action": action, "sent_revision": case["sent"], "handoff_id": self._handoff(db, cid)["handoff_id"], "urgency": level, "delay_minutes": delay, "simulated": True})

    def tick(self) -> None:
        # Claim one due job transactionally. A lease can be retried after a crash.
        for case in self.list_cases():
            effective = datetime.now(timezone.utc) + timedelta(minutes=case["clock_minutes"])
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                case = self._case(db, case["id"])
                if case["sent"] and case["data"]["consent"] and case["stage"] != "CLOSED" and not self._handoff_acknowledged(db, case["id"]):
                    self._route_case(db, case["id"], "pending handoff availability check")
                due = db.execute("SELECT * FROM consultation_jobs WHERE case_id=? AND (status='pending' OR (kind='review' AND status='running')) AND due<=? ORDER BY due", (case["id"], effective.isoformat())).fetchall()
                review_job = None
                for job in due:
                    if job["kind"] == "review":
                        lease = (effective + timedelta(minutes=2)).isoformat()
                        db.execute("UPDATE consultation_jobs SET status='running',due=? WHERE id=? AND data=?", (lease, job["id"], job["data"]))
                        review_job = job
                        continue
                    data = self._decode(job["data"])
                    if job["kind"].startswith("escalation-"):
                        if not case["sent"] or not case["data"]["consent"] or data.get("sent_revision") != case["sent"]:
                            db.execute("UPDATE consultation_jobs SET status='cancelled' WHERE id=?", (job["id"],))
                            continue
                        acknowledged = self._handoff_acknowledged(db, case["id"])
                        if not acknowledged and case["stage"] != "CLOSED":
                            if data["action"] == "Next doctor":
                                self._route_case(db, case["id"], "unacknowledged handoff escalation", alternate=True)
                            self._event(db, case["id"], "ESCALATION", {**data, "scheduled": True}, "rule")
                    elif job["kind"] == "followup" and case["stage"] != "CLOSED":
                        self._event(db, case["id"], "FOLLOWUP_DUE", {"due": job["due"], "questions": 5, "delivery": "worker inbox", "outbound_simulated": True}, "scheduler")
                    db.execute("UPDATE consultation_jobs SET status='done' WHERE id=?", (job["id"],))
            if review_job:
                try:
                    data = self._decode(review_job["data"])
                    current_case = self.case(case["id"])
                    pending_request = next((r for r in self.care_status(case["id"])["requests"] if r["status"] == "open"), None) if data.get("resume_for_request") else None
                    needs_doctor = bool(pending_request and current_case["sent"] and current_case["data"]["consent"])
                    if self.output(case["id"], "packet") is None or needs_doctor:
                        packet = self.review(case["id"], live=False, persist_workflow=bool(needs_doctor))
                        if needs_doctor and packet["verification"]["status"] == "VERIFIED":
                            from .pipeline import resume
                            resume(self.path, case["id"], "worker", {"confirmed": True, "source": "worker-confirmed communication request", "request_id": pending_request["request_id"], "fact_ids": packet["fact_ids"]})
                    with self.connect() as db:
                        db.execute("UPDATE consultation_jobs SET status='done' WHERE id=? AND data=?", (review_job["id"], review_job["data"]))
                except Exception as exc:
                    with self.connect() as db:
                        db.execute("UPDATE consultation_jobs SET status='pending',attempts=attempts+1,due=? WHERE id=? AND data=?", ((effective + timedelta(minutes=1)).isoformat(), review_job["id"], review_job["data"]))
                        self._event(db, case["id"], "BACKGROUND_REVIEW_RETRY", {"error_type": type(exc).__name__}, "scheduler")

    def cleanup_media(self, retention_days: int = 30, dry_run: bool = True) -> list[str]:
        if retention_days < 1:
            raise ValueError("Retention must be at least one day.")
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        root = (ROOT / "assets/uploaded/consultation").resolve()
        removed = []
        for case in self.list_cases():
            for event in self.events(case["id"]):
                if event["type"] not in ("PHOTO_ATTACHED", "AUDIO_ATTACHED", "COUGH_AUDIO_ATTACHED", "DOCTOR_AUDIO_ATTACHED", "GUIDANCE_AUDIO_APPROVED", "READBACK_AUDIO_CONFIRMED", "MEDICINE_IMAGE_READ") or datetime.fromisoformat(event["ts"]) >= cutoff:
                    continue
                candidate = Path(event["data"].get("path", "")).resolve()
                if candidate.is_relative_to(root) and candidate.is_file() and str(candidate) not in removed:
                    removed.append(str(candidate))
                    if not dry_run:
                        candidate.unlink()
                        self.record(case["id"], "MEDIA_RETENTION_REMOVED", {"media_event_id": event["seq"], "retention_days": retention_days}, "retention policy")
        return removed

    def notifications(self, cid: str) -> list[dict]:
        return [e for e in self.events(cid) if e["type"] in ("FOLLOWUP_DUE", "ESCALATION", "DOCTOR_SEEN", "DOCTOR_REPLY", "TASK_CREATED", "TASK_COMPLETED", "TEST_ORDERED", "TEST_RESULT_RETURNED", "TEST_UNAVAILABLE", "TEST_ORDER_CANCELLED", "HANDOFF_STARTED", "ROUTED", "ROUTING_UNAVAILABLE", *CARE_EVENTS)]

    def save_draft(self, cid: str, section: str, data: dict) -> None:
        with self.connect() as db:
            self._case(db, cid)
            db.execute("INSERT INTO consultation_drafts(case_id,section,data,updated) VALUES(?,?,?,?) ON CONFLICT(case_id,section) DO UPDATE SET data=excluded.data,updated=excluded.updated", (cid, section, self._encode(data), now()))

    def draft(self, cid: str, section: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT data FROM consultation_drafts WHERE case_id=? AND section=?", (cid, section)).fetchone()
            return self._decode(row["data"]) if row else {}

    def effort(self, cid: str) -> dict:
        from .metrics import effort
        with self.connect() as db:
            db.execute("BEGIN")
            case = self._case(db, cid)
            events = [{**dict(row), "data": self._decode(row["data"])} for row in db.execute("SELECT * FROM consultation_events WHERE case_id=? ORDER BY seq", (cid,))]
            return effort(case, events)

    def load_ayaan(self, wrong_age: bool = False) -> str:
        cid = self.create("Ayaan (fictional)", Age.from_input(14, "years" if wrong_age else "months"), "Male", False, "Synthetic village", "Urdu", True)
        items = [{"key": q["key"], "value": False} for q in danger_questions(self.age(cid), False)]
        items += [{"key": k, "value": v} for k, v in {"cough": True, "cough_days": 4, "fever": True, "fever_days": 2,
                  "vomiting": False, "diarrhoea": False, "rash": False, "drinking": True, "allergy_penicillin": False,
                  "allergies": "none reported", "medicines": ["Panadol", "Calpol"], "worker_request": "Please assess breathing and advise next steps."}.items()]
        self.add_facts(cid, items)
        for key, val in {"resp_rate": 46, "spo2": 94, "weight": 10, "temperature": 38}.items():
            self.measurement(cid, key, val, source="device")
        self.stage(cid, "REVIEW")
        return cid
