"""Packet snapshots and durable approval of patient-language drafts."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smartsehat_workspace.consultation.clinical import Age
from smartsehat_workspace.consultation.patient import simplify_doctor_advice, guidance_confirmation_id
from smartsehat_workspace.consultation.store import Store


class ConsultationIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = Store(Path(self.temp.name) / "integrity.db")

    def sent_case(self):
        cid = self.s.load_ayaan()
        self.s.review(cid)
        self.s.acknowledge_medcheck(cid, self.s.medcheck(cid)["version"])
        self.s.readback(cid, True, True)
        self.s.send(cid)
        return cid

    def test_initial_pregnancy_is_cited_in_packet_and_full_readbacks(self):
        cid = self.s.create("Fictional maternal case", Age.from_input(25, "years"), "Female", True, "Synthetic village", "Urdu", True)
        fact = next(f for f in self.s.facts(cid) if f["key"] == "pregnant")
        packet = self.s.review(cid)
        self.assertIs(fact["value"], True)
        self.assertIn("Pregnancy: Yes", packet["full_text"])
        self.assertTrue(any(fact["id"] in row["fact_ids"] for row in packet["sections"]["Background"]))
        self.assertIn("Pregnant: present / yes", packet["readbacks"]["English"]["text"])
        self.assertIn("حاملہ", packet["readbacks"]["Urdu"]["text"])
        self.assertIn(fact["id"], packet["readbacks"]["Urdu"]["fact_ids"])
        self.assertIn("Language: Urdu", packet["compact_text"])

    def test_legacy_intake_answer_recovered_once_without_rewriting_audit(self):
        append = self.s._append
        def old_append(db, cid, key, value, **kwargs):
            return None if key == "pregnant" else append(db, cid, key, value, **kwargs)
        with patch.object(self.s, "_append", side_effect=old_append):
            cid = self.s.create("Fictional legacy case", Age.from_input(25, "years"), "Female", True, "Synthetic", "English", True)
        old_events = self.s.events(cid)
        old_facts = self.s.facts(cid)
        resumed = Store(self.s.path)
        resumed.review(cid)
        resumed.review(cid)
        self.assertEqual(resumed.events(cid)[:len(old_events)], old_events)
        self.assertEqual(resumed.facts(cid)[:len(old_facts)], old_facts)
        recovered = [e for e in resumed.events(cid) if e["type"] == "INTAKE_FACT_RECOVERED"]
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["data"]["source_event_id"], old_events[0]["seq"])
        self.assertIs(next(f for f in resumed.facts(cid) if f["key"] == "pregnant")["value"], True)

    def test_worker_request_arriving_during_packet_generation_rejects_stale_result(self):
        cid = self.sent_case()
        self.s.reply(cid, {"advice": "Synthetic doctor guidance"})
        reply = self.s.latest_reply(cid)
        from smartsehat_workspace.consultation.pipeline import review
        def changed(*args, **kwargs):
            result = review(*args, **kwargs)
            self.s.guidance_receipt(cid, reply["reply_id"], False, "spoken read-back", "Please clarify the instructions", worker_confirmed=True)
            return result
        count = len([e for e in self.s.events(cid) if e["type"] == "PACKET_COMPOSED"])
        with patch("smartsehat_workspace.consultation.pipeline.review", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "handoffs changed"):
                self.s.review(cid, persist_workflow=False)
        self.assertEqual(len([e for e in self.s.events(cid) if e["type"] == "PACKET_COMPOSED"]), count)
        self.assertIsNone(self.s.output(cid, "packet"))
        self.s.tick()
        self.assertIn("Please clarify the instructions", self.s.output(cid, "packet")["full_text"])

    def test_consent_change_and_regrant_during_review_cannot_publish_old_snapshot(self):
        cid = self.sent_case()
        from smartsehat_workspace.consultation.pipeline import review
        def changed(*args, **kwargs):
            result = review(*args, **kwargs)
            self.s.consent(cid, False)
            self.s.consent(cid, True)
            return result
        with patch("smartsehat_workspace.consultation.pipeline.review", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "sharing details changed"):
                self.s.review(cid, persist_workflow=False)
        self.assertIsNone(self.s.case(cid)["sent"])
        self.assertIsNone(self.s.output(cid, "packet"))

    def test_local_clarification_invalidates_packet_before_explicit_resend(self):
        cid = self.sent_case()
        self.s.reply(cid, {"advice": "Synthetic guidance"})
        reply = self.s.latest_reply(cid)
        self.s.consent(cid, False)
        self.s.review(cid)
        self.s.readback(cid, True, True)
        self.s.guidance_receipt(cid, reply["reply_id"], False, "spoken read-back", "Unresolved local question", worker_confirmed=True)
        self.assertIsNone(self.s.output(cid, "packet"))
        self.s.consent(cid, True)
        with self.assertRaises(ValueError):
            self.s.send(cid)
        self.s.tick()
        self.assertIn("Unresolved local question", self.s.output(cid, "packet")["full_text"])
        self.assertIsNone(self.s.case(cid)["sent"])

    def test_older_packet_without_input_revisions_requires_regeneration(self):
        cid = self.s.load_ayaan()
        packet = self.s.review(cid)
        for key in ("care_revision", "context_revision", "media_ids"):
            packet.pop(key)
        with self.s.connect() as db:
            self.s._save_output(db, cid, "packet", packet)
        self.assertIsNone(self.s.output(cid, "packet"))
        self.assertTrue(self.s.output(cid, "packet", True)["stale"])
        with self.assertRaises(ValueError):
            self.s.send(cid)
        regenerated = self.s.review(cid)
        self.assertIn("context_revision", regenerated)
        self.assertFalse(self.s.output(cid, "packet")["stale"])

    def test_wording_guard_rejects_changed_units_and_swapped_quantity_pairs(self):
        for source, rewritten in (("Synthetic amount 5 mg", "Synthetic amount 5 g"),
                                  ("Synthetic 5 mg and 10 mL", "Synthetic 5 mL and 10 mg"),
                                  ("Synthetic duration 2 days", "Synthetic duration 2 hours"),
                                  ("Synthetic reading 100 mg/dL", "Synthetic reading 100 mg")):
            with self.subTest(source=source), patch("smartsehat_workspace.consultation.patient.call_json", return_value=({"text_ur": rewritten}, "fixture")):
                result = simplify_doctor_advice(source, True)
            self.assertEqual(result["text"], source)
            self.assertIn("rejected", result["mode"])

    def test_wording_approval_survives_provenance_restart_but_expires_on_edits(self):
        cid = self.sent_case()
        advice, local = "Synthetic source advice", "فرضی ڈاکٹر کی رہنمائی"
        self.s.save_draft(cid, "doctor_reply", {"local_language": local, "guidance_proposal": {"source_hash": "fixture-source", "mode": "fixture"}})
        resumed = Store(self.s.path)
        reply = {"advice": advice, "local_language": local, "ai_guidance_used": False}
        with self.assertRaisesRegex(ValueError, "exact patient wording"):
            resumed.reply(cid, reply)
        approval = guidance_confirmation_id(cid, advice, local, resumed.review_snapshot(cid))
        approved = {**reply, "guidance_confirmed": True, "guidance_confirmation_id": approval}
        for change in ({"advice": advice + " changed"}, {"local_language": local + " تبدیل"}):
            with self.assertRaisesRegex(ValueError, "exact patient wording"):
                resumed.reply(cid, {**approved, **change})
        resumed.measurement(cid, "spo2", 95)
        with self.assertRaisesRegex(ValueError, "exact patient wording"):
            resumed.reply(cid, approved)
        approved["guidance_confirmation_id"] = guidance_confirmation_id(cid, advice, local, resumed.review_snapshot(cid))
        resumed.reply(cid, approved)
        final = resumed.latest_reply(cid)
        self.assertTrue(final["ai_guidance_used"])
        self.assertEqual(final["guidance_provenance"]["assistance"], "AI-assisted draft")
        self.assertEqual(final["guidance_provenance"]["confirmation_id"], approved["guidance_confirmation_id"])


if __name__ == "__main__":
    unittest.main()
