"""Regional demo app-level regressions for the guided Consultation flow.

These use the live Streamlit entry point (``app.py``) with synthetic records,
socket isolation and no network. They protect the regional-demo journey:
complaint (voice/typed) → transcript confirmation → early emergency screen →
structured intake (adaptive respiratory/TB pathway) → review.

They are engineering checks of the software journey, not clinical validation.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from smartsehat import audit
import seed

ROOT = Path(__file__).resolve().parents[1]


class RegionalConsultationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "audit.db"
        self.ckpt = str(Path(self.temp.name) / "checkpoints.db")
        environment = patch.dict("os.environ", {
            "SMARTSEHAT_OFFLINE": "1", "SMARTSEHAT_MODE": "stub",
            "SMARTSEHAT_AUTH_MODE": "demo",
            "SMARTSEHAT_MEDIA_ROOT": str(Path(self.temp.name) / "media"),
            "DOTENV_DISABLED": "1"}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        patcher = patch("smartsehat.audit.DB_PATH", str(self.db))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.network = patch("socket.socket.connect",
                             side_effect=AssertionError("Network forbidden in regional UI test"))
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)
        self.create_network = patch("socket.create_connection",
                                    side_effect=AssertionError("Network forbidden in regional UI test"))
        self.create_network_mock = self.create_network.start()
        self.addCleanup(self.create_network.stop)
        from smartsehat import dawa_graph, graph
        original_start = graph.start_case
        original_resume = graph.resume_case
        dawa_start = dawa_graph.start_dawa_case
        dawa_resume = dawa_graph.resume_dawa_case
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
        self.app = AppTest.from_file(str(ROOT / "smartsehat" / "app.py"), default_timeout=20)

    def tearDown(self):
        self.network_mock.assert_not_called()
        self.create_network_mock.assert_not_called()

    def button(self, label):
        return next(b for b in self.app.button if b.label == label)

    def assert_renders(self):
        self.assertEqual(list(self.app.exception), [])

    def login_and_create(self):
        self.app.run()
        self.button("Start demo session").click().run()
        self.assert_renders()
        self.button("Create consultation encounter for selected patient").click().run()
        self.assert_renders()

    def start_guided(self, complaint: str):
        """Start the guided consultation with a typed complaint."""
        self.login_and_create()
        if self.app.radio:
            source = next(r for r in self.app.radio if r.label == "Complaint source")
            source.set_value("Typed complaint").run()
        tarea = next(t for t in self.app.text_area
                     if t.label == "Patient complaint — type the original statement")
        tarea.set_value(complaint).run()
        self.assert_renders()
        self.button("Start guided consultation (structured intake)").click().run()
        self.assert_renders()

    def confirm_history(self):
        self.button("Confirm transcript + answers").click().run()
        self.assert_renders()

    def set_yes_no(self, label_prefix: str, value: str):
        radio = next(r for r in self.app.radio if r.label.startswith(label_prefix))
        radio.set_value(value).run()

    # ----------------------------------------------------------------
    # 1. Supported respiratory consultation completes structured intake
    # ----------------------------------------------------------------
    def test_supported_respiratory_consultation_completes_structured_intake(self):
        self.start_guided("patient has cough and fever")
        self.confirm_history()
        # Step 3 assessment renders with the pathway recommendation + questions.
        self.assertTrue(any(s.value == "Step 3 — Assessment" for s in self.app.subheader))
        pathway_box = next(c for c in self.app.checkbox
                          if c.label.startswith("I confirm this supported pathway"))
        pathway_box.check().run()
        self.set_yes_no("Does the patient currently have a cough", "Yes")
        self.set_yes_no("Is there blood in the sputum", "No")
        self.set_yes_no("Is there severe breathlessness", "No")
        self.set_yes_no("Does the patient currently have chest pain", "No")
        self.set_yes_no("Is the patient currently unconscious", "No")
        self.set_yes_no("Is the patient currently fitting", "No")
        self.button("Confirm structured information and assess").click().run()
        self.assert_renders()
        self.assertTrue(self.button("Approve reviewed plan"))

    # ----------------------------------------------------------------
    # 2. Fever YES opens fever duration
    # ----------------------------------------------------------------
    def test_fever_yes_opens_fever_duration(self):
        self.start_guided("patient has cough and fever")
        self.confirm_history()
        self.set_yes_no("Does the patient currently have fever", "Yes")
        durations = [r for r in self.app.radio
                     if r.label.startswith("How long has the fever")]
        self.assertEqual(len(durations), 1)

    # ----------------------------------------------------------------
    # 3. Fever NO does not require fever duration
    # ----------------------------------------------------------------
    def test_fever_no_does_not_require_fever_duration(self):
        self.start_guided("patient has cough and fever")
        self.confirm_history()
        self.set_yes_no("Does the patient currently have fever", "No")
        durations = [r for r in self.app.radio
                     if r.label.startswith("How long has the fever")]
        self.assertEqual(len(durations), 0)

    # ----------------------------------------------------------------
    # 4. UNKNOWN remains UNKNOWN (no fabricated duration)
    # ----------------------------------------------------------------
    def test_fever_unknown_remains_unknown_without_duration(self):
        self.start_guided("patient has cough")
        self.confirm_history()
        self.set_yes_no("Does the patient currently have fever", "Unknown")
        durations = [r for r in self.app.radio
                     if r.label.startswith("How long has the fever")]
        self.assertEqual(len(durations), 0)
        # The adaptive behavior keeps fever explicitly UNKNOWN: the duration
        # question is not required and no neutral "patient is fine" label is
        # shown; UNKNOWN is never silently treated as NORMAL/ABSENT.
        for warn in self.app.warning:
            self.assertNotIn("patient is safe", str(warn.value).lower())

    # ----------------------------------------------------------------
    # 5. Unsupported complaint does not borrow respiratory pathway
    # ----------------------------------------------------------------
    def test_unsupported_complaint_does_not_borrow_respiratory_pathway(self):
        self.start_guided("patient swallowed pesticide")
        self.confirm_history()
        self.assert_renders()
        # Unsupported scope is shown; no pathway confirm checkbox is offered.
        confirm_boxes = [c for c in self.app.checkbox
                         if c.label.startswith("I confirm this supported pathway")]
        self.assertEqual(len(confirm_boxes), 0)
        self.assertTrue(any("UNSUPPORTED" in str(m.value) for m in self.app.warning) or
                        any("does not include" in str(m.value) for m in self.app.info))

    # ----------------------------------------------------------------
    # 6. Pathway can be rejected
    # ----------------------------------------------------------------
    def test_pathway_can_be_rejected(self):
        self.start_guided("patient has fever only")
        self.confirm_history()
        # AMBIGUOUS match still offers a confirm, but the worker can decline
        # by not confirming. No crash and the intake stays open.
        self.assert_renders()
        self.button("Confirm structured information and assess").click().run()
        self.assert_renders()
        self.assertTrue(any("confirm" in str(m.value).lower() for m in self.app.warning))

    # ----------------------------------------------------------------
    # 7. Patient A/B state remains isolated
    # ----------------------------------------------------------------
    def test_patient_a_b_state_remains_isolated(self):
        self.login_and_create()
        case_a = self.app.session_state["active_consultation_id"]
        self.button("Create consultation encounter for selected patient").click().run()
        case_b = self.app.session_state["active_consultation_id"]
        self.assertNotEqual(case_a, case_b)
        records = self.app.session_state["consultation_encounters"]
        self.assertEqual(records[case_a]["patient_id"], "PT-0001")
        self.assertEqual(records[case_b]["patient_id"], "PT-0001")
        # Fresh Case B keeps an empty draft bucket (no cross-case leakage).
        self.assertEqual(records[case_b]["drafts"], {})

    # ----------------------------------------------------------------
    # 8. RED appears before image/model work
    # ----------------------------------------------------------------
    def test_red_appears_before_image_or_model_work(self):
        from smartsehat import audit as _audit
        self.start_guided("chest pain with sweating")
        self.confirm_history()
        # The early safety screen fires on accepted history. No image model
        # or structure step is required before RED is surfaced.
        self.assert_renders()
        # The HITL gate (immediately after early RED) shows the emergency
        # referral confirmation, and no STRUCTURE/VISION events occurred.
        events = [e["event_type"] for e in _audit.get_case_timeline(
            self.app.session_state["active_consultation_id"])]
        self.assertIn("EARLY_SAFETY_SCREEN", events)
        self.assertNotIn("STRUCTURE_EXTRACTED", events)
        self.assertNotIn("VISION_FINDING", events)
        self.assertTrue(any("Confirm emergency referral" in b.label for b in self.app.button))

    # ----------------------------------------------------------------
    # 9. Critical transcript correction requires explicit confirmation
    # ----------------------------------------------------------------
    def test_critical_transcript_correction_requires_explicit_confirmation(self):
        self.start_guided("chest pain with sweating and breathlessness")
        # Correct the transcript to remove the urgent evidence.
        tarea = next(t for t in self.app.text_area
                     if t.label == "Transcript — editable")
        tarea.set_value("mild headache only").run()
        self.assert_renders()
        # First confirm attempt: the backend rejects the silent downgrade and
        # re-renders the transcript stage with the urgent-evidence error.
        confirm = next(b for b in self.app.button if "Confirm transcript" in b.label)
        confirm.click().run()
        self.assert_renders()
        all_messages = [str(e.value) for e in list(self.app.error) + list(self.app.warning)]
        self.assertTrue(any("removes evidence" in m for m in all_messages))
        # The reviewed-removal acknowledgment path is now offered, and a bare
        # confirm (without acknowledgment/reason) must still be blocked.
        reviewed = [b for b in self.app.button
                    if "urgent-evidence removal reviewed" in b.label]
        self.assertTrue(reviewed)
        # Checking the acknowledgment AND recording a reason makes the
        # reviewed confirm available; a silent downgrade is never accepted.
        ack = next(c for c in self.app.checkbox
                  if "confirm the removal" in c.label)
        ack.check().run()
        reason = next(t for t in self.app.text_area
                      if t.label == "Reason for correcting the transcript (required)")
        reason.set_value("Worker judged the reported sweating to be an ASR error and arranged clinical follow-up.").run()
        self.assert_renders()
        reviewed = [b for b in self.app.button
                    if "urgent-evidence removal reviewed" in b.label]
        self.assertTrue(reviewed)
        reviewed[0].click().run()
        self.assert_renders()
        # The reviewed removal proceeds (structured intake or attributed RED
        # referral), never a silent downgrade to GREEN.
        on_intake = any(s.value == "Step 3 — Assessment" for s in self.app.subheader)
        on_emergency = any("Confirm emergency referral" in b.label for b in self.app.button)
        self.assertTrue(on_intake or on_emergency)

    # ----------------------------------------------------------------
    # 10. Blank allergies/current medicines remain unassessed
    # ----------------------------------------------------------------
    def test_blank_allergies_and_current_medicines_remain_unassessed(self):
        self.start_guided("patient has cough")
        self.confirm_history()
        self.set_yes_no("Does the patient currently have a cough", "Yes")
        pathway_box = next(c for c in self.app.checkbox
                          if c.label.startswith("I confirm this supported pathway"))
        pathway_box.check().run()
        self.button("Confirm structured information and assess").click().run()
        self.assert_renders()
        # The snapshot is updated at graph completion (approve).
        self.button("Approve reviewed plan").click().run()
        self.assert_renders()
        snapshot = _encounter_snapshot(self, self.app.session_state["active_consultation_id"])
        allergy = next((o for o in snapshot.observations if o.concept == "allergies"), None)
        meds = next((o for o in snapshot.observations if o.concept == "current_medicines"), None)
        self.assertIsNotNone(allergy)
        self.assertIsNotNone(meds)
        # Blank (untouched) states are UNASSESSED, never auto-"none".
        self.assertEqual(allergy.assertion, "UNASSESSED")
        self.assertEqual(meds.assertion, "UNASSESSED")


def _encounter_snapshot(test, encounter_id):
    from smartsehat import access
    from smartsehat.domain import ActorContext
    auth_actor = test.app.session_state["authenticated_actor"]
    ctx = ActorContext(
        auth_actor["actor_id"], auth_actor["display_name"],
        auth_actor["role"], tuple(auth_actor["facility_ids"]),
        auth_actor["active_facility_id"],
        frozenset(auth_actor["permissions"]), auth_actor["auth_source"])
    return access.get_encounter(ctx, encounter_id).snapshot


if __name__ == "__main__":
    unittest.main()