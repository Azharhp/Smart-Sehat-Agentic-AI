"""Integrated worker/doctor UI and saved draft checks."""
import os
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from smartsehat_workspace.consultation.store import Store


class ConsultationUITests(unittest.TestCase):
    def test_unsent_view_guidance_and_doctor_queue_focus_after_handoff(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CONSULTATION_DB": str(Path(tmp) / "views.db"), "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            service = Store(Path(tmp) / "views.db")
            older = service.load_ayaan()
            service.review(older)
            service.acknowledge_medcheck(older, service.medcheck(older)["version"])
            service.readback(older, True, True)
            service.send(older)
            current = service.load_ayaan()
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "smartsehat_workspace/app.py"), default_timeout=25)
            app.query_params["case"] = current
            app.run()
            next(r for r in app.radio if r.label == "Workflow").set_value("Split-screen demo").run()
            self.assertEqual(list(app.exception), [])
            self.assertTrue(any("Split-screen demo" in title.value for title in app.title))
            self.assertTrue(any("This case has not been sent yet" in item.value for item in app.info))
            self.assertTrue(any("Worker phone" in item.value for item in app.markdown))
            next(r for r in app.radio if r.label == "Workflow").set_value("Doctor console").run()
            self.assertTrue(any(current in item.value and "has not been sent" in item.value for item in app.info))
            self.assertEqual(next(s for s in app.selectbox if s.label == "Doctor queue · urgency then arrival time").value, older)
            next(b for b in app.button if b.label == "Open worker review for this case").click().run()
            self.assertEqual(next(r for r in app.radio if r.label == "Workflow").value, "Consultation")
            self.assertEqual(app.query_params["case"], [current])
            next(b for b in app.button if b.label == "Generate verified case packet").click().run()
            for label in ("Patient/caregiver confirms the read-back", "Worker confirms the full packet and consent to send", "I reviewed the medicine safety flags and will include them in the doctor handoff"):
                next(c for c in app.checkbox if c.label == label).check().run()
            next(b for b in app.button if b.label == "Confirm read-back and send to doctor").click().run()
            next(r for r in app.radio if r.label == "Workflow").set_value("Doctor console").run()
            self.assertEqual(list(app.exception), [])
            self.assertEqual(next(s for s in app.selectbox if s.label == "Doctor queue · urgency then arrival time").value, current)

    def test_dob_mismatch_requires_confirmation_and_preserves_source_dates(self):
        from smartsehat_workspace.consultation.timeutils import local_today
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CONSULTATION_DB": str(Path(tmp) / "dob.db"), "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            source = "from smartsehat_workspace.consultation.ui import _intake\n_intake()\n"
            app = AppTest.from_string(source, default_timeout=25).run()
            next(c for c in app.checkbox if c.label == "Use date of birth instead").check().run()
            dob = local_today() - timedelta(days=59)
            next(d for d in app.date_input if d.label == "Date of birth").set_value(dob).run()
            self.assertTrue(next(b for b in app.button if b.label == "Create consultation").disabled)
            next(c for c in app.checkbox if c.label == "I confirmed the date of birth and calculated age").check().run()
            next(b for b in app.button if b.label == "Create consultation").click().run()
            self.assertEqual(list(app.exception), [])
            service = Store(Path(tmp) / "dob.db")
            cid = service.list_cases()[0]["id"]
            self.assertEqual(service.age(cid).date_of_birth, dob.isoformat())
            self.assertEqual(service.age(cid).as_of, local_today().isoformat())
            self.assertEqual(service.age(cid).days, 59)

    def test_doctor_measurement_tasks_have_timer_units_and_saved_drafts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CONSULTATION_DB": str(Path(tmp) / "measured.db"), "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            service = Store(Path(tmp) / "measured.db")
            cid = service.load_ayaan()
            service.review(cid)
            service.acknowledge_medcheck(cid, service.medcheck(cid)["version"])
            service.readback(cid, True, True)
            service.send(cid)
            service.task(cid, "resp_rate", "Count a full minute")
            service.task(cid, "temperature", "Return temperature and device unit")
            tasks = {task["key"]: task["task_id"] for task in service.tasks(cid)}
            before = service.facts(cid)
            result = {"case_id": f"{cid}:{tasks['resp_rate']}", "count": 46, "complete": True, "duration_seconds": 60}
            source = f"from smartsehat_workspace.consultation.ui import _tasks\n_tasks({cid!r})\n"
            with patch("smartsehat_workspace.consultation.ui.breath_counter", return_value=result):
                app = AppTest.from_string(source, default_timeout=25).run()
                next(b for b in app.button if b.label == "Use completed breath count for this task").click().run()
                self.assertEqual(app.text_input(key="task_value_" + tasks["resp_rate"]).value, "46")
                app.text_input(key="task_value_" + tasks["temperature"]).set_value("102.2").run()
                app.selectbox(key="task_unit_" + tasks["temperature"]).set_value("F").run()
                self.assertEqual(service.facts(cid), before)
                restarted = AppTest.from_string(source, default_timeout=25).run()
                self.assertEqual(restarted.text_input(key="task_value_" + tasks["resp_rate"]).value, "46")
                self.assertEqual(restarted.selectbox(key="task_unit_" + tasks["temperature"]).value, "F")
                restarted.button(key="task_done_" + tasks["resp_rate"]).click().run()
                restarted.button(key="task_done_" + tasks["temperature"]).click().run()
                self.assertEqual(list(restarted.exception), [])
            self.assertTrue(all(task["done"] for task in service.tasks(cid)))
            response = next(t for t in service.tasks(cid) if t["key"] == "resp_rate")["response"]["value"]
            self.assertEqual(response["duration_seconds"], 60)
            self.assertEqual(next(f for f in service.facts(cid) if f["key"] == "temperature")["value"], 39)

    def test_patient_wording_approval_resets_after_edit_and_browser_restart(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CONSULTATION_DB": str(Path(tmp) / "wording.db"), "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            service = Store(Path(tmp) / "wording.db")
            cid = service.load_ayaan()
            service.review(cid)
            service.acknowledge_medcheck(cid, service.medcheck(cid)["version"])
            service.readback(cid, True, True)
            service.send(cid)
            source = f"from smartsehat_workspace.consultation.ui import _doctor_case\n_doctor_case({cid!r})\n"
            app = AppTest.from_string(source, default_timeout=25).run()
            next(t for t in app.text_area if t.label == "Doctor advice").set_value("Synthetic source advice").run()
            next(b for b in app.button if b.label == "Draft patient-friendly wording from my advice").click().run()
            self.assertEqual(list(app.exception), [])
            label = "I verified that the patient guidance preserves my clinical instructions"
            self.assertFalse(next(c for c in app.checkbox if c.label == label).value)
            next(c for c in app.checkbox if c.label == label).check().run()
            next(t for t in app.text_area if t.label == "Doctor advice").set_value("Synthetic updated source advice").run()
            self.assertFalse(next(c for c in app.checkbox if c.label == label).value)
            next(t for t in app.text_area if t.label == "Patient guidance in Urdu (doctor authored)").set_value("Synthetic updated source advice").run()
            next(c for c in app.checkbox if c.label == label).check().run()
            restarted = AppTest.from_string(source, default_timeout=25).run()
            self.assertFalse(next(c for c in restarted.checkbox if c.label == label).value)
            self.assertIn("guidance_proposal", service.draft(cid, "doctor_reply"))
            next(c for c in restarted.checkbox if c.label == label).check().run()
            next(b for b in restarted.button if b.label == "Save simulated doctor response").click().run()
            self.assertEqual(list(restarted.exception), [])
            reply = service.latest_reply(cid)
            self.assertEqual(reply["local_language"], "Synthetic updated source advice")
            self.assertEqual(reply["guidance_provenance"]["assistance"], "AI-assisted draft")

    def test_task_and_followup_drafts_survive_new_worker_session(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CONSULTATION_DB": str(Path(tmp) / "drafts.db"), "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            service = Store(Path(tmp) / "drafts.db")
            cid = service.load_ayaan()
            service.review(cid)
            service.acknowledge_medcheck(cid, service.medcheck(cid)["version"])
            service.readback(cid, True, True)
            service.send(cid)
            service.reply(cid, {"advice": "Synthetic final doctor guidance"})
            for key in ("spo2", "glucose", "malaria_rdt", "cough", "cough_days", "free_text"):
                service.task(cid, key, "Synthetic request: " + key)
            tasks = {task["key"]: task["task_id"] for task in service.tasks(cid)}
            before = service.facts(cid)
            source = f'''import streamlit as st
from smartsehat_workspace.consultation.ui import _tasks, _patient, _safety_update
_safety_update({cid!r})
view = st.radio("Draft view", ["Tasks", "Patient"])
if view == "Tasks":
    _tasks({cid!r})
else:
    _patient({cid!r})
'''
            app = AppTest.from_string(source, default_timeout=25).run()
            app.text_input(key="task_value_" + tasks["spo2"]).set_value("96").run()
            app.text_input(key="task_poc_value_" + tasks["glucose"]).set_value("5.5").run()
            app.selectbox(key="task_poc_unit_" + tasks["glucose"]).set_value("mmol/L").run()
            app.selectbox(key="task_poc_choice_" + tasks["malaria_rdt"]).set_value("negative").run()
            app.radio(key="task_answer_" + tasks["cough"]).set_value("No").run()
            app.number_input(key="task_number_" + tasks["cough_days"]).set_value(0.0).run()
            app.text_area(key="task_text_" + tasks["free_text"]).set_value("Unsubmitted worker detail").run()
            app.text_input(key="task_reason_" + tasks["free_text"]).set_value("Saved draft reason").run()
            self.assertEqual(list(app.exception), [])
            resumed = AppTest.from_string(source, default_timeout=25).run()
            self.assertEqual(resumed.text_input(key="task_value_" + tasks["spo2"]).value, "96")
            self.assertEqual(resumed.text_input(key="task_poc_value_" + tasks["glucose"]).value, "5.5")
            self.assertEqual(resumed.selectbox(key="task_poc_unit_" + tasks["glucose"]).value, "mmol/L")
            self.assertEqual(resumed.selectbox(key="task_poc_choice_" + tasks["malaria_rdt"]).value, "negative")
            self.assertEqual(resumed.radio(key="task_answer_" + tasks["cough"]).value, "No")
            self.assertEqual(resumed.number_input(key="task_number_" + tasks["cough_days"]).value, 0.0)
            self.assertEqual(resumed.text_area(key="task_text_" + tasks["free_text"]).value, "Unsubmitted worker detail")
            self.assertEqual(resumed.text_input(key="task_reason_" + tasks["free_text"]).value, "Saved draft reason")
            self.assertEqual(service.facts(cid), before)
            self.assertFalse(any(task["done"] for task in service.tasks(cid)))
            next(r for r in resumed.radio if r.label == "Draft view").set_value("Patient").run()
            for label, value in (("Compared with before", "same"), ("Drinking/eating", "less"), ("Went to the referred facility", "No"), ("New danger sign reported", "No")):
                next(r for r in resumed.radio if r.label == label).set_value(value).run()
            restarted = AppTest.from_string(source, default_timeout=25).run()
            next(r for r in restarted.radio if r.label == "Draft view").set_value("Patient").run()
            self.assertEqual(next(r for r in restarted.radio if r.label == "Compared with before").value, "same")
            self.assertEqual(next(r for r in restarted.radio if r.label == "Drinking/eating").value, "less")
            self.assertEqual(next(r for r in restarted.radio if r.label == "Went to the referred facility").value, "No")
            self.assertEqual(next(r for r in restarted.radio if r.label == "New danger sign reported").value, "No")
            self.assertFalse(any(e["type"] == "FOLLOWUP" for e in service.events(cid)))
            service.reply(cid, {"advice": "Synthetic interim guidance", "status": "awaiting_information"})
            restarted.run()
            next(c for c in restarted.checkbox if c.label == "New danger sign observed or reported").check().run()
            next(c for c in restarted.checkbox if c.label == "Worker confirms this reported change").check().run()
            next(b for b in restarted.button if b.label == "Report urgent change now").click().run()
            self.assertEqual(list(restarted.exception), [])
            self.assertEqual(service.case(cid)["stage"], "EMERGENCY")

    def test_worker_to_doctor_and_preserved_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("CONSULTATION_DB")
            os.environ["CONSULTATION_DB"] = str(Path(tmp) / "ui.db")
            try:
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "smartsehat_workspace/app.py"), default_timeout=25).run()
                self.assertEqual(list(app.exception), [])
                next(b for b in app.button if b.label == "Load fictional Ayaan").click().run()
                self.assertEqual(list(app.exception), [])
                next(s for s in app.selectbox if s.label == "Saved consultation step").set_value("QUESTIONS").run()
                next(t for t in app.text_input if t.label == "Answer").set_value("Synthetic unfinished concern").run()
                next(s for s in app.selectbox if s.label == "Saved consultation step").set_value("MEASURE").run()
                next(s for s in app.selectbox if s.label == "Saved consultation step").set_value("QUESTIONS").run()
                self.assertEqual(next(t for t in app.text_input if t.label == "Answer").value, "Synthetic unfinished concern")
                next(s for s in app.selectbox if s.label == "Saved consultation step").set_value("MEDS").run()
                next(t for t in app.text_area if t.label == "Additional medicine history").set_value("Synthetic unfinished medicine history").run()
                next(s for s in app.selectbox if s.label == "Saved consultation step").set_value("MEASURE").run()
                next(s for s in app.selectbox if s.label == "Saved consultation step").set_value("MEDS").run()
                self.assertEqual(next(t for t in app.text_area if t.label == "Additional medicine history").value, "Synthetic unfinished medicine history")
                next(s for s in app.selectbox if s.label == "Saved consultation step").set_value("REVIEW").run()
                self.assertEqual(list(app.exception), [])
                next(b for b in app.button if b.label == "Generate verified case packet").click().run()
                self.assertEqual(list(app.exception), [])
                next(c for c in app.checkbox if c.label == "Patient/caregiver confirms the read-back").check()
                next(c for c in app.checkbox if c.label == "Worker confirms the full packet and consent to send").check().run()
                next(c for c in app.checkbox if c.label == "I reviewed the medicine safety flags and will include them in the doctor handoff").check().run()
                next(b for b in app.button if b.label == "Confirm read-back and send to doctor").click().run()
                self.assertEqual(list(app.exception), [])
                next(r for r in app.radio if r.label == "Workflow").set_value("Doctor console").run()
                self.assertEqual(list(app.exception), [])
                next(s for s in app.multiselect if s.label == "Worker can do now").set_value(["Glucose"]).run()
                next(s for s in app.multiselect if s.label == "Needs referral lab").set_value(["Blood count"]).run()
                next(r for r in app.radio if r.label == "Response status").set_value("Awaiting information / results").run()
                next(t for t in app.text_area if t.label == "Doctor advice").set_value("Synthetic interim guidance: return the requested test results.").run()
                next(b for b in app.button if b.label == "Save simulated doctor response").click().run()
                self.assertEqual(list(app.exception), [])
                next(r for r in app.radio if r.label == "Workflow").set_value("Consultation").run()
                next(t for t in app.text_input if t.label == "Test value").set_value("100")
                next(b for b in app.button if b.label == "Return task answer").click().run()
                self.assertEqual(list(app.exception), [])
                next(t for t in app.text_area if t.label == "Laboratory report result (copy exactly)").set_value("Synthetic issued laboratory report.")
                next(b for b in app.button if b.label == "Return task answer").click().run()
                self.assertEqual(list(app.exception), [])
                next(r for r in app.radio if r.label == "Workflow").set_value("Doctor console").run()
                next(b for b in app.button if b.label == "I reviewed the updated worker information").click().run()
                next(c for c in app.checkbox if c.label == "I reviewed the returned test reports and unavailable-test reasons").check().run()
                next(r for r in app.radio if r.label == "Response status").set_value("Final plan").run()
                next(b for b in app.button if b.label == "Save simulated doctor response").click().run()
                self.assertEqual(list(app.exception), [])
                next(s for s in app.selectbox if s.label == "Task").set_value("spo2").run()
                next(b for b in app.button if b.label == "Send simulated task").click().run()
                self.assertEqual(list(app.exception), [])
                next(r for r in app.radio if r.label == "Workflow").set_value("Split-screen demo").run()
                self.assertEqual(list(app.exception), [])
                next(t for t in app.text_input if t.label == "Measured value").set_value("89")
                next(b for b in app.button if b.label == "Return task answer").click().run()
                self.assertEqual(list(app.exception), [])
                self.assertTrue(any("EMERGENCY LANE" in e.value for e in app.error))
                next(b for b in app.button if b.label == "Create referral and simulated pre-alert now").click().run()
                self.assertEqual(list(app.exception), [])
                changed = next((b for b in app.button if b.label == "I reviewed the updated worker information"), None)
                if changed:
                    changed.click().run()
                next(t for t in app.text_area if t.label == "Doctor advice").set_value("Fictional doctor: proceed to the referral facility.")
                next(b for b in app.button if b.label == "Save simulated doctor response").click().run()
                self.assertEqual(list(app.exception), [])
                next(r for r in app.radio if r.label == "Workflow").set_value("Consultation").run()
                self.assertEqual(list(app.exception), [])
                self.assertTrue(any("Patient card" in s.value for s in app.subheader))
                next(s for s in app.selectbox if s.label == "Reported referral status").set_value("arrived").run()
                next(t for t in app.text_input if t.label == "Transport, arrival or barrier details").set_value("Worker reports facility arrival")
                next(b for b in app.button if b.label == "Save referral update").click().run()
                next(c for c in app.checkbox if c.label == "Worker explained this doctor response").check()
                next(r for r in app.radio if r.label == "Patient/caregiver understanding").set_value("Needs clarification")
                next(t for t in app.text_area if t.label == "Question or reason for clarification").set_value("Please explain the return precautions again")
                next(b for b in app.button if b.label == "Save counselling confirmation").click().run()
                self.assertEqual(list(app.exception), [])
                service = Store(Path(tmp) / "ui.db")
                cid = service.list_cases()[0]["id"]
                request = next(r for r in service.care_status(cid)["requests"] if r["status"] == "open")
                next(r for r in app.radio if r.label == "Workflow").set_value("Doctor console").run()
                next(b for b in app.button if b.label == "I reviewed the updated worker information").click().run()
                next(s for s in app.multiselect if s.label == "Worker questions or barriers answered in this response").set_value([request["request_id"]]).run()
                next(t for t in app.text_area if t.label == "Doctor advice").set_value("Synthetic revised explanation from the doctor.")
                next(b for b in app.button if b.label == "Save simulated doctor response").click().run()
                self.assertEqual(list(app.exception), [])
                next(r for r in app.radio if r.label == "Workflow").set_value("Consultation").run()
                next(c for c in app.checkbox if c.label == "Worker explained this doctor response").check()
                next(r for r in app.radio if r.label == "Patient/caregiver understanding").set_value("Reports understanding")
                next(b for b in app.button if b.label == "Save counselling confirmation").click().run()
                self.assertEqual(list(app.exception), [])
                care = service.care_status(cid)
                self.assertEqual(care["referrals"][0]["status"], "arrived")
                self.assertFalse(any(r["status"] == "open" for r in care["requests"]))
                self.assertTrue(any(g["understood"] is True for g in care["guidance"]))
            finally:
                if previous is None:
                    os.environ.pop("CONSULTATION_DB", None)
                else:
                    os.environ["CONSULTATION_DB"] = previous


if __name__ == "__main__":
    unittest.main()
