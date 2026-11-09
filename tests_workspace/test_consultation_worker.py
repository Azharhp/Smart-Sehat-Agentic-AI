"""Cross-case work recovery and urgent updates outside the final-plan gate."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.pipeline import graph


class WorkerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = Store(Path(self.temp.name) / "worker.db")

    def sent_case(self):
        cid = self.s.load_ayaan()
        self.s.review(cid)
        self.s.acknowledge_medcheck(cid, self.s.medcheck(cid)["version"])
        self.s.readback(cid, True, True)
        self.s.send(cid)
        return cid

    def test_urgent_update_while_doctor_awaits_tests_reopens_review(self):
        cid = self.sent_case()
        self.s.reply(cid, {"advice": "Synthetic interim plan", "status": "awaiting_information", "local_tests": ["Glucose"]})
        self.s.safety_update(cid, new_danger=True, notes="Caregiver reports a new danger", worker_confirmed=True)
        self.assertEqual(self.s.case(cid)["stage"], "EMERGENCY")
        self.assertEqual(self.s.output(cid, "urgency")["level"], "RED")
        self.assertFalse(self.s.tasks(cid)[0]["done"])
        self.s.tick()
        self.assertIn("Caregiver reports a new danger", self.s.output(cid, "packet")["full_text"])
        with graph(self.s.path) as workflow:
            self.assertIn("await_doctor", workflow.get_state({"configurable": {"thread_id": cid}}).next)
        # Repeated explicit reports refresh review without losing the SENT/RED stage.
        self.s.safety_update(cid, new_danger=True, notes="Caregiver reports a new danger", worker_confirmed=True)
        self.assertEqual(self.s.case(cid)["stage"], "EMERGENCY")
        self.assertEqual(len(self.s.care_status(cid)["requests"]), 1)
        self.assertEqual(len([e for e in self.s.events(cid) if e["type"] == "SAFETY_UPDATE"]), 2)

    def test_urgent_reports_never_reactivate_withdrawn_handoff(self):
        cid = self.sent_case()
        self.s.reply(cid, {"advice": "Synthetic final guidance"})
        self.s.consent(cid, False)
        self.s.safety_update(cid, worse=True, worker_confirmed=True)
        self.assertIsNone(self.s.case(cid)["sent"])
        self.assertEqual(self.s.case(cid)["stage"], "REVIEW")
        self.s.consent(cid, True)
        self.s.followup(cid, {"condition": "worse", "outcome": "ongoing"})
        self.s.safety_update(cid, unable_drink=True, worker_confirmed=True)
        self.assertIsNone(self.s.case(cid)["sent"])
        self.assertEqual(self.s.case(cid)["stage"], "EMERGENCY")
        self.s.advance_clock(cid, 60)
        self.assertFalse(any(e["type"] == "ESCALATION" for e in self.s.events(cid)))
        self.assertFalse(self.s.worker_worklist()[0]["sharing_active"])

    def test_action_list_prioritises_red_due_followup_and_tasks_after_restart(self):
        task_case = self.sent_case()
        self.s.reply(task_case, {"advice": "Synthetic guidance"})
        self.s.task(task_case, "photo", "Return the requested photograph")
        due_case = self.sent_case()
        self.s.reply(due_case, {"advice": "Synthetic final guidance"})
        red_case = self.sent_case()
        self.s.safety_update(red_case, new_danger=True, worker_confirmed=True)
        resumed = Store(self.s.path)
        future = datetime.now(timezone.utc) + timedelta(hours=49)
        with patch("smartsehat_workspace.consultation.worklist.current_time", return_value=future):
            rows = resumed.worker_worklist()
        self.assertEqual(rows[0]["case_id"], red_case)
        due = next(row for row in rows if row["case_id"] == due_case)
        self.assertTrue(due["followup_due"])
        self.assertIn("Follow-up due", due["actions"])
        self.assertEqual(next(row for row in rows if row["case_id"] == task_case)["pending_tasks"], 1)
        resumed.cancel_task(task_case, resumed.tasks(task_case)[0]["task_id"], "Synthetic doctor cancellation")
        self.assertEqual(next(row for row in resumed.worker_worklist() if row["case_id"] == task_case)["pending_tasks"], 0)
        resumed.advance_clock(due_case, 49 * 60)
        self.assertTrue(next(row for row in resumed.worker_worklist() if row["case_id"] == due_case)["followup_due"])
        resumed.followup(due_case, {"condition": "better", "drinking": "normal", "new_danger": False, "outcome": "recovered"})
        self.assertNotIn(due_case, [row["case_id"] for row in resumed.worker_worklist()])

    def test_unconfirmed_safety_selections_do_not_change_facts(self):
        cid = self.s.load_ayaan()
        before = self.s.events(cid)
        for data in ({"worse": True}, {"new_danger": "false", "worker_confirmed": True}, {"worker_confirmed": True}):
            with self.assertRaises(ValueError):
                self.s.safety_update(cid, **data)
        self.assertEqual(self.s.events(cid), before)
        self.s.safety_update(cid, worse=True, worker_confirmed=True)
        self.assertEqual(self.s.case(cid)["stage"], "REVIEW")
        self.assertIsNone(self.s.case(cid)["sent"])


if __name__ == "__main__":
    unittest.main()
