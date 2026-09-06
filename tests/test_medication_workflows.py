"""Offline integration checks for the medication review workflow.

These test software safeguards and audit traceability, not clinical validation.
Every test uses its own SQLite databases and deterministic or fake providers.
Run with: .venv\\Scripts\\python.exe -m unittest discover -s tests -p test_medication_workflows.py -v
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import seed
from smartsehat import access, agents, audit, dawa_graph, graph
from smartsehat.auth import DemoAuthenticationProvider
from smartsehat.medication_review import review_medicines


CONFIRMED_CONTEXT = {
    "age": 42,
    "sex": "M",
    "weight_kg": 70,
    "allergies": [],
    "current_meds": [],
    "pregnant": False,
    "renal_impairment": False,
    "liver_disease": False,
    "context_confirmed": True,
    "aspirin_prescribed": False,
}
REVIEW_NOTE = "Reviewed the supported checks and limitations; clinician follow-up arranged."
STUB_CONFIG = {"agents": "stub", "allowlist_medicines": ["paracetamol", "amoxicillin"]}


def paracetamol(dose_mg=500, frequency_per_day=3):
    return {"name": "paracetamol", "strength": "500 mg", "dose_mg": dose_mg,
            "frequency_per_day": frequency_per_day, "duration_days": 2,
            "route": "oral", "indication": "mild pain"}


class MedicationWorkflowTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory(prefix="smartsehat_medication_test_")
        self.addCleanup(workspace.cleanup)
        self.directory = Path(workspace.name)
        self.checkpoint = str(self.directory / "consultation_checkpoints.db")
        self.addCleanup(patch.stopall)
        patch.object(audit, "DB_PATH", str(self.directory / "audit.db")).start()
        patch.object(agents, "_config", return_value=copy.deepcopy(STUB_CONFIG)).start()
        self.unexpected_router = patch.object(
            agents, "_router", side_effect=AssertionError("No external providers in workflow tests")
        ).start()
        self.unexpected_dawa_router = patch.object(
            dawa_graph, "_router", side_effect=AssertionError("No external providers in Dawa tests")
        ).start()
        self.unexpected_network = patch(
            "socket.create_connection", side_effect=AssertionError("No network in workflow tests")
        ).start()
        seed.seed()
        self.actor = DemoAuthenticationProvider().authenticate("PM-2213")
        self.case_id = self.thread_id = None

    def tearDown(self):
        self.unexpected_router.assert_not_called()
        self.unexpected_dawa_router.assert_not_called()
        self.unexpected_network.assert_not_called()

    def start(self, context=None, force_triage=None):
        self.case_id, self.thread_id, first = graph.run_case(
            "PT-0001", self.actor, checkpoint_db=self.checkpoint,
            medication_context=copy.deepcopy(context), force_triage=force_triage,
        )
        self.assertEqual(self.gate(first)["gate"], "transcript")
        return self.resume("confirm", transcript="Synthetic test history reviewed by worker")

    def resume(self, action, **kwargs):
        return graph.resume_case(self.thread_id, self.actor, action, checkpoint_db=self.checkpoint, **kwargs)

    def gate(self, result):
        self.assertIn("__interrupt__", result)
        self.assertEqual(len(result["__interrupt__"]), 1)
        return result["__interrupt__"][0].value

    def events(self, event_type=None):
        rows = audit.get_case_timeline(self.case_id)
        return [row for row in rows if event_type is None or row["event_type"] == event_type]

    def assert_pending(self):
        self.assertIsNone(audit.get_case(self.case_id)["completed_at"])
        self.assertEqual(audit.get_hitl_decisions(self.case_id), [])
        self.assertEqual(self.events("RX_FINALIZED"), [])

    def approve_review(self):
        return self.resume("approve", medication_review_acknowledged=True, reason=REVIEW_NOTE)

    def test_no_medicines_is_explicit_and_audited_without_fake_pass(self):
        result = self.start()
        payload = self.gate(result)
        self.assertEqual(payload["patient_id"], "PT-0001")
        self.assertEqual(payload["proposed_medicines"], [])
        review = payload["medication_review"]
        self.assertEqual(review["status"], "NO_MEDICINES_PROPOSED")
        self.assertFalse(review["approval_blocked"])
        self.assert_pending()
        self.assertEqual(self.events("PHARM_CHECK_PASSED"), [])
        self.assertNotIn("insurance_status", result)
        self.assertEqual(len(self.events("MEDICATION_REVIEW")), 1)
        receipt_row = self.events("MEDICATION_REVIEW")[0]
        receipt = json.loads(receipt_row["payload_json"])
        self.assertEqual(receipt_row["provider_used"], "local-rules")
        self.assertIsNone(receipt_row["confidence"])
        self.assertEqual(receipt["actor_id"], "PM-2213")
        self.assertEqual(receipt["review"]["input_hash"], review["input_hash"])
        self.assertTrue(review["ruleset_version"])
        self.assertNotIn("__interrupt__", self.resume("approve"))
        self.assertTrue(audit.get_case(self.case_id)["completed_at"])

    def test_unknown_patient_cannot_start_a_consultation(self):
        with self.assertRaises(LookupError):
            graph.run_case("PT-NOT-FOUND", self.actor, checkpoint_db=self.checkpoint)

    def test_high_dose_cannot_be_approved_and_corrected_dose_is_rechecked(self):
        self.start(CONFIRMED_CONTEXT)
        high = self.resume("modify", proposed_medicines=[paracetamol(1500, 4)])
        high_report = self.gate(high)["medication_review"]
        self.assertEqual(high_report["status"], "HOLD")
        self.assertTrue(high_report["approval_blocked"])
        self.assertTrue(any(flag.get("blocking") for flag in high_report["flags"]))
        rejected_approval = self.approve_review()
        self.assertTrue(self.gate(rejected_approval)["review_validation_error"])
        self.assert_pending()

        corrected = self.resume("modify", proposed_medicines=[paracetamol(500, 3)])
        corrected_report = self.gate(corrected)["medication_review"]
        self.assertFalse(corrected_report["approval_blocked"])
        self.assertNotEqual(high_report["input_hash"], corrected_report["input_hash"])
        self.assertEqual(len(self.events("MEDICATION_REVIEW")), 3)
        final = self.approve_review()
        self.assertNotIn("__interrupt__", final)
        decision = json.loads(audit.get_hitl_decisions(self.case_id)[0]["final_json"])
        self.assertEqual(decision["medication_review"]["input_hash"], corrected_report["input_hash"])
        self.assertEqual(decision["proposed_medicines"][0]["dose_mg"], 500)
        self.assertTrue(decision["review_acknowledged"])

    def test_edits_at_both_gates_preserve_original_and_latest_plan(self):
        first = self.start(CONFIRMED_CONTEXT)
        original = self.gate(first)["prescription_draft"]
        first_edit = self.resume("modify", proposed_medicines=[paracetamol(500, 3)])
        self.assertEqual(self.gate(first_edit)["gate"], "post_modify")
        latest_edit = self.resume("modify", proposed_medicines=[paracetamol(1000, 3)])
        latest_report = self.gate(latest_edit)["medication_review"]
        self.assertEqual(len(self.events("MEDICATION_REVIEW")), 3)
        self.assertNotIn("__interrupt__", self.approve_review())
        decision = audit.get_hitl_decisions(self.case_id)[0]
        self.assertEqual(json.loads(decision["original_json"])["prescription_draft"], original)
        final = json.loads(decision["final_json"])
        self.assertEqual(final["proposed_medicines"][0]["dose_mg"], 1000)
        self.assertEqual(final["medication_review"]["input_hash"], latest_report["input_hash"])
        self.assertEqual(audit.get_case(self.case_id)["hitl_outcome"], "modified")

    def test_unknown_actions_stay_at_review_gate(self):
        self.start(CONFIRMED_CONTEXT)
        for action in ("", "finalize", "APPROVE", "invented", None):
            with self.subTest(action=action):
                payload = self.gate(self.resume(action))
                self.assertTrue(payload["review_validation_error"])
                self.assert_pending()
        self.assertNotIn("__interrupt__", self.resume("approve"))

    def test_invalid_structured_edit_cannot_become_an_empty_approved_plan(self):
        self.start(CONFIRMED_CONTEXT)
        response = self.resume("modify", proposed_medicines=[{"dose_mg": 500}])
        payload = self.gate(response)
        self.assertTrue(payload["review_validation_error"] or payload["medication_review"]["approval_blocked"])
        self.assert_pending()

    def test_explicit_removal_of_all_proposals_is_rechecked(self):
        self.start(CONFIRMED_CONTEXT)
        self.resume("modify", proposed_medicines=[paracetamol(1500, 4)])
        result = self.resume("modify", proposed_medicines=[])
        report = self.gate(result)["medication_review"]
        self.assertEqual(report["status"], "NO_MEDICINES_PROPOSED")
        self.assertFalse(report["approval_blocked"])
        self.assertNotIn("__interrupt__", self.resume("approve"))
        self.assertEqual(len(self.events("MEDICATION_REVIEW")), 3)

    def test_missing_context_requires_acknowledgment_and_meaningful_review_note(self):
        self.start()
        result = self.resume("modify", proposed_medicines=[paracetamol()])
        report = self.gate(result)["medication_review"]
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertTrue(report["not_assessed"])
        self.assertTrue(self.gate(self.resume("approve"))["review_validation_error"])
        self.assert_pending()
        short_note = self.resume("approve", medication_review_acknowledged=True, reason="ok")
        self.assertTrue(self.gate(short_note)["review_validation_error"])
        self.assert_pending()
        self.assertNotIn("__interrupt__", self.approve_review())

    def test_patient_context_changes_refresh_review_hash_and_reset_acknowledgment(self):
        self.start(CONFIRMED_CONTEXT)
        before = self.gate(self.resume("modify", proposed_medicines=[paracetamol()]))
        changed_context = {**CONFIRMED_CONTEXT, "weight_kg": 49}
        after = self.gate(self.resume("update_context", medication_context=changed_context))
        self.assertNotEqual(before["medication_review"]["input_hash"], after["medication_review"]["input_hash"])
        self.assertEqual(after["medication_context"]["weight_kg"], 49)
        self.assertEqual(after["medication_review"]["status"], "REVIEW_REQUIRED")
        self.assertTrue(self.gate(self.resume("approve", reason=REVIEW_NOTE))["review_validation_error"])
        self.assert_pending()

    def test_approval_cannot_persist_an_unreviewed_context_change(self):
        self.start(CONFIRMED_CONTEXT)
        self.resume("modify", proposed_medicines=[paracetamol()])
        with self.assertRaisesRegex(ValueError, "update_context"):
            self.resume("approve", medication_context={**CONFIRMED_CONTEXT,
                                                        "allergies": ["paracetamol"]})
        restored = access.get_encounter(self.actor, self.case_id)
        self.assertEqual(restored.snapshot.patient.allergies, [])

    def test_resume_decisions_are_attributed_to_current_authenticated_reviewer(self):
        conn = audit._connect()
        try:
            conn.execute("INSERT INTO users VALUES('DOC-1','Doctor One','doctor','BHU-0442')")
            conn.execute("INSERT INTO user_facilities VALUES('DOC-1','BHU-0442')")
            conn.commit()
        finally:
            conn.close()
        doctor = DemoAuthenticationProvider().authenticate("DOC-1")

        self.case_id, self.thread_id, first = graph.run_case(
            "PT-0001", self.actor, checkpoint_db=self.checkpoint,
            medication_context=copy.deepcopy(CONFIRMED_CONTEXT),
        )
        self.assertEqual(self.gate(first)["gate"], "transcript")
        review = graph.resume_case(
            self.thread_id, doctor, "confirm", transcript="Doctor-confirmed history",
            checkpoint_db=self.checkpoint,
        )
        self.assertEqual(self.gate(review)["gate"], "primary")
        graph.resume_case(self.thread_id, doctor, "approve", checkpoint_db=self.checkpoint)
        transcript_event = json.loads(self.events("VOICE_TRANSCRIPT_CONFIRMED")[0]["payload_json"])
        decision_event = json.loads(self.events("HITL_DECISION")[0]["payload_json"])
        self.assertEqual(transcript_event["actor_id"], "DOC-1")
        self.assertEqual(decision_event["actor_id"], "DOC-1")
        self.assertEqual(access.get_encounter(doctor, self.case_id).actor_id, "PM-2213")

        self.checkpoint = str(self.directory / "doctor_dawa_checkpoints.db")
        self.case_id, self.thread_id, first = dawa_graph.run_dawa_case(
            "", self.actor, checkpoint_db=self.checkpoint, patient_id="PT-0001"
        )
        self.assertEqual(self.gate(first)["gate"], "dawa_confirm")
        review = dawa_graph.resume_dawa_case(
            self.thread_id, doctor, checkpoint_db=self.checkpoint, action="confirm",
            medicines=[paracetamol()], patient=copy.deepcopy(CONFIRMED_CONTEXT),
        )
        self.assertEqual(self.gate(review)["gate"], "dawa_review")
        dawa_graph.resume_dawa_case(
            self.thread_id, doctor, checkpoint_db=self.checkpoint, action="finalize",
            disposition="reviewed", review_acknowledged=True, reason=REVIEW_NOTE,
        )
        extracted = json.loads(self.events("RX_EXTRACTED")[0]["payload_json"])
        finalized = json.loads(self.events("DAWA_FINALIZED")[0]["payload_json"])
        self.assertEqual(extracted["actor_id"], "DOC-1")
        self.assertEqual(finalized["actor_id"], "DOC-1")
        self.assertEqual(access.get_encounter(doctor, self.case_id).actor_id, "PM-2213")

    def test_stale_consultation_receipt_cannot_approve_a_changed_plan(self):
        self.start(CONFIRMED_CONTEXT)
        old = self.gate(self.resume("modify", proposed_medicines=[paracetamol()]))["medication_review"]
        with graph.graph_with_checkpointer(self.checkpoint) as workflow:
            workflow.update_state({"configurable": {"thread_id": self.thread_id}},
                                  {"proposed_medicines": [paracetamol(1500, 4)]})
        refreshed = self.gate(self.approve_review())["medication_review"]
        self.assertNotEqual(old["input_hash"], refreshed["input_hash"])
        self.assertEqual(refreshed["status"], "HOLD")
        self.assert_pending()
        self.assertTrue(self.gate(self.approve_review())["review_validation_error"])
        self.assert_pending()

    def test_new_allergy_blocks_approval_after_context_update(self):
        self.start(CONFIRMED_CONTEXT)
        self.resume("modify", proposed_medicines=[paracetamol()])
        allergy_context = {**CONFIRMED_CONTEXT, "allergies": ["paracetamol"]}
        report = self.gate(self.resume("update_context", medication_context=allergy_context))["medication_review"]
        self.assertTrue(report["approval_blocked"])
        self.assertEqual(report["status"], "HOLD")
        self.assertTrue(self.gate(self.approve_review())["review_validation_error"])
        self.assert_pending()

    def test_unparsed_free_text_edit_is_not_assumed_to_have_a_valid_dose(self):
        self.start(CONFIRMED_CONTEXT)
        report = self.gate(self.resume("modify", modified_content="paracetamol 500mg TDS"))["medication_review"]
        self.assertTrue(report["approval_blocked"])
        self.assertIsNone(report["medicines"][0].get("dose_mg"))
        self.assertTrue(self.gate(self.approve_review())["review_validation_error"])
        self.assert_pending()

    def test_rejection_requires_reason_and_records_referral(self):
        self.start(CONFIRMED_CONTEXT)
        self.assertTrue(self.gate(self.resume("reject"))["review_validation_error"])
        self.assert_pending()
        result = self.resume("reject", reason="Referral for clinician assessment before any medicine decision.")
        self.assertNotIn("__interrupt__", result)
        self.assertEqual(audit.get_case(self.case_id)["outcome"], "rejected_by_paramedic")
        self.assertEqual(len(self.events("REFERRAL_ISSUED")), 1)

    def test_red_referral_does_not_issue_universal_aspirin_or_allow_new_medicine_edits(self):
        result = self.start(CONFIRMED_CONTEXT, force_triage="RED")
        gate = self.gate(result)
        self.assertEqual(gate["triage_level"], "RED")
        self.assertEqual(gate["proposed_medicines"], [])
        self.assertNotIn("aspirin", gate["prescription_draft"].lower())
        self.assertEqual(self.events("MEDICATION_REVIEW"), [])
        self.assertEqual(self.events("REASONING_RESULT"), [])
        invalid = self.resume("modify", proposed_medicines=[paracetamol()])
        self.assertTrue(self.gate(invalid)["review_validation_error"])
        self.assert_pending()
        context_edit = self.resume("update_context", medication_context=CONFIRMED_CONTEXT)
        self.assertTrue(self.gate(context_edit)["review_validation_error"])
        self.assertEqual(self.events("MEDICATION_REVIEW"), [])
        self.assertNotIn("__interrupt__", self.resume("approve"))
        self.assertEqual(audit.get_case(self.case_id)["outcome"], "emergency_referral")

    def test_final_audit_receipt_retains_evidence_and_actor(self):
        self.start(CONFIRMED_CONTEXT)
        amoxicillin = {"name": "amoxicillin", "strength": "500 mg", "dose_mg": 500,
                       "frequency_per_day": 3, "duration_days": 5, "route": "oral",
                       "indication": "Proposed antibiotic requiring clinician indication review"}
        review = self.gate(self.resume("modify", proposed_medicines=[amoxicillin]))["medication_review"]
        self.assertEqual(review["status"], "REVIEW_REQUIRED")
        self.assertTrue(any(flag.get("evidence", {}).get("url") for flag in review["flags"]))
        self.assertNotIn("__interrupt__", self.approve_review())
        final = json.loads(self.events("RX_FINALIZED")[0]["payload_json"])
        self.assertEqual(final["actor_id"], "PM-2213")
        self.assertEqual(final["review_note"], REVIEW_NOTE)
        self.assertTrue(final["review_acknowledged"])
        self.assertEqual(final["medication_review"]["ruleset_version"], review["ruleset_version"])
        self.assertEqual(final["medication_review"]["flags"], review["flags"])
        self.assertEqual(final["medication_review"]["input_hash"], review["input_hash"])
        decision = json.loads(self.events("HITL_DECISION")[0]["payload_json"])
        self.assertEqual(decision["medication_review_hash"], review["input_hash"])

    def run_model_response(self, response):
        self.case_id = "MODEL-" + uuid.uuid4().hex[:8]
        audit.create_case(self.case_id, "PT-0001", "PM-2213", "BHU-0442")
        state = {"case_id": self.case_id, "patient_id": "PT-0001", "paramedic_id": "PM-2213",
                 "medication_context": copy.deepcopy(CONFIRMED_CONTEXT), "triage_level": "GREEN",
                 "transcript_confirmed": "Synthetic clinical history", "symptoms": ["mild_headache"]}
        generator = Mock(return_value=(json.dumps(response), "test:local"))
        fake = SimpleNamespace(route_generate=generator)
        with patch.object(agents, "_config", return_value={**STUB_CONFIG, "agents": "real"}), \
             patch.object(agents, "_router", return_value=fake):
            reasoning = agents.reasoning_node(state)
            checked = agents.pharm_node({**state, **reasoning})
        self.assertGreaterEqual(generator.call_count, 1)
        return reasoning, checked, generator

    @staticmethod
    def model_response(**changes):
        return {"differential_diagnosis": [{"diagnosis": "Undifferentiated headache", "citations": []}],
                "initial_management": "Proposed paracetamol for clinician review.",
                "recommended_workup": ["Clinical assessment"], "proposed_medicines": [paracetamol()],
                "confidence": 0.5, "uncertainty_note": "Synthetic test only", **changes}

    def test_real_reasoning_preserves_structured_dose_and_patient_context(self):
        reasoning, checked, generator = self.run_model_response(self.model_response())
        self.assertTrue(reasoning["medication_plan_error"])
        self.assertEqual(checked["proposed_medicines"][0]["dose_mg"], 500)
        self.assertTrue(checked["medication_review"]["approval_blocked"])
        messages = generator.call_args.args[1]
        self.assertIn('"weight_kg": 70', messages[1]["content"])
        self.assertIn("proposed_medicines", messages[0]["content"])

    def test_model_narrative_medicines_with_empty_structured_list_require_reconciliation(self):
        reasoning, checked, _ = self.run_model_response(self.model_response(
            proposed_medicines=[], initial_management="Give a medicine named only in this narrative."
        ))
        self.assertEqual(reasoning["proposed_medicines"], [])
        self.assertTrue(reasoning["medication_plan_error"])
        self.assertTrue(checked["medication_review"]["approval_blocked"])
        state = {"case_id": self.case_id, "paramedic_id": "PM-2213", "triage_level": "GREEN",
                 "medication_context": copy.deepcopy(CONFIRMED_CONTEXT), **reasoning, **checked}
        self.assertEqual(graph._apply_resume(state, {"action": "approve",
                         "medication_review_acknowledged": True, "reason": REVIEW_NOTE})["hitl_action"], "invalid")
        reconciled = graph._apply_resume(state, {"action": "modify", "proposed_medicines": []})
        self.assertIsNone(reconciled["medication_plan_error"])
        checked_again = agents.pharm_node({**state, **reconciled})
        self.assertEqual(checked_again["medication_review"]["status"], "NO_MEDICINES_PROPOSED")
        self.assertFalse(checked_again["medication_review"]["approval_blocked"])

    def test_missing_model_medicine_list_is_held_instead_of_called_no_medicines(self):
        response = self.model_response()
        response.pop("proposed_medicines")
        reasoning, checked, _ = self.run_model_response(response)
        self.assertTrue(reasoning["medication_plan_error"])
        self.assertEqual(checked["medication_review"]["status"], "HOLD")
        self.assertTrue(checked["medication_review"]["approval_blocked"])

    def test_nonfinite_model_confidence_degrades_and_blocks_medicine_plan(self):
        reasoning, checked, _ = self.run_model_response(self.model_response(confidence=float("nan")))
        self.assertEqual(reasoning["degraded"], "reasoning_unavailable")
        self.assertEqual(checked["medication_review"]["status"], "HOLD")
        self.assertTrue(checked["medication_review"]["approval_blocked"])

    def start_dawa(self):
        self.checkpoint = str(self.directory / "dawa_checkpoints.db")
        self.case_id, self.thread_id, first = dawa_graph.run_dawa_case(
            "", self.actor, checkpoint_db=self.checkpoint, patient_id="PT-0001"
        )
        payload = self.gate(first)
        self.assertEqual(payload["gate"], "dawa_confirm")
        self.assertEqual(payload["patient_id"], "PT-0001")
        self.assertEqual(audit.get_case(self.case_id)["patient_id"], "PT-0001")
        return first

    def dawa_resume(self, **payload):
        return dawa_graph.resume_dawa_case(self.thread_id, self.actor,
                                           checkpoint_db=self.checkpoint, **payload)

    def confirm_dawa(self, medicines, context=None):
        return self.dawa_resume(action="confirm", medicines=medicines,
                                patient=copy.deepcopy(CONFIRMED_CONTEXT if context is None else context))

    def test_dawa_requires_explicit_confirmation_and_nonempty_medicine_list(self):
        self.start_dawa()
        for payload in ({}, {"action": "approve"},
                        {"action": "confirm", "medicines": [], "patient": CONFIRMED_CONTEXT},
                        {"action": "confirm", "medicines": [paracetamol()], "patient": None}):
            with self.subTest(payload=payload):
                response = self.gate(self.dawa_resume(**payload))
                self.assertEqual(response["gate"], "dawa_confirm")
                # LangGraph interprets an empty resume mapping as no supplied
                # interrupt answer, so the original pending gate is preserved.
                if payload:
                    self.assertTrue(response["review_validation_error"])
                self.assertEqual(self.events("MEDICATION_REVIEW"), [])
                self.assertIsNone(audit.get_case(self.case_id)["completed_at"])
        self.assertEqual(self.gate(self.confirm_dawa([paracetamol()]))["gate"], "dawa_review")

    def test_dawa_exposes_the_full_shared_report_and_receipt(self):
        self.start_dawa()
        gate = self.gate(self.confirm_dawa([paracetamol(1500, 4)]))
        report = gate["medication_review"]
        expected = review_medicines(gate["medicines"], gate["patient"])
        self.assertEqual(report["input_hash"], expected["input_hash"])
        self.assertEqual(report["ruleset_version"], expected["ruleset_version"])
        self.assertEqual(report["flags"], expected["flags"])
        self.assertEqual(gate["grid"], report["medicines"])
        self.assertEqual(report["status"], "HOLD")
        receipt = json.loads(self.events("MEDICATION_REVIEW")[0]["payload_json"])
        self.assertEqual(receipt["review"], report)
        self.assertEqual(receipt["actor_id"], "PM-2213")
        self.assertTrue(self.events("MEDICINE_FLAGGED"))

    def test_dawa_review_note_retains_original_flags_and_hold(self):
        self.start_dawa()
        before = self.gate(self.confirm_dawa([paracetamol(1500, 4)]))["medication_review"]
        flag_id = before["flags"][0]["id"]
        after = self.gate(self.dawa_resume(action="review_flag", flag_id=flag_id, reason=REVIEW_NOTE))
        self.assertEqual(after["medication_review"]["flags"], before["flags"])
        self.assertEqual(after["medication_review"]["status"], "HOLD")
        self.assertEqual(after["grid"], before["medicines"])
        self.assertEqual(after["reviewed_flags"][0]["review_hash"], before["input_hash"])
        self.assertEqual(self.events("FLAG_DISMISSED"), [])
        self.assertEqual(len(self.events("FLAG_REVIEWED")), 1)
        self.assertIsNone(audit.get_case(self.case_id)["completed_at"])

    def test_dawa_invalid_flag_notes_cannot_clear_findings_or_finalize(self):
        self.start_dawa()
        before = self.gate(self.confirm_dawa([paracetamol(1500, 4)]))["medication_review"]
        for flag_id, reason in (("nonexistent", REVIEW_NOTE), (before["flags"][0]["id"], "ok")):
            with self.subTest(flag_id=flag_id, reason=reason):
                result = self.gate(self.dawa_resume(action="review_flag", flag_id=flag_id, reason=reason))
                self.assertTrue(result["review_validation_error"])
                self.assertEqual(result["medication_review"]["flags"], before["flags"])
                self.assertIsNone(audit.get_case(self.case_id)["completed_at"])
        self.assertEqual(self.events("FLAG_REVIEWED"), [])

    def test_dawa_hold_can_only_complete_with_explicit_referral_and_note(self):
        self.start_dawa()
        self.confirm_dawa([paracetamol(1500, 4)])
        invalid_requests = (
            {}, {"action": "dismiss"}, {"action": "finalize"},
            {"action": "finalize", "disposition": "reviewed", "review_acknowledged": True, "reason": REVIEW_NOTE},
            {"action": "finalize", "disposition": "refer", "review_acknowledged": False, "reason": REVIEW_NOTE},
            {"action": "finalize", "disposition": "refer", "review_acknowledged": True, "reason": "ok"},
        )
        for payload in invalid_requests:
            with self.subTest(payload=payload):
                response = self.gate(self.dawa_resume(**payload))
                if payload:
                    self.assertTrue(response["review_validation_error"])
                self.assertEqual(self.events("DAWA_FINALIZED"), [])
                self.assertIsNone(audit.get_case(self.case_id)["completed_at"])
        final = self.dawa_resume(action="finalize", disposition="refer",
                                 review_acknowledged=True, reason=REVIEW_NOTE)
        self.assertNotIn("__interrupt__", final)
        self.assertEqual(audit.get_case(self.case_id)["outcome"], "dawa_referred")
        receipt = json.loads(self.events("DAWA_FINALIZED")[0]["payload_json"])
        self.assertEqual(receipt["review"]["status"], "HOLD")
        self.assertFalse(receipt["dispensing_authorized"])
        self.assertEqual(receipt["disposition"], "refer")
        self.assertEqual(receipt["actor_id"], "PM-2213")
        self.assertNotIn("kept_green", receipt)

    def test_dawa_edit_returns_to_confirmation_and_reruns_review(self):
        self.start_dawa()
        original = self.gate(self.confirm_dawa([paracetamol(1500, 4)]))["medication_review"]
        edit_gate = self.gate(self.dawa_resume(action="modify"))
        self.assertEqual(edit_gate["gate"], "dawa_confirm")
        corrected = self.gate(self.confirm_dawa([paracetamol()]))["medication_review"]
        self.assertFalse(corrected["approval_blocked"])
        self.assertNotEqual(corrected["input_hash"], original["input_hash"])
        self.assertEqual(len(self.events("MEDICATION_REVIEW")), 2)
        final = self.dawa_resume(action="finalize", disposition="reviewed",
                                 review_acknowledged=True, reason=REVIEW_NOTE)
        self.assertNotIn("__interrupt__", final)
        self.assertEqual(audit.get_case(self.case_id)["outcome"], "dawa_check_completed")
        receipt = json.loads(self.events("DAWA_FINALIZED")[0]["payload_json"])
        self.assertEqual(receipt["review"]["input_hash"], corrected["input_hash"])
        self.assertFalse(receipt["dispensing_authorized"])

    def test_stale_dawa_receipt_cannot_finalize_changed_medicines(self):
        self.start_dawa()
        old = self.gate(self.confirm_dawa([paracetamol()]))["medication_review"]
        with dawa_graph.dawa_graph_with_checkpointer(self.checkpoint) as workflow:
            workflow.update_state({"configurable": {"thread_id": self.thread_id}},
                                  {"medicines": [paracetamol(1500, 4)]})
        result = self.dawa_resume(action="finalize", disposition="reviewed",
                                  review_acknowledged=True, reason=REVIEW_NOTE)
        refreshed = self.gate(result)["medication_review"]
        self.assertNotEqual(old["input_hash"], refreshed["input_hash"])
        self.assertEqual(refreshed["status"], "HOLD")
        self.assertEqual(self.events("DAWA_FINALIZED"), [])
        self.assertIsNone(audit.get_case(self.case_id)["completed_at"])

    def test_out_of_sequence_dawa_confirm_cannot_change_canonical_patient_context(self):
        self.start_dawa()
        review = self.gate(self.confirm_dawa([paracetamol()]))
        self.assertEqual(review["gate"], "dawa_review")
        before = access.get_encounter(self.actor, self.case_id)
        self.assertEqual(before.snapshot.patient.allergies, [])

        changed = {**CONFIRMED_CONTEXT, "allergies": ["paracetamol"]}
        rejected = self.gate(self.dawa_resume(
            action="confirm", medicines=[paracetamol()], patient=changed,
        ))
        self.assertEqual(rejected["gate"], "dawa_review")
        self.assertTrue(rejected["review_validation_error"])
        after = access.get_encounter(self.actor, self.case_id)
        self.assertEqual(after.snapshot.patient.allergies, [])
        self.assertEqual(after.snapshot.version, before.snapshot.version)


if __name__ == "__main__":
    unittest.main()
