"""Phase 3 acceptance regressions using synthetic inputs and no external calls.

These are engineering checks of the bounded intake rules, not clinical validation.
"""
from __future__ import annotations

import copy
import inspect
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import seed
from smartsehat import access, agents, audit, clinical_intake as intake, graph
from smartsehat import rules_triage
from smartsehat.auth import DemoAuthenticationProvider
from smartsehat.rules_triage import evaluate


DEVA_RED = "मुझे आधे घंटे से सीने में बहुत दर्द है और पसीना आ रहा है"
URDU_RED = "مجھے آدھے گھنٹے سے سینے میں بہت درد ہے اور پسینہ آ رہا ہے"
FIXED_TIME = "2026-09-07T10:00:00+00:00"
PATIENT = {"age": 42, "age_unit": "years", "sex": "M", "pregnant": False,
           "allergies": [], "current_medicines": [], "current_meds": [],
           "weight_kg": 70, "renal_impairment": False, "liver_disease": False,
           "context_confirmed": True}


class OfflineCase(unittest.TestCase):
    def setUp(self):
        self.network_guards = []
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("External connections forbidden in Phase 3 tests"))
            self.network_guards.append(guard.start())
            self.addCleanup(guard.stop)
        environment = patch.dict(os.environ, {"SMARTSEHAT_OFFLINE": "1", "SMARTSEHAT_MODE": "stub"})
        environment.start()
        self.addCleanup(environment.stop)

    def tearDown(self):
        for guard in self.network_guards:
            guard.assert_not_called()


