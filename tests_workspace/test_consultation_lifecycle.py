"""Regression checks for sharing, unresolved outcomes and provider failure."""
from pathlib import Path
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from unittest.mock import patch

from smartsehat_workspace.consultation.ai import extract, _bounded_json
from smartsehat_workspace.consultation.store import Store, SCHEMA, dump
from smartsehat_workspace.consultation.privacy import PREFIX


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = Store(Path(self.temp.name) / "lifecycle.db")

    def sent_case(self):
        cid = self.s.load_ayaan()
        self.s.review(cid)
        self.s.acknowledge_medcheck(cid, self.s.medcheck(cid)["version"])
        self.s.readback(cid, True, True)
        self.s.send(cid)
        return cid

    def test_consent_withdrawal_cancels_sharing_until_explicit_resend(self):
        cid = self.sent_case()
        self.s.task(cid, "spo2", "Return an oxygen reading")
        self.s.consent(cid, False)
        self.s.advance_clock(cid, 60)
        self.assertFalse(any(e["type"] == "ESCALATION" for e in self.s.events(cid)))
        self.assertIsNone(self.s.case(cid)["sent"])
        with self.assertRaises(ValueError):
            self.s.complete_task(cid, self.s.tasks(cid)[0]["task_id"], 95)
        self.s.consent(cid, True)
        with self.assertRaises(ValueError):
            self.s.reply(cid, {"advice": "Must not silently restore withdrawn sharing"})
        self.s.review(cid)
        self.s.readback(cid, True, True)
        self.s.send(cid)
        self.s.task(cid, "spo2", "New authorised task")

    def test_ongoing_followup_is_rescheduled_and_false_recovery_rejected(self):
        cid = self.sent_case()
        self.s.reply(cid, {"advice": "Synthetic doctor plan"})
        before = len(self.s.events(cid))
        with self.assertRaises(ValueError):
            self.s.followup(cid, {"condition": "worse", "drinking": "unable", "new_danger": True, "outcome": "recovered"})
        self.assertEqual(len(self.s.events(cid)), before)
        self.s.followup(cid, {"condition": "same", "drinking": "normal", "new_danger": False, "outcome": "ongoing"})
        self.assertEqual(self.s.case(cid)["stage"], "FOLLOWUP")
        resumed = Store(self.s.path)
        resumed.advance_clock(cid, 48 * 60)
        self.assertTrue(any(e["type"] == "FOLLOWUP_DUE" for e in resumed.events(cid)))
        resumed.followup(cid, {"condition": "better", "drinking": "normal", "new_danger": False, "outcome": "recovered"})
        self.assertEqual(resumed.case(cid)["stage"], "CLOSED")

    def test_medicine_acknowledgement_expires_when_evidence_changes(self):
        cid = self.s.load_ayaan()
        self.s.review(cid)
        self.s.readback(cid, True, True)
        with self.assertRaisesRegex(ValueError, "medicine safety flags"):
            self.s.send(cid)
        checked = self.s.medcheck(cid)
        self.s.acknowledge_medcheck(cid, checked["version"])
        self.s.add_fact(cid, "allergy_penicillin", True)
        with self.assertRaises(ValueError):
            self.s.acknowledge_medcheck(cid, checked["version"])
        with self.assertRaises(ValueError):
            self.s.add_fact(cid, "medicine_records", "unstructured")

    def test_unresolved_red_cannot_close_as_recovered(self):
        cid = self.sent_case()
        self.s.reply(cid, {"advice": "Synthetic doctor plan"})
        self.s.measurement(cid, "spo2", 89)
        answers = {"condition": "better", "drinking": "normal", "new_danger": False, "outcome": "recovered"}
        with self.assertRaisesRegex(ValueError, "RED warning"):
            self.s.followup(cid, answers)
        self.s.followup(cid, {**answers, "outcome": "ongoing"})
        self.assertEqual(self.s.case(cid)["stage"], "EMERGENCY")
        self.assertFalse(any(f["key"] == "followup_worse" for f in self.s.facts(cid)))

    def test_plaintext_migration_preserves_audit_and_immutability(self):
        path = Path(self.temp.name) / "legacy.db"
        with closing(sqlite3.connect(path)) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO consultation_cases(id,data,stage,created) VALUES(?,?,?,?)", ("C-old", dump({"name": "OLD_PRIVATE_MARKER_7"}), "DANGER_SCREEN", "2026-10-21"))
            db.execute("INSERT INTO consultation_events(case_id,ts,actor,type,data,payload_hash) VALUES(?,?,?,?,?,?)", ("C-old", "2026-10-21", "worker", "OLD_EVENT", dump({"name": "OLD_PRIVATE_MARKER_7"}), "original-hash"))
            db.commit()
        migrated = Store(path)
        self.assertEqual(migrated.events("C-old")[0]["payload_hash"], "original-hash")
        self.assertNotIn(b"OLD_PRIVATE_MARKER_7", path.read_bytes())
        with migrated.connect() as db:
            self.assertTrue(db.execute("SELECT data FROM consultation_events").fetchone()[0].startswith(PREFIX))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM consultation_events")

    def test_schedule_only_prescriptions_are_checked_and_readback_is_complete(self):
        cid = self.sent_case()
        self.s.medicines(cid, [{"name": "Panadol", "strength": "unknown", "formulation": "syrup", "frequency": "patient reported once", "amount": "unknown", "duration": "2 days", "adviser": "pharmacy"}], "Reported history", True, "penicillin")
        packet = self.s.review(cid)
        self.assertIn("patient reported once", packet["readbacks"]["English"]["text"])
        self.assertIn("syrup", packet["readbacks"]["English"]["text"])
        reply = {"advice": "Synthetic doctor plan", "medication_schedule": [{"medicine": "Augmentin", "dose": "synthetic doctor-entered amount", "morning": True}]}
        with self.assertRaisesRegex(ValueError, "prescription safety flags"):
            self.s.reply(cid, reply)
        self.s.reply(cid, {**reply, "safety_acknowledged": True})
        rule_ids = {flag["rule_id"] for flag in self.s.latest_reply(cid)["prescription_checks"]["flags"]}
        self.assertTrue({"MED-ALLERGY", "RX-SCHEDULE-DOSE", "RX-DOSE-PENDING"} <= rule_ids)

    def test_transcript_negation_duration_and_ambiguity(self):
        proposals, _ = extract("Cough for 4 days. Fever for 2 days. No vomiting.")
        values = {f["key"]: f["value"] for f in proposals}
        self.assertEqual(values, {"cough": True, "cough_days": 4, "fever": True, "fever_days": 2, "vomiting": False})
        proposals, _ = extract("Age 14 months. Oxygen 94. No cough or fever. Maybe vomiting?")
        values = {f["key"]: f["value"] for f in proposals}
        self.assertNotIn("cough_days", values)
        self.assertNotIn("fever_days", values)
        self.assertNotIn("fever", values)
        self.assertNotIn("vomiting", values)
        self.assertIs(values["cough"], False)

    def test_provider_wait_is_bounded_and_same_request_reused(self):
        release = threading.Event()
        calls = []
        def slow_provider():
            calls.append(True)
            release.wait(2)
            return {"facts": []}, "fixture"
        try:
            with patch.dict(os.environ, {"CONSULTATION_AI_TIMEOUT_SECONDS": "0.02"}):
                start = time.monotonic()
                for _ in range(2):
                    with self.assertRaises(TimeoutError):
                        _bounded_json("synthetic-timeout-regression", slow_provider)
                self.assertLess(time.monotonic() - start, 1)
                self.assertEqual(len(calls), 1)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
