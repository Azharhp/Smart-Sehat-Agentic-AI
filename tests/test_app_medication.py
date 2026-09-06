"""Full Streamlit entry-point smoke tests using synthetic records and no network."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from smartsehat import audit
import seed

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {"agents": "stub", "allowlist_medicines": ["paracetamol", "amoxicillin"]}


class AppMedicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "audit.db"
        self.ckpt = str(Path(self.temp.name) / "checkpoints.db")
        environment = patch.dict("os.environ", {"SMARTSEHAT_OFFLINE": "1",
                                                 "SMARTSEHAT_MODE": "stub",
                                                 "SMARTSEHAT_AUTH_MODE": "demo",
                                                 "SMARTSEHAT_MEDIA_ROOT": str(Path(self.temp.name) / "media"),
                                                 "DOTENV_DISABLED": "1"}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        for target, value in (("smartsehat.audit.DB_PATH", str(self.db)),):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.network = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden in UI test"))
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)
        self.create_network = patch("socket.create_connection", side_effect=AssertionError("Network forbidden in UI test"))
        self.create_network_mock = self.create_network.start()
        self.addCleanup(self.create_network.stop)
        from smartsehat import graph, dawa_graph
        original_start, original_resume = graph.start_case, graph.resume_case
        dawa_start, dawa_resume = dawa_graph.start_dawa_case, dawa_graph.resume_dawa_case
        for target, original in (("smartsehat.graph.start_case", original_start),
                                 ("smartsehat.graph.resume_case", original_resume),
                                 ("smartsehat.dawa_graph.start_dawa_case", dawa_start),
                                 ("smartsehat.dawa_graph.resume_dawa_case", dawa_resume)):
            def isolated(*args, _original=original, **kwargs):
                kwargs["checkpoint_db"] = self.ckpt
                return _original(*args, **kwargs)
            patcher = patch(target, isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        seed.seed(str(self.db))
        conn = audit._connect()
        try:
            conn.execute(
                "INSERT INTO patients(patient_id,name,age,sex,chronic_conditions_json,allergies_json,bhu_id) "
                "VALUES('PT-0002','Second Patient',28,'F','[]','[]','BHU-0442')"
            )
            conn.commit()
        finally:
            conn.close()
        self.app = AppTest.from_file(str(ROOT / "smartsehat" / "app.py"), default_timeout=20)

    def tearDown(self):
        self.network_mock.assert_not_called()
        self.create_network_mock.assert_not_called()

    def button(self, label):
        return next(button for button in self.app.button if button.label == label)

    def assert_renders(self):
        self.assertEqual(list(self.app.exception), [])

    def login(self):
        self.app.run()
        self.assert_renders()
        self.button("Start demo session").click().run()
        self.assert_renders()

    def start_consultation(self):
        self.login()
        self.button("Create consultation encounter for selected patient").click().run()
        self.assert_renders()
        self.button("Start consultation workflow").click().run()
        self.assert_renders()

    def test_consultation_no_medicine_review_and_completion_render(self):
        self.start_consultation()
        self.button("Confirm transcript + answers").click().run()
        self.assert_renders()
        self.assertTrue(any("NO MEDICINES PROPOSED" in item.value for item in self.app.info))
        self.button("Approve reviewed plan").click().run()
        self.assert_renders()
        self.assertTrue(any("Encounter complete" in item.value for item in self.app.success))

    def test_unsaved_patient_context_change_disables_approval(self):
        self.start_consultation()
        self.button("Confirm transcript + answers").click().run()
        self.assertFalse(self.button("Approve reviewed plan").disabled)
        allergy_known = next(box for box in self.app.checkbox
                             if box.label.startswith("Allergy history confirmed"))
        allergy_known.uncheck().run()
        self.assert_renders()
        self.assertTrue(self.button("Approve reviewed plan").disabled)
        self.assertTrue(any("Unsaved medicine or context" in item.value
                            for item in self.app.warning))

    def test_review_acknowledgment_is_scoped_to_review_input_hash(self):
        self.start_consultation()
        self.button("Confirm transcript + answers").click().run()
        acknowledgment = next(
            box for box in self.app.checkbox
            if box.label == "I reviewed the medicine findings and checks not assessed."
        )
        acknowledgment.check().run()
        self.assertTrue(next(
            box for box in self.app.checkbox
            if box.label == "I reviewed the medicine findings and checks not assessed."
        ).value)

        weight = next(item for item in self.app.number_input
                      if item.label == "Weight (kg; blank if unknown)")
        weight.set_value(49).run()
        self.button("Save context and recheck").click().run()
        self.assert_renders()
        refreshed = next(
            box for box in self.app.checkbox
            if box.label == "I reviewed the medicine findings and checks not assessed."
        )
        self.assertFalse(refreshed.value)

    def test_medicine_recheck_preserves_unsaved_patient_context(self):
        self.start_consultation()
        self.button("Confirm transcript + answers").click().run()
        allergies = next(item for item in self.app.text_input
                         if item.label == "Allergies (comma separated)")
        allergies.set_value("penicillin").run()
        self.assertTrue(self.button("Approve reviewed plan").disabled)

        self.button("Submit medicine plan and recheck").click().run()
        self.assert_renders()
        retained = next(item for item in self.app.text_input
                        if item.label == "Allergies (comma separated)")
        self.assertEqual(retained.value, "penicillin")
        self.assertTrue(self.button("Approve reviewed plan").disabled)

    def test_dawa_manual_entry_and_empty_extraction_validation_render(self):
        self.login()
        self.app.sidebar.radio[0].set_value("Dawa Check").run()
        self.button("Create Dawa encounter").click().run()
        self.button("Start Dawa workflow").click().run()
        self.assert_renders()
        self.assertTrue(any("Manual entry" in item.value for item in self.app.info))
        self.button("Confirm list and run review").click().run()
        self.assert_renders()
        self.assertTrue(any("at least one medicine" in item.value for item in self.app.error))
        case_id = self.app.session_state["active_dawa_id"]
        self.assertEqual(audit.get_case(case_id)["patient_id"], "PT-0001")
        self.assertFalse(audit.get_case(case_id)["completed_at"])

    def test_emergency_packet_has_referral_and_no_universal_drug_order(self):
        self.start_consultation()
        self.app.text_area[0].set_value("Severe chest pain with sweating and breathlessness").run()
        self.button("Confirm transcript + answers").click().run()
        self.assert_renders()
        self.assertTrue(any(button.label == "Confirm emergency referral" for button in self.app.button),
                        str(self.app.session_state["consultation_encounters"]))
        self.button("Confirm emergency referral").click().run()
        self.assert_renders()
        code = " ".join(item.value for item in self.app.code)
        self.assertIn("UNSUPPORTED_EMERGENCY_PROTOCOL", code)
        self.assertIn("emergency referral required", code)
        self.assertNotIn("aspirin 300", code.casefold())

    def test_worker_identity_is_authenticated_not_an_editable_text_field(self):
        self.login()
        labels = [item.label.casefold() for item in self.app.text_input]
        self.assertFalse(any("worker id" in label or "paramedic id" in label for label in labels))
        actor = self.app.session_state["authenticated_actor"]
        self.assertEqual(actor["actor_id"], "PM-2213")
        self.assertEqual(actor["auth_source"], "demo-local")
        self.assertTrue(any("not production authentication" in item.value
                            for item in self.app.sidebar.warning))

    def test_patient_switch_requires_new_encounter_and_does_not_rebind(self):
        self.login()
        self.button("Create consultation encounter for selected patient").click().run()
        first_id = self.app.session_state["active_consultation_id"]
        patient_picker = next(item for item in self.app.sidebar.selectbox
                              if item.label == "Patient")
        patient_picker.set_value("Second Patient (PT-0002)").run()
        self.assertTrue(any("differs from this active encounter" in item.value
                            for item in self.app.warning))
        self.button("Start new encounter for selected patient").click().run()
        self.assert_renders()
        second_id = self.app.session_state["active_consultation_id"]
        records = self.app.session_state["consultation_encounters"]
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(records[first_id]["patient_id"], "PT-0001")
        self.assertEqual(records[second_id]["patient_id"], "PT-0002")

    def test_unsaved_transcript_is_restored_after_case_a_b_a_switch(self):
        self.start_consultation()
        case_a = self.app.session_state["active_consultation_id"]
        transcript = next(item for item in self.app.text_area
                          if item.label == "Transcript — editable")
        transcript.set_value("CASE A UNSAVED TRANSCRIPT").run()
        self.button("Create consultation encounter for selected patient").click().run()
        case_b = self.app.session_state["active_consultation_id"]
        self.assertNotEqual(case_a, case_b)
        picker = next(item for item in self.app.selectbox
                      if item.label == "Open consultation encounter")
        picker.set_value(case_a).run()
        self.assert_renders()
        restored = next(item for item in self.app.text_area
                        if item.label == "Transcript — editable")
        self.assertEqual(restored.value, "CASE A UNSAVED TRANSCRIPT")
        records = self.app.session_state["consultation_encounters"]
        self.assertEqual(records[case_b]["drafts"], {})


if __name__ == "__main__":
    unittest.main()