class HistoryAndIntakeContractTests(OfflineCase):
    def capture(self, raw="Patient has a cough", source="ASR"):
        return intake.captured_history(
            "CASE-SYNTHETIC", source=source, source_media_artifact_id="MEDIA-SYNTHETIC" if source == "ASR" else None,
            raw_text=raw, declared_language="ur", provider="fake-local" if source == "ASR" else "worker",
        )

    def observations(self, answers, patient=None):
        return intake.build_observations(answers, patient or PATIENT, "PM-2213", 7, observed_at=FIXED_TIME)

    def test_raw_corrected_and_accepted_stages_preserve_original_provenance(self):
        raw = self.capture("Patient has cough for tree weeks")
        original = copy.deepcopy(raw)
        accepted, error = intake.accept_history(raw, "Patient has cough for three weeks", "PM-2213", confirmed_at=FIXED_TIME)
        self.assertIsNone(error)
        self.assertEqual(raw, original)
        self.assertEqual(raw["status"], "RAW")
        self.assertIsNone(raw["accepted_text"])
        self.assertEqual(accepted["raw_text"], original["raw_text"])
        self.assertEqual(accepted["corrected_text"], "Patient has cough for three weeks")
        self.assertEqual(accepted["accepted_text"], accepted["corrected_text"])
        self.assertEqual(accepted["status"], "ACCEPTED")
        self.assertEqual(accepted["encounter_id"], "CASE-SYNTHETIC")
        self.assertEqual(accepted["source_media_artifact_id"], "MEDIA-SYNTHETIC")
        self.assertEqual(accepted["confirming_actor_id"], "PM-2213")
        self.assertEqual(accepted["confirmed_at"], FIXED_TIME)
        self.assertGreater(accepted["version"], raw["version"])
        self.assertIsNone(accepted["confidence"])

    def test_blank_invalid_acceptance_never_restores_rejected_raw_text(self):
        history = self.capture("chest pain with sweating")
        for correction in (None, "", "  \n\t", 3, {}, []):
            with self.subTest(correction=correction):
                result, error = intake.accept_history(history, correction, "PM-2213")
                self.assertIsNone(result)
                self.assertTrue(error)
                self.assertIsNone(history["accepted_text"])
                self.assertEqual(history["raw_text"], "chest pain with sweating")

    def test_typed_complaint_has_equivalent_confirmation_provenance(self):
        history = self.capture("Typed complaint: cough", "typed")
        self.assertEqual(history["source"], "typed")
        self.assertIsNone(history["source_media_artifact_id"])
        self.assertIsNone(history["accepted_text"])
        result, error = intake.accept_history(history, "Typed complaint: cough", "PM-2213", confirmed_at=FIXED_TIME)
        self.assertIsNone(error)
        self.assertEqual(result["source"], "typed")
        self.assertEqual(result["confirming_actor_id"], "PM-2213")
        self.assertEqual(result["raw_text"], "Typed complaint: cough")

    def test_unavailable_capture_remains_empty_without_canned_symptoms(self):
        history = intake.captured_history("CASE-SYNTHETIC", source="ASR", source_media_artifact_id="MEDIA-SYNTHETIC",
                                          raw_text=None, declared_language="ur", provider=None,
                                          degraded_reason="asr_unavailable")
        self.assertEqual(history["status"], "UNAVAILABLE")
        self.assertEqual(history["degraded_reason"], "asr_unavailable")
        self.assertIsNone(history["raw_text"])
        self.assertIsNone(history["accepted_text"])

    def test_supported_scripts_keep_original_text_immutable(self):
        for text in (URDU_RED, DEVA_RED, "سینے میں pain اور sweating", "  CHEST PAIN; SWEATING!  "):
            with self.subTest(text=text):
                result = intake.normalize_text(text)
                self.assertEqual(result["original_text"], text)
                self.assertIsInstance(result["normalized_text"], str)
                self.assertIn(result["normalization_status"], {"SUCCESS", "NOT_REQUIRED"})
                history = self.capture(text)
                accepted, error = intake.accept_history(history, text, "PM-2213")
                self.assertIsNone(error)
                self.assertEqual(accepted["raw_text"], text)
                self.assertEqual(accepted["accepted_text"], text.strip())

    def test_normalization_failure_preserves_original_without_fabricating_text(self):
        with patch.object(intake.unicodedata, "normalize", side_effect=ValueError("synthetic normalization failure")):
            result = intake.normalize_text(DEVA_RED)
        self.assertEqual(result["original_text"], DEVA_RED)
        self.assertIsNone(result["normalized_text"])
        self.assertEqual(result["normalization_status"], "FAILED")

    def test_assertion_states_and_question_identity_remain_distinct(self):
        for assertion in ("PRESENT", "ABSENT", "UNKNOWN", "UNASSESSED"):
            with self.subTest(assertion=assertion):
                observations, errors = self.observations({"resp_hemoptysis": {"assertion": assertion}})
                self.assertEqual(errors, [])
                row = next(item for item in observations if item["question_id"] == "resp_hemoptysis")
                self.assertEqual(row["assertion"], assertion)
                self.assertEqual(row["concept"], "hemoptysis")
                self.assertEqual(row["snapshot_version"], 7)
                self.assertEqual(row["entered_by"], "PM-2213")
                self.assertEqual(row["observed_at"], FIXED_TIME)
                self.assertEqual(row["verification_state"], "CONFIRMED")
                self.assertTrue(row["observation_id"])
                self.assertTrue(row["definition_version"])
                self.assertEqual(json.loads(json.dumps(row)), row)

    def test_duration_preserves_original_weeks_and_normalized_days(self):
        observations, errors = self.observations({
            "resp_cough": {"assertion": "PRESENT"},
            "resp_cough_duration": {"assertion": "PRESENT", "value": 3, "unit": "weeks"},
        })
        self.assertEqual(errors, [])
        duration = next(row for row in observations if row["concept"] == "cough_duration")
        self.assertEqual((duration["value"], duration["unit"]), (3, "weeks"))
        self.assertEqual((duration["normalized_value"], duration["normalized_unit"]), (21, "days"))

    def test_temperature_original_and_normalized_values_are_distinct(self):
        for value, unit, expected in ((100, "F", 37.78), (100.4, "F", 38.0), (37, "C", 37.0)):
            with self.subTest(value=value, unit=unit):
                observations, errors = self.observations({"vital_temperature": {"assertion": "PRESENT", "value": value, "unit": unit}})
                self.assertEqual(errors, [])
                row = next(item for item in observations if item["concept"] == "temperature")
                self.assertEqual((row["value"], row["unit"]), (value, unit))
                self.assertAlmostEqual(row["normalized_value"], expected, places=2)
                self.assertEqual(row["normalized_unit"], "C")

    def test_missing_or_unknown_temperature_unit_is_not_guessed(self):
        for unit in (None, "", "Celsius?", "K"):
            with self.subTest(unit=unit):
                observations, errors = self.observations({"vital_temperature": {"assertion": "PRESENT", "value": 100, "unit": unit}})
                self.assertTrue(errors)
                self.assertFalse(any(row["concept"] == "temperature" and row["assertion"] == "PRESENT" for row in observations))

    def test_malformed_numeric_measurements_are_rejected(self):
        for value in (True, False, "many", "", None, float("nan"), float("inf"), -1, {}, []):
            with self.subTest(value=value):
                _, errors = self.observations({"vital_temperature": {"assertion": "PRESENT", "value": value, "unit": "C"}})
                self.assertTrue(errors)

    def test_dependency_appears_only_for_a_present_prerequisite(self):
        for assertion in ("PRESENT", "ABSENT", "UNKNOWN", "UNASSESSED"):
            with self.subTest(assertion=assertion):
                definitions = intake.relevant_questions({"resp_fever": {"assertion": assertion}}, PATIENT)
                question_ids = {item["question_id"] for item in definitions}
                self.assertEqual("resp_fever_duration" in question_ids, assertion == "PRESENT")

    def test_inactive_duration_answer_does_not_survive_as_current_evidence(self):
        answers = {"resp_fever": {"assertion": "ABSENT"},
                   "resp_fever_duration": {"assertion": "PRESENT", "value": 7, "unit": "days"}}
        sanitized = intake.sanitize_answers(answers, PATIENT)
        self.assertNotIn("resp_fever_duration", sanitized)
        self.assertIn("resp_fever_duration", answers)
        observations, errors = self.observations(answers)
        self.assertEqual(errors, [])
        self.assertFalse(any(item["concept"] == "fever_duration" for item in observations))

    def test_unknown_prerequisite_remains_visibly_unknown(self):
        observations, errors = self.observations({"resp_fever": {"assertion": "UNKNOWN"}})
        self.assertEqual(errors, [])
        missing = intake.missing_information(observations, PATIENT, intake.RESPIRATORY_PATHWAY_ID)
        self.assertEqual(next(row for row in missing if row["concept"] == "fever")["status"], "UNKNOWN")
        self.assertFalse(any(row["concept"] == "fever_duration" for row in missing))

    def test_explicit_negative_is_complete_while_unasked_is_not_normal(self):
        observations, errors = self.observations({"resp_hemoptysis": {"assertion": "ABSENT"}})
        self.assertEqual(errors, [])
        missing = intake.missing_information(observations, PATIENT, intake.RESPIRATORY_PATHWAY_ID)
        self.assertFalse(any(row["concept"] == "hemoptysis" for row in missing))
        self.assertEqual(next(row for row in missing if row["concept"] == "fever")["status"], "UNASSESSED")

    def test_supported_ambiguous_and_unsupported_pathways_remain_distinct(self):
        for text, expected in (("Patient has a cough", "SUPPORTED_CANDIDATE"),
                               ("Patient has fever", "AMBIGUOUS"),
                               ("Patient swallowed pesticide", "UNSUPPORTED_SCOPE")):
            with self.subTest(text=text):
                recommendation = intake.recommend_pathway(text)
                self.assertEqual(recommendation["status"], expected)
                self.assertTrue(recommendation["reason"])
                self.assertTrue(recommendation["pathway_version"])
                if expected == "UNSUPPORTED_SCOPE":
                    self.assertIsNone(recommendation["pathway_id"])
                else:
                    self.assertEqual(recommendation["pathway_id"], intake.RESPIRATORY_PATHWAY_ID)
                    self.assertTrue(recommendation["requires_worker_confirmation"])

    def test_pathway_confirmation_is_explicit_versioned_and_attributed(self):
        recommendation = intake.recommend_pathway("Patient has a cough")
        payload = {"action": "submit_intake", "selected_pathway_id": intake.RESPIRATORY_PATHWAY_ID}
        selected, error = intake.select_pathway(recommendation, payload, "PM-2213")
        self.assertIsNone(selected)
        self.assertTrue(error)
        selected, error = intake.select_pathway(recommendation, {**payload, "pathway_confirmed": True},
                                                "PM-2213", selected_at=FIXED_TIME)
        self.assertIsNone(error)
        self.assertEqual(selected["pathway_id"], intake.RESPIRATORY_PATHWAY_ID)
        self.assertEqual(selected["pathway_version"], intake.RESPIRATORY_PATHWAY_VERSION)
        self.assertEqual(selected["selected_by"], "PM-2213")
        self.assertEqual(selected["selected_at"], FIXED_TIME)
        self.assertEqual(selected["selection_method"], "WORKER_CONFIRMED")

    def test_unsupported_complaint_cannot_borrow_respiratory_pathway(self):
        recommendation = intake.recommend_pathway("Heavy bleeding from a leg wound")
        selected, error = intake.select_pathway(recommendation, {
            "action": "submit_intake", "selected_pathway_id": intake.RESPIRATORY_PATHWAY_ID,
            "pathway_confirmed": True}, "PM-2213")
        self.assertIsNone(selected)
        self.assertTrue(error)
        selected, error = intake.select_pathway(recommendation, {
            "action": "acknowledge_unsupported", "reason": "Urgent clinical assessment arranged outside this pathway."}, "PM-2213")
        self.assertIsNone(error)
        self.assertEqual(selected["status"], "UNSUPPORTED_SCOPE")
        self.assertIsNone(selected["pathway_id"])


