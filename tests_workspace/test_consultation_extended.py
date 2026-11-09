"""Connected consultation behavior added after the initial prototype audit."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from smartsehat_workspace.consultation.clinical import Age, validate_measurement
from smartsehat_workspace.consultation.store import Store, SCHEMA, dump
from smartsehat_workspace.consultation.narrative import sentence
from smartsehat_workspace.consultation.verify import verify
from smartsehat_workspace.consultation.pipeline import graph
from smartsehat_workspace.consultation.privacy import save_media, read_media, PREFIX
from smartsehat_workspace.consultation.patient import simplify_doctor_advice
from smartsehat_workspace.consultation.fhir import bundle


class CompleteConsultationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = Store(Path(self.temp.name) / "flow.db")

    def sent_case(self):
        cid = self.s.load_ayaan()
        self.s.review(cid)
        self.s.acknowledge_medcheck(cid, self.s.medcheck(cid)["version"])
        self.s.readback(cid, True, True)
        self.s.send(cid)
        return cid

    def test_full_packet_readback_and_graph_gates(self):
        cid = self.s.load_ayaan()
        packet = self.s.review(cid)
        self.assertEqual(set(packet["sections"]), {"Situation", "Background", "Assessment", "Request"})
        self.assertEqual(set(packet["readbacks"]["English"]["fact_ids"]), {f["id"] for f in self.s.facts(cid) if f["key"] != "transcript"})
        self.assertTrue(packet["considerations"])
        self.assertIn("Lower respiratory infection", " ".join(c["title"] for c in packet["considerations"]))
        for c in packet["considerations"]:
            self.assertEqual(verify(c["evidence_for"] + c["evidence_against"], self.s.facts(cid))["status"], "VERIFIED")
        with graph(self.s.path) as workflow:
            self.assertIn("worker_confirmation", workflow.get_state({"configurable": {"thread_id": cid}}).next)
        self.s.acknowledge_medcheck(cid, self.s.medcheck(cid)["version"])
        self.s.readback(cid, True, True)
        self.s.send(cid)
        with graph(self.s.path) as workflow:
            self.assertIn("await_doctor", workflow.get_state({"configurable": {"thread_id": cid}}).next)
        self.s.reply(cid, {"advice": "Synthetic doctor advice"})
        with graph(self.s.path) as workflow:
            self.assertEqual(workflow.get_state({"configurable": {"thread_id": cid}}).next, ())

    def test_patient_rejection_cannot_send_same_version(self):
        cid = self.s.load_ayaan()
        packet = self.s.review(cid)
        self.s.record(cid, "READBACK_REJECTED", {"packet_version": packet["version"], "reason": "History duration is incorrect"})
        with self.assertRaises(ValueError):
            self.s.readback(cid, True, True)
        self.s.add_fact(cid, "cough_days", 5)
        self.s.review(cid)
        self.s.acknowledge_medcheck(cid, self.s.medcheck(cid)["version"])
        self.s.readback(cid, True, True)
        self.s.send(cid)

    def test_structured_medicines_and_typed_tests(self):
        cid = self.s.load_ayaan()
        self.s.medicines(cid, [{"name": "Panadol", "strength": "as printed", "amount": "patient-reported amount", "duration": "2 days", "adviser": "pharmacy"}, {"name": "Calpol", "adviser": "self"}], "Synthetic reported history", False, "none reported")
        self.assertIn("MED-DUP", {f["rule_id"] for f in self.s.medcheck(cid)["flags"]})
        with self.assertRaises(ValueError):
            self.s.test_result(cid, "glucose", 100, "days")
        self.s.test_result(cid, "glucose", 100, "mg/dL")
        glucose = next(f for f in self.s.facts(cid) if f["key"] == "glucose")
        self.assertEqual(glucose["value"]["unit"], "mg/dL")
        with self.assertRaises(ValueError):
            self.s.add_fact(cid, "glucose", 100)
        self.assertFalse(validate_measurement("spo2", 94, self.s.age(cid), "/min")["ok"])

    def test_new_doctor_tasks_validate_then_commit_once(self):
        cid = self.sent_case()
        self.s.task(cid, "symptom_course", "Is the child getting worse?")
        task = self.s.tasks(cid)[0]
        with self.assertRaises(ValueError):
            self.s.complete_task(cid, task["task_id"], "invalid choice")
        self.assertFalse(self.s.tasks(cid)[0]["done"])
        self.s.complete_task(cid, task["task_id"], "unchanged")
        self.assertTrue(self.s.tasks(cid)[0]["done"])
        with self.assertRaises(ValueError):
            self.s.complete_task(cid, task["task_id"], "worsening")
        self.assertEqual(len([e for e in self.s.events(cid) if e["type"] == "TASK_COMPLETED"]), 1)
        self.s.task(cid, "glucose", "Measure glucose")
        task = self.s.tasks(cid)[-1]
        self.s.complete_task(cid, task["task_id"], {"value": 100, "unit": "mg/dL"})
        self.s.task(cid, "free_text", "Clarify the reported concern")
        task = self.s.tasks(cid)[-1]
        with self.assertRaises(ValueError):
            self.s.complete_task(cid, task["task_id"], "")
        self.s.complete_task(cid, task["task_id"], "", "Patient unavailable")
        self.assertIsNone(next(f["value"] for f in self.s.facts(cid) if f["key"] == "free_text"))
        self.s.task(cid, "symptom_severity", "Confirm reported severity")
        self.s.complete_task(cid, self.s.tasks(cid)[-1]["task_id"], "unknown")
        self.assertIsNone(next(f["value"] for f in self.s.facts(cid) if f["key"] == "symptom_severity"))

    def test_scheduling_survives_restart_and_is_idempotent(self):
        cid = self.sent_case()
        self.s.reply(cid, {"advice": "Synthetic plan"})
        resumed = Store(self.s.path)
        resumed.advance_clock(cid, 48 * 60)
        resumed.tick()
        self.assertEqual(len([e for e in resumed.events(cid) if e["type"] == "FOLLOWUP_DUE"]), 1)
        resumed.followup(cid, {"condition": "worse", "drinking": "unable", "new_danger": True, "outcome": "referred"})
        self.assertEqual(resumed.output(cid, "urgency")["level"], "RED")

    def test_ai_concern_cannot_lower_and_depends_on_confirmed_facts(self):
        cid = self.s.load_ayaan()
        cough = next(f for f in self.s.facts(cid) if f["key"] == "cough")
        result = {"considerations": [{"consideration_id": "respiratory_infection", "fact_ids": [cough["id"]]}], "concern": "RED", "concern_fact_ids": [cough["id"]]}
        with patch("smartsehat_workspace.consultation.reasoning.call_json", return_value=(result, "synthetic concern fixture")):
            self.s.review(cid)
        self.assertEqual(self.s.output(cid, "urgency")["level"], "RED")
        self.s.add_fact(cid, "allergies", "none reported")
        self.assertEqual(self.s.output(cid, "urgency")["level"], "RED")
        self.s.add_fact(cid, "cough", False)
        self.assertIsNone(self.s.output(cid, "ai_concern"))
        self.assertEqual(self.s.output(cid, "urgency")["level"], "AMBER")

    def test_payload_checkpoint_and_media_encryption(self):
        cid = self.s.create("PRIVATE_SYNTHETIC_NAME_X9", Age.from_input(14, "months"), "Male", False, "Synthetic", "Urdu", True)
        self.s.review(cid)
        self.assertNotIn(b"PRIVATE_SYNTHETIC_NAME_X9", Path(self.s.path).read_bytes())
        self.assertNotIn(b"PRIVATE_SYNTHETIC_NAME_X9", Path(self.s.path + ".workflow.db").read_bytes())
        with self.s.connect() as db:
            self.assertTrue(db.execute("SELECT data FROM consultation_cases WHERE id=?", (cid,)).fetchone()[0].startswith(PREFIX))
        target = Path(self.temp.name) / "image.jpg"
        encrypted = save_media(target, b"synthetic-media-content")
        self.assertNotIn(b"synthetic-media-content", encrypted.read_bytes())
        self.assertEqual(read_media(encrypted), b"synthetic-media-content")
        self.assertEqual(Store(self.s.path).case(cid)["data"]["name"], "PRIVATE_SYNTHETIC_NAME_X9")

    def test_guarded_wording_and_fhir_references(self):
        with patch("smartsehat_workspace.consultation.patient.call_json", return_value=({"text_ur": "changed to 50 mg"}, "fixture")):
            draft = simplify_doctor_advice("Original doctor amount 5 mg", True)
        self.assertEqual(draft["text"], "Original doctor amount 5 mg")
        cid = self.sent_case()
        doc = bundle(self.s.case(cid), self.s.facts(cid), self.s.output(cid, "packet"), self.s.tasks(cid), None)
        resources = {entry["resource"]["resourceType"] + "/" + entry["resource"]["id"] for entry in doc["entry"]}
        self.assertEqual(doc["entry"][0]["resource"]["resourceType"], "Composition")
        def check(value):
            if isinstance(value, dict):
                if "reference" in value and "/" in value["reference"]:
                    self.assertIn(value["reference"], resources)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(doc)


if __name__ == "__main__":
    unittest.main()
