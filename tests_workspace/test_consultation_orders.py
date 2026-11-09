"""Doctor orders, provisional care and portable recorded patient guidance."""
from __future__ import annotations
import base64
from datetime import date, timedelta
import io
from pathlib import Path
import tempfile
import unittest
import wave

from smartsehat_workspace.consultation.clinical import ROOT
from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.pipeline import graph
from smartsehat_workspace.consultation.privacy import save_media
from smartsehat_workspace.consultation.exports import patient_card


class DoctorOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = Store(Path(self.temp.name) / "orders.db")
        self.cid = self.s.load_ayaan()
        self.s.review(self.cid)
        self.s.acknowledge_medcheck(self.cid, self.s.medcheck(self.cid)["version"])
        self.s.readback(self.cid, True, True)
        self.s.send(self.cid)

    def upload(self, name: str, data: bytes) -> Path:
        folder = ROOT / "assets/uploaded/consultation" / self.cid
        folder.mkdir(parents=True, exist_ok=True)
        path = save_media(folder / name, data)
        def clean():
            path.unlink(missing_ok=True)
            if folder.is_relative_to(ROOT / "assets/uploaded/consultation") and not any(folder.iterdir()):
                folder.rmdir()
        self.addCleanup(clean)
        return path

    def test_local_order_result_provisional_then_final_and_no_duplicate(self):
        self.s.reply(self.cid, {"advice": "Synthetic interim advice", "status": "awaiting_information", "local_tests": ["Glucose"]})
        self.assertEqual(self.s.case(self.cid)["stage"], "SENT")
        self.assertIsNone(self.s.latest_reply(self.cid)["followup_due"])
        with graph(self.s.path) as workflow:
            self.assertIn("await_doctor", workflow.get_state({"configurable": {"thread_id": self.cid}}).next)
        order = self.s.orders(self.cid)[0]
        with self.assertRaises(ValueError):
            self.s.complete_task(self.cid, order["task_id"], {"value": 100, "unit": "days"})
        self.assertEqual(self.s.orders(self.cid)[0]["status"], "pending")
        self.s.complete_task(self.cid, order["task_id"], {"value": 100, "unit": "mg/dL"})
        result = self.s.orders(self.cid)[0]
        self.assertEqual(result["status"], "result returned")
        self.assertFalse(result["doctor_reviewed"])
        with self.assertRaises(ValueError):
            self.s.followup(self.cid, {"outcome": "ongoing"})
        self.s.reply(self.cid, {"advice": "Synthetic final advice after reviewing the result", "local_tests": ["Glucose"], "reviewed_order_ids": [order["order_id"]]})
        self.assertEqual(len(self.s.orders(self.cid)), 1)
        self.assertEqual(len(self.s.tasks(self.cid)), 1)
        self.assertTrue(self.s.orders(self.cid)[0]["doctor_reviewed"])
        self.assertIsNotNone(self.s.latest_reply(self.cid)["followup_due"])
        with graph(self.s.path) as workflow:
            self.assertEqual(workflow.get_state({"configurable": {"thread_id": self.cid}}).next, ())

    def test_laboratory_report_and_attachment_preserve_the_issued_result(self):
        self.s.reply(self.cid, {"advice": "Synthetic report request", "status": "awaiting_information", "lab_tests": ["Blood count"]})
        order = self.s.orders(self.cid)[0]
        with self.assertRaises(ValueError):
            self.s.complete_task(self.cid, order["task_id"], {"result": "Synthetic report", "report_date": str(date.today() + timedelta(days=1))})
        report = self.upload("synthetic-report.pdf", b"%PDF-1.4\nsynthetic transport fixture")
        self.s.complete_task(self.cid, order["task_id"], {"result": "SYNTHETIC LAB TEXT: retain the issued wording exactly", "laboratory": "Fictional lab", "report_date": str(date.today()), "attachment_path": str(report)})
        result = self.s.orders(self.cid)[0]
        self.assertEqual(result["value"]["result"], "SYNTHETIC LAB TEXT: retain the issued wording exactly")
        self.assertIn("media_event_id", result["value"])
        packet = self.s.output(self.cid, "packet")
        self.assertIn("SYNTHETIC LAB TEXT", packet["full_text"])
        self.assertTrue(any(m.get("order_id") == order["order_id"] for m in packet["appendix"]["media"]))

    def test_explicit_cancellation_and_repeat_request(self):
        self.s.reply(self.cid, {"advice": "Synthetic test order", "local_tests": ["Glucose"]})
        original = self.s.orders(self.cid)[0]
        self.s.cancel_order(self.cid, original["order_id"], "Synthetic doctor cancellation")
        self.assertTrue(self.s.tasks(self.cid)[0]["cancelled"])
        with self.assertRaises(ValueError):
            self.s.complete_task(self.cid, original["task_id"], {"value": 100, "unit": "mg/dL"})
        self.s.reply(self.cid, {"advice": "Review the recorded cancelled order", "local_tests": ["Glucose"]})
        self.assertEqual(len(self.s.orders(self.cid)), 1)
        self.s.reply(self.cid, {"advice": "Synthetic explicit repeat", "local_tests": ["Glucose"], "repeat_tests": True})
        self.assertEqual(len(self.s.orders(self.cid)), 2)
        self.assertEqual(self.s.orders(self.cid)[-1]["status"], "pending")

    def test_unavailable_test_keeps_reason_and_requires_doctor_review(self):
        self.s.reply(self.cid, {"advice": "Synthetic local test request", "status": "awaiting_information", "local_tests": ["Malaria RDT"]})
        order = self.s.orders(self.cid)[0]
        self.s.complete_task(self.cid, order["task_id"], {"result": None}, "No test kit available")
        current = self.s.orders(self.cid)[0]
        self.assertEqual(current["status"], "unavailable")
        self.assertEqual(current["reason"], "No test kit available")
        self.assertIsNone(current["value"])
        self.s.reply(self.cid, {"advice": "Synthetic doctor reviewed unavailable test", "reviewed_order_ids": [order["order_id"]]})
        self.assertTrue(self.s.orders(self.cid)[0]["doctor_reviewed"])

    def test_recorded_audio_needs_approval_and_is_embedded_for_offline_playback(self):
        content = io.BytesIO()
        with wave.open(content, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\0\0" * 800)
        audio = content.getvalue()
        path = self.upload("synthetic-guidance.wav", audio)
        with self.assertRaises(ValueError):
            self.s.reply(self.cid, {"advice": "Synthetic written guidance", "guidance_audio_path": str(path)})
        self.s.reply(self.cid, {"advice": "Synthetic written guidance", "guidance_audio_path": str(path), "guidance_audio_confirmed": True})
        reply = self.s.latest_reply(self.cid)
        html = patient_card("Fictional patient", reply, audio)
        self.assertIn("<audio controls", html)
        self.assertIn(base64.b64encode(audio).decode("ascii"), html)
        self.assertNotIn(str(path), html)
        self.assertNotIn("<audio controls", patient_card("Fictional", {**reply, "guidance_audio_confirmed": False}, audio))

    def test_background_recomposition_preserves_the_active_doctor_gate(self):
        self.s.reply(self.cid, {"advice": "Synthetic pending response", "status": "awaiting_information", "local_tests": ["Glucose"]})
        self.s.tick()
        self.assertIsNotNone(self.s.output(self.cid, "packet"))
        with graph(self.s.path) as workflow:
            self.assertIn("await_doctor", workflow.get_state({"configurable": {"thread_id": self.cid}}).next)
        self.assertEqual(self.s.case(self.cid)["stage"], "SENT")

    def test_recovery_cannot_hide_a_pending_order(self):
        self.s.reply(self.cid, {"advice": "Synthetic plan with pending investigation", "local_tests": ["Glucose"]})
        answers = {"condition": "better", "drinking": "normal", "new_danger": False, "outcome": "recovered"}
        with self.assertRaisesRegex(ValueError, "tests or returned results"):
            self.s.followup(self.cid, answers)
        self.s.cancel_order(self.cid, self.s.orders(self.cid)[0]["order_id"], "Doctor explicitly cancelled the outstanding test")
        self.s.followup(self.cid, answers)
        self.assertEqual(self.s.case(self.cid)["stage"], "CLOSED")

    def test_unknown_test_and_cross_case_media_are_rejected(self):
        with self.assertRaises(ValueError):
            self.s.reply(self.cid, {"advice": "Synthetic", "local_tests": ["Unsupported test"]})
        path = Path(self.temp.name) / "unrelated.wav"
        path.write_bytes(b"synthetic")
        with self.assertRaises(ValueError):
            self.s.reply(self.cid, {"advice": "Synthetic", "guidance_audio_path": str(path), "guidance_audio_confirmed": True})

    def test_worker_media_updates_require_a_fresh_doctor_review(self):
        original = self.s.review_snapshot(self.cid)
        path = self.upload("synthetic-photo.png", b"synthetic transport fixture")
        self.s.record(self.cid, "PHOTO_ATTACHED", {"path": str(path), "site": "synthetic"})
        with self.assertRaisesRegex(ValueError, "photos/audio"):
            self.s.reply(self.cid, {"advice": "Old snapshot", "reviewed_fact_ids": original["fact_ids"], "reviewed_media_ids": original["media_ids"]})
        current = self.s.review_snapshot(self.cid)
        self.s.reply(self.cid, {"advice": "Current reviewed snapshot", "reviewed_fact_ids": current["fact_ids"], "reviewed_media_ids": current["media_ids"]})
        self.s.record(self.cid, "AUDIO_ATTACHED", {"path": str(path), "experimental": True})
        with self.assertRaisesRegex(ValueError, "photos/audio"):
            self.s.followup(self.cid, {"condition": "better", "drinking": "normal", "new_danger": False, "outcome": "recovered"})


if __name__ == "__main__":
    unittest.main()