class TriageTruthfulnessTests(OfflineCase):
    """A09/A10/A06/A07 rules-level contract checks (engineering, not clinical)."""

    def evaluate(self, **state):
        return evaluate(state)

    def test_every_configured_red_rule_positive_returns_rule_and_version(self):
        cases = {
            "chest_pain_with_ACS_features": "chest pain with sweating",
            "severe_breathlessness": "patient has severe breathlessness",
            "altered_consciousness_or_fits": "patient is unconscious",
            "pregnancy_with_bleeding": "pregnant patient with bleeding",
            "infant_lt_2mo_with_fever": "newborn with fever",
        }
        for rule_id, text in cases.items():
            with self.subTest(rule=rule_id):
                result = self.evaluate(transcript_confirmed=text)
                self.assertEqual(result["status"], "RED")
                self.assertEqual(result["triggered_rules"][0], rule_id)
                self.assertEqual(result["rule_version"], rules_triage.RULE_VERSION)
                self.assertEqual(result["findings"][0]["rule_id"], rule_id)
                self.assertEqual(result["findings"][0]["rule_version"], rules_triage.RULE_VERSION)
                self.assertTrue(result["findings"][0]["evidence"])

    def test_negated_red_wording_does_not_assert_the_symptom(self):
        cases = [
            "no chest pain",
            "no chest pain, no sweating",
            "Patient says no blood in sputum",
            "no shortness of breath",
        ]
        for text in cases:
            with self.subTest(text=text):
                result = self.evaluate(transcript_confirmed=text)
                self.assertNotIn(result["status"], ("RED", "YELLOW"))
                self.assertNotIn(result["status"], ("YELLOW",))

    def test_mixed_positive_and_negative_keeps_high_sensitivity(self):
        result = self.evaluate(transcript_confirmed="chest pain but no sweating")
        self.assertEqual(result["status"], "NO_CONFIGURED_FLAG")

    def test_substring_inside_a_word_does_not_assert_a_symptom(self):
        # CLIN-007: "benefits" contains "fits" but must not fire convulsions.
        result = self.evaluate(transcript_confirmed="Patient asks about sunhanefits of the medicine")
        self.assertNotIn(result["status"], ("RED", "YELLOW"))
        # And a genuine boundary match still fires.
        red = self.evaluate(transcript_confirmed="patient is having fits")
        self.assertEqual(red["status"], "RED")

    def test_force_green_cannot_downgrade_red(self):
        for payload in ({"force_triage": "GREEN"}, {"force_triage": "YELLOW"}):
            with self.subTest(payload=payload):
                result = self.evaluate(transcript_confirmed="chest pain with sweating", **payload)
                self.assertEqual(result["status"], "RED")

    def test_force_red_is_honoured_for_demo(self):
        result = self.evaluate(transcript_confirmed="mild headache", force_triage="RED")
        self.assertEqual(result["status"], "RED")

    def test_empty_input_is_insufficient_data_not_green(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertNotEqual(result["level"], "GREEN")
        result = self.evaluate(transcript_confirmed="no blood in sputum")
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")

    def test_no_configured_match_is_flagged_not_safe(self):
        result = self.evaluate(transcript_confirmed="mild headache")
        self.assertEqual(result["status"], "NO_CONFIGURED_FLAG")
        self.assertEqual(result["level"], "GREEN")
        # The instruction must NOT claim the patient is healthy / cleared.
        self.assertNotIn("healthy", result["instruction"].lower())
        self.assertNotIn("routine care", result["instruction"].lower())

    def test_structured_infant_fever_triggers_configured_red_rule(self):
        observations = [
            {"concept": "age", "assertion": "PRESENT", "value": 1, "unit": "months",
             "normalized_value": 1, "normalized_unit": "months", "verification_state": "CONFIRMED"},
            {"concept": "fever", "assertion": "PRESENT", "value": True,
             "verification_state": "CONFIRMED"},
        ]
        result = self.evaluate(observations=observations)
        self.assertEqual(result["status"], "RED")
        self.assertEqual(result["triggered_rules"][0], "infant_lt_2mo_with_fever")

    def test_structured_duration_triggers_configured_yellow_rule(self):
        observations = [
            {"concept": "fever_duration", "assertion": "PRESENT", "value": 3, "unit": "days",
             "normalized_value": 3, "normalized_unit": "days", "verification_state": "CONFIRMED"},
        ]
        result = self.evaluate(observations=observations)
        self.assertEqual(result["status"], "YELLOW")
        self.assertEqual(result["triggered_rules"][0], "fever_gt_3days")

    def test_unknown_duration_is_not_yellow_and_remains_unknown(self):
        observations = [
            {"concept": "fever_duration", "assertion": "UNKNOWN",
             "verification_state": "CONFIRMED"},
        ]
        result = self.evaluate(observations=observations)
        self.assertNotEqual(result["status"], "YELLOW")
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")

    def test_reasoning_output_cannot_alter_deterministic_triage(self):
        # A reasoning node writing a GREEN-ish/'confirmed safe' field into
        # state cannot change the deterministic status already established.
        red = self.evaluate(transcript_confirmed="chest pain with sweating")
        red_after_model = self.evaluate(transcript_confirmed="chest pain with sweating",
                                        ddx=[], confidence=0.0, degraded=None)
        self.assertEqual(red["status"], red_after_model["status"])
        self.assertEqual(red_after_model["status"], "RED")


class TranscriptGateNodeTests(OfflineCase):
    """stt_node / transcript_gate graph-level behaviour (no provider calls)."""

    def setUp(self):
        super().setUp()
        self.workspace = tempfile.TemporaryDirectory(prefix="smartsehat_sttnode_test_")
        self.addCleanup(self.workspace.cleanup)
        patch.object(audit, "DB_PATH",
                     str(Path(self.workspace.name) / "audit.db")).start()
        patch.object(agents, "_config", return_value={"agents": "stub"}).start()
        audit.init_db()

    def test_stt_failure_produces_degraded_history_not_canned_symptoms(self):
        from smartsehat.agents import stt_node
        with patch.object(agents, "_mode", return_value="real"):
            result = stt_node({"case_id": "CASE-X", "audio_path": "x.wav",
                               "audio_artifact_id": "MEDIA-X", "stt_language": "ur"})
        self.assertIsNone(result["transcript"])
        self.assertEqual(result["degraded"], "stt_unavailable")
        self.assertEqual(result["history"]["status"], "UNAVAILABLE")
        self.assertEqual(result["history"]["degraded_reason"], "stt_unavailable")
        self.assertIsNone(result["history"]["raw_text"])

    def test_real_mode_no_audio_requires_typed_history(self):
        from smartsehat.agents import stt_node
        with patch.object(agents, "_mode", return_value="real"):
            result = stt_node({"case_id": "CASE-X", "stt_language": "ur"})
        self.assertIsNone(result["transcript"])
        self.assertEqual(result["history"]["status"], "UNAVAILABLE")
        self.assertIsNone(result["history"]["raw_text"])

    def test_typed_complaint_uses_provenance_model(self):
        from smartsehat.agents import stt_node
        typed = "  cough since 2 weeks  "
        result = stt_node({"case_id": "CASE-X", "typed_complaint": typed,
                           "stt_language": "ur"})
        # The original typed text is preserved immutably (A03); the accepted
        # stage strips it only after explicit confirmation.
        self.assertEqual(result["transcript"], typed)
        self.assertEqual(result["history"]["source"], "typed")
        self.assertEqual(result["history"]["status"], "RAW")
        self.assertEqual(result["history"]["raw_text"], typed)
        self.assertIsNone(result["history"]["accepted_text"])

    def test_stub_mode_canned_transcript_is_explicit_demo_only(self):
        from smartsehat.agents import stt_node
        with patch.object(agents, "_mode", return_value="stub"):
            result = stt_node({"case_id": "CASE-X", "stt_language": "ur"})
        self.assertEqual(result["history"]["provider"], "stub")
        self.assertEqual(result["history"]["status"], "RAW")


class ClinicalSnapshotPersistenceTests(OfflineCase):
    """Versioned canonical snapshot round-trips for Phase 3 evidence."""

    def setUp(self):
        super().setUp()
        self.workspace = tempfile.TemporaryDirectory(prefix="smartsehat_snapshot_test_")
        self.addCleanup(self.workspace.cleanup)
        self.directory = Path(self.workspace.name)
        self.audit_db = str(self.directory / "audit.db")
        self.checkpoint = str(self.directory / "consultation_checkpoints.db")
        patch.object(audit, "DB_PATH", self.audit_db).start()
        patch.object(agents, "_config", return_value={"agents": "stub",
                                                      "allowlist_medicines": ["paracetamol", "amoxicillin"]}).start()
        patch.object(agents, "_router", side_effect=AssertionError("No external providers")).start()
        patch("socket.create_connection", side_effect=AssertionError("No network")).start()
        seed.seed(self.audit_db)
        self.actor = DemoAuthenticationProvider().authenticate("PM-2213")

    def completed_encounter(self, transcript_text: str):
        case_id, thread_id, _first = graph.run_case(
            "PT-0001", self.actor, checkpoint_db=self.checkpoint)
        graph.resume_case(thread_id, self.actor, action="confirm",
                          transcript=transcript_text, checkpoint_db=self.checkpoint)
        graph.resume_case(thread_id, self.actor, action="approve",
                          checkpoint_db=self.checkpoint)
        return access.get_encounter(self.actor, case_id).snapshot

    def test_accepted_history_persists_into_canonical_snapshot_with_actor(self):
        snapshot = self.completed_encounter("patient has cough for two weeks")
        self.assertEqual(snapshot.version, 2)
        self.assertIsNotNone(snapshot.history)
        self.assertEqual(snapshot.history.status, "ACCEPTED")
        self.assertEqual(snapshot.history.accepted_text, "patient has cough for two weeks")
        self.assertEqual(snapshot.history.confirming_actor_id, "PM-2213")
        self.assertEqual(snapshot.history.source, "stub_demo")
        self.assertEqual(snapshot.triage.status, "YELLOW")

    def test_early_red_routes_to_emergency_and_persists_assessment(self):
        snapshot = self.completed_encounter("chest pain with sweating")
        self.assertEqual(snapshot.triage.status, "RED")
        self.assertEqual(snapshot.triage.rule_version, rules_triage.RULE_VERSION)
        self.assertTrue(any(f.get("rule_id") == "chest_pain_with_ACS_features"
                            for f in snapshot.triage.findings))

    def test_update_clinical_snapshot_round_trips_structured_observations(self):
        actor = self.actor
        case_id, _thread, _first = graph.run_case("PT-0001", actor, checkpoint_db=self.checkpoint)
        history = intake.captured_history(case_id, source="typed", source_media_artifact_id=None,
                                          raw_text="cough three weeks", declared_language="ur", provider="worker")
        accepted, error = intake.accept_history(history, "cough three weeks", "PM-2213",
                                                confirmed_at=FIXED_TIME)
        self.assertIsNone(error)
        observations, errors = intake.build_observations(
            {"resp_cough": {"assertion": "PRESENT"},
             "resp_cough_duration": {"assertion": "PRESENT", "value": 3, "unit": "weeks"}},
            PATIENT, "PM-2213", 2, case_id, observed_at=FIXED_TIME)
        self.assertEqual(errors, [])
        pathway = intake.recommend_pathway(accepted["accepted_text"])
        selected, _ = intake.select_pathway(pathway, {
            "action": "submit_intake", "selected_pathway_id": intake.RESPIRATORY_PATHWAY_ID,
            "pathway_confirmed": True}, "PM-2213", selected_at=FIXED_TIME)
        self.assertIsNotNone(selected)
        restored = access.update_clinical_snapshot(
            actor, case_id, history=accepted, observations=observations,
            pathway=selected, triage=evaluate({"history": accepted,
                                               "observations": observations}),
            expected_version=1)
        self.assertEqual(restored.version, 2)
        self.assertEqual(restored.history.accepted_text, "cough three weeks")
        self.assertEqual(restored.pathway.status, "CONFIRMED")
        self.assertEqual(restored.pathway.selected_by, "PM-2213")
        self.assertEqual(restored.triage.status, "YELLOW")
        # build_observations records *every* relevant question (UNASSESSED
        # when unanswered); the answered ones carry explicit provenance.
        self.assertGreaterEqual(len(restored.observations), 2)
        by_concept = {o.concept for o in restored.observations}
        self.assertIn("cough", by_concept)
        self.assertIn("cough_duration", by_concept)
        duration = next(o for o in restored.observations if o.concept == "cough_duration")
        self.assertEqual((duration.value, duration.unit), (3, "weeks"))
        self.assertEqual((duration.normalized_value, duration.normalized_unit), (21, "days"))
        self.assertEqual(duration.entered_by, "PM-2213")
        self.assertEqual(duration.observed_at, FIXED_TIME)
        self.assertEqual(duration.snapshot_version, 2)
        self.assertEqual(duration.verification_state, "CONFIRMED")
        # persistence across restore
        reloaded = access.get_encounter(actor, case_id).snapshot
        self.assertEqual(reloaded.history.accepted_text, "cough three weeks")
        reloaded_duration = next(o for o in reloaded.observations if o.concept == "cough_duration")
        self.assertEqual(reloaded_duration.snapshot_version, 2)

    def test_medication_context_carries_age_unit_and_age(self):
        case_id, _thread, _first = graph.run_case("PT-0001", self.actor, checkpoint_db=self.checkpoint)
        obs, errors = intake.build_observations(
            {"demographic_age": {"assertion": "PRESENT", "value": 6, "unit": "months"}},
            PATIENT, "PM-2213", 2, case_id, observed_at=FIXED_TIME)
        self.assertEqual(errors, [])
        access.update_clinical_snapshot(self.actor, case_id, observations=obs, expected_version=1)
        ctx = access.medication_context(access.get_encounter(self.actor, case_id).snapshot)
        self.assertEqual(ctx["age"], 6)
        self.assertEqual(ctx["age_unit"], "months")

    def test_concurrent_snapshot_change_is_not_silently_overwritten(self):
        case_id, _thread, _first = graph.run_case("PT-0001", self.actor, checkpoint_db=self.checkpoint)
        with self.assertRaises(ValueError):
            access.update_clinical_snapshot(
                self.actor, case_id, expected_version=99,
                observations=[{"concept": "fever", "assertion": "PRESENT",
                               "verification_state": "CONFIRMED", "unit": None,
                               "value": None, "normalized_value": None,
                               "normalized_unit": None, "observation_id": "X"}])


if __name__ == "__main__":
    unittest.main()
