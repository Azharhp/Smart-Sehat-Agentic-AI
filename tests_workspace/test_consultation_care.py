"""Counselling, reported referral/contact progress and unresolved handoffs."""
from pathlib import Path
import tempfile
import unittest

from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.care import project
from smartsehat_workspace.consultation.medicines import resources
from smartsehat_workspace.consultation.pipeline import graph


class CareWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = Store(Path(self.temp.name) / "care.db")
        self.cid = self.s.load_ayaan()
        self.s.review(self.cid)
        self.s.acknowledge_medcheck(self.cid, self.s.medcheck(self.cid)["version"])
        self.s.readback(self.cid, True, True)
        self.s.send(self.cid)

    def reply(self, **values):
        self.s.reply(self.cid, {"advice": "Synthetic doctor guidance", **values})
        return self.s.latest_reply(self.cid)

    def recovered(self):
        return self.s.followup(self.cid, {"condition": "better", "drinking": "normal", "new_danger": False, "outcome": "recovered"})

    def test_counselling_clarification_returns_to_doctor_and_binds_response(self):
        reply = self.reply()
        snapshot = self.s.review_snapshot(self.cid)
        self.s.guidance_receipt(self.cid, reply["reply_id"], False, "spoken read-back", "Please clarify the return precautions", worker_confirmed=True)
        care = self.s.care_status(self.cid)
        request = care["requests"][0]
        self.assertEqual(request["status"], "open")
        self.assertEqual(self.s.case(self.cid)["stage"], "SENT")
        with self.assertRaises(ValueError):
            self.reply(reviewed_fact_ids=snapshot["fact_ids"], reviewed_media_ids=snapshot["media_ids"], reviewed_care_revision=snapshot["care_revision"])
        with self.assertRaises(ValueError):
            self.recovered()
        self.s.tick()
        packet = self.s.output(self.cid, "packet")
        self.assertIn("Please clarify the return precautions", packet["compact_text"])
        self.assertTrue(any(row.get("event_ids") for row in packet["sections"]["Request"]))
        with graph(self.s.path) as workflow:
            self.assertIn("await_doctor", workflow.get_state({"configurable": {"thread_id": self.cid}}).next)
        updated = self.reply(advice="Synthetic clarified doctor guidance", resolved_request_ids=[request["request_id"]])
        self.assertEqual(self.s.care_status(self.cid)["requests"][0]["status"], "resolved")
        with self.assertRaises(ValueError):
            self.s.guidance_receipt(self.cid, reply["reply_id"], True, "spoken read-back", worker_confirmed=True)
        self.s.guidance_receipt(self.cid, updated["reply_id"], True, "spoken read-back", worker_confirmed=True)
        self.assertEqual(len(self.s.care_status(self.cid)["guidance"]), 2)
        self.recovered()

    def test_receipt_confirmation_missing_audio_and_stale_guidance(self):
        reply = self.reply()
        with self.assertRaises(ValueError):
            self.s.guidance_receipt(self.cid, reply["reply_id"], True, "spoken read-back")
        with self.assertRaises(ValueError):
            self.s.guidance_receipt(self.cid, reply["reply_id"], None, "spoken read-back", worker_confirmed=True)
        with self.assertRaises(ValueError):
            self.s.guidance_receipt(self.cid, reply["reply_id"], True, "recorded audio", worker_confirmed=True)
        self.s.guidance_receipt(self.cid, reply["reply_id"], True, "printed card", worker_confirmed=True)
        count = len(self.s.events(self.cid))
        self.s.guidance_receipt(self.cid, reply["reply_id"], True, "printed card", worker_confirmed=True)
        self.assertEqual(count, len(self.s.events(self.cid)))
        self.s.measurement(self.cid, "spo2", 95)
        with self.assertRaises(ValueError):
            self.s.guidance_receipt(self.cid, reply["reply_id"], True, "printed card", worker_confirmed=True)

    def test_referral_progress_barrier_and_explicit_correction(self):
        facility = resources()["facilities"][0]["name"]
        self.reply(referral=facility)
        original = self.s.care_status(self.cid)["referrals"][0]
        self.reply(referral=facility)
        self.assertEqual(len(self.s.care_status(self.cid)["referrals"]), 1)
        rid = original["referral_id"]
        self.s.referral_status(self.cid, rid, "unable", "Transport unavailable")
        request = self.s.care_status(self.cid)["requests"][0]
        self.assertEqual(self.s.case(self.cid)["stage"], "SENT")
        for status in ("transport arranged", "departed", "arrived"):
            self.s.referral_status(self.cid, rid, status, "Synthetic progress report")
        with self.assertRaises(ValueError):
            self.s.referral_status(self.cid, rid, "departed", "")
        self.s.referral_status(self.cid, rid, "departed", "Worker corrects an accidental arrival entry", correction=True)
        self.s.referral_status(self.cid, rid, "arrived", "Reported arrival")
        self.reply(referral=facility, resolved_request_ids=[request["request_id"]])
        self.assertEqual(self.s.care_status(self.cid)["referrals"][0]["status"], "arrived")
        self.recovered()

    def test_emergency_referral_is_reused_and_reported_referral_stays_open(self):
        self.s.measurement(self.cid, "spo2", 89)
        self.s.emergency_referral(self.cid, "SYN-DHQ")
        self.s.emergency_referral(self.cid, "SYN-DHQ")
        self.assertEqual(len(self.s.care_status(self.cid)["referrals"]), 1)
        self.reply()
        self.s.followup(self.cid, {"condition": "same", "drinking": "normal", "new_danger": False, "outcome": "referred"})
        self.assertEqual(self.s.case(self.cid)["stage"], "EMERGENCY")

    def test_call_progress_reuses_requests_and_requires_completion(self):
        self.reply(request_call=True)
        self.reply(request_call=True)
        calls = self.s.care_status(self.cid)["calls"]
        self.assertEqual(len(calls), 1)
        with self.assertRaises(ValueError):
            self.recovered()
        self.s.call_status(self.cid, calls[0]["call_id"], "planned")
        self.s.call_status(self.cid, calls[0]["call_id"], "completed", "Worker reported contact completed")
        self.recovered()

    def test_withdrawn_sharing_keeps_barriers_local(self):
        reply = self.reply(request_call=True, referral=resources()["facilities"][0]["name"])
        state = self.s.care_status(self.cid)
        self.s.consent(self.cid, False)
        self.s.call_status(self.cid, state["calls"][0]["call_id"], "unable", "No connection")
        self.s.referral_status(self.cid, state["referrals"][0]["referral_id"], "unable", "Transport unavailable")
        self.s.guidance_receipt(self.cid, reply["reply_id"], None, "spoken read-back", "Caregiver unavailable", worker_confirmed=True)
        self.assertIsNone(self.s.case(self.cid)["sent"])
        self.s.advance_clock(self.cid, 60)
        self.assertFalse(any(e["type"] == "ESCALATION" for e in self.s.events(self.cid)))
        self.assertEqual(len(self.s.care_status(self.cid)["requests"]), 3)
        with self.assertRaises(ValueError):
            self.s.cancel_call(self.cid, state["calls"][0]["call_id"], "No longer needed")

    def test_pending_tasks_and_unavailable_requested_media(self):
        self.reply()
        self.s.task(self.cid, "photo", "Send the requested clinical photo")
        task = self.s.tasks(self.cid)[0]
        with self.assertRaises(ValueError):
            self.recovered()
        with self.assertRaises(ValueError):
            self.s.complete_task(self.cid, task["task_id"], None, " ")
        self.s.complete_task(self.cid, task["task_id"], None, "Phone camera unavailable")
        task = self.s.tasks(self.cid)[0]
        self.assertTrue(task["done"])
        self.assertEqual(task["response"]["reason"], "Phone camera unavailable")
        self.assertIn("Phone camera unavailable", self.s.output(self.cid, "packet")["full_text"])
        self.reply(advice="Doctor reviewed the unavailable photograph")
        self.recovered()

    def test_task_cancellation_keeps_test_order_consistent(self):
        self.reply(local_tests=["Glucose"])
        task = self.s.tasks(self.cid)[0]
        self.s.cancel_task(self.cid, task["task_id"], "Doctor withdrew this investigation")
        self.assertTrue(self.s.tasks(self.cid)[0]["cancelled"])
        self.assertEqual(self.s.orders(self.cid)[0]["status"], "cancelled")
        self.recovered()

    def test_legacy_handoff_events_have_stable_ids(self):
        stamp = "2026-10-21T12:00:00+00:00"
        events = [{"seq": 1, "ts": stamp, "type": "EMERGENCY_REFERRAL", "data": {"facility": {"name": "Fictional facility"}}},
                  {"seq": 2, "ts": stamp, "type": "CALL_REQUESTED", "data": {"reply_id": "old"}},
                  {"seq": 3, "ts": stamp, "type": "DOCTOR_REPLY", "data": {"advice": "Legacy synthetic advice"}}]
        state = project(events)
        self.assertEqual(state["referrals"][0]["referral_id"], "legacy-referral-1")
        self.assertEqual(state["calls"][0]["call_id"], "legacy-call-2")
        self.assertEqual(state["latest_reply"]["reply_id"], "legacy-reply-3")
        self.s.record(self.cid, "DOCTOR_REPLY", {"advice": "Legacy synthetic guidance"})
        latest = next(e for e in reversed(self.s.events(self.cid)) if e["type"] == "DOCTOR_REPLY")
        self.assertEqual(self.s.latest_reply(self.cid)["reply_id"], f"legacy-reply-{latest['seq']}")


if __name__ == "__main__":
    unittest.main()
