"""Synthetic rule-boundary tests; these are not clinical validation."""
from copy import deepcopy
import json
import unittest

from smartsehat.dawa import check_medicines, parse_strength_mg
from smartsehat.medication_review import (
    NO_MEDICINES, RULESET_VERSION, SOURCES, approval_error, draft_to_medicines,
    format_medicines, normalize_medicines, resolve, review_medicines,
)


def context(**changes):
    value = {"age": 42, "sex": "F", "weight_kg": 70, "allergies": [],
             "current_meds": [], "pregnant": False, "renal_impairment": False,
             "liver_disease": False, "context_confirmed": True}
    value.update(changes)
    return value


def para(**changes):
    value = {"name": "paracetamol", "strength": "500 mg", "dose_mg": 500,
             "frequency_per_day": 3, "duration_days": 2, "route": "oral",
             "indication": "pain"}
    value.update(changes)
    return value


def codes(report):
    return {flag["code"] for flag in report["flags"]}


class MedicationRulesTests(unittest.TestCase):
    def test_exact_aliases_resolve_but_ocr_fragments_do_not(self):
        self.assertEqual(resolve(" PANADOL ")["ingredients"], ["paracetamol"])
        self.assertEqual(resolve("paracetamol + caffeine")["ingredients"], ["paracetamol", "caffeine"])
        for name in ["Pan", "Panadol?", "Panadol 500 mg", "Panadol Extra ???", "Rimactazid", "", None]:
            with self.subTest(name=name):
                self.assertIsNone(resolve(name)["generic"])

    def test_no_legacy_pregnancy_category_is_returned(self):
        self.assertNotIn("pregnancy_category", resolve("Brufen"))

    def test_single_mass_parser_rejects_concentrations_and_free_text(self):
        for text, expected in [("1 g", 1000), ("500mg", 500), ("250 mcg", .25)]:
            self.assertEqual(parse_strength_mg(text), expected)
        for text in ["500mg/5ml", "500+125 mg", "take 500 mg", "500mg BID", "5 ml", "0 mg", None]:
            self.assertIsNone(parse_strength_mg(text), text)

    def test_strength_never_implies_an_administered_dose(self):
        report = review_medicines([para(strength="2000 mg", dose_mg=None)], context())
        self.assertIn("dose_not_assessed", codes(report))
        self.assertNotIn("paracetamol_adult_limit", codes(report))
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_unknown_name_is_a_hold_with_source_and_limitation(self):
        report = review_medicines([{"name": "unknown pill"}], context())
        self.assertEqual(report["status"], "HOLD")
        self.assertIn("unrecognized_medicine", codes(report))
        self.assertTrue(report["not_assessed"])

    def test_missing_list_cannot_become_explicit_no_medicines(self):
        for value in [None, "", "paracetamol", {}, False]:
            with self.subTest(value=value):
                self.assertEqual(review_medicines(value, context())["status"], "HOLD")
        self.assertEqual(review_medicines([], context())["status"], "NO_MEDICINES_PROPOSED")

    def test_malformed_entries_cannot_disappear(self):
        report = review_medicines([None, {"dose_mg": 500}, {"name": 123}], context())
        self.assertEqual(len(report["medicines"]), 3)
        self.assertEqual(report["status"], "HOLD")

    def test_malformed_dose_remains_blocking_after_re_normalization(self):
        for dose in [True, -1, 0, float("nan"), float("inf"), "500 mg", "as needed"]:
            with self.subTest(dose=dose):
                normalized, errors = normalize_medicines([para(dose_mg=dose)])
                self.assertTrue(errors)
                report = review_medicines(normalized, context())
                self.assertEqual(report["status"], "HOLD")
                self.assertIn("invalid_input", codes(report))
                json.dumps(report, allow_nan=False)

    def test_legacy_draft_remains_conservative(self):
        self.assertEqual(draft_to_medicines(NO_MEDICINES), [])
        self.assertEqual(format_medicines([]), NO_MEDICINES)
        self.assertEqual(review_medicines(draft_to_medicines("Panadol 500mg twice daily"), context())["status"], "HOLD")
        self.assertEqual(review_medicines(draft_to_medicines(""), context())["status"], "HOLD")

    def test_adult_ordinary_boundaries_below_at_above_maximum(self):
        for dose, frequency, blocked in [(999, 4, False), (1000, 4, False), (1001, 4, True), (800, 5, True), (1000, 4.01, True)]:
            with self.subTest(dose=dose, frequency=frequency):
                report = review_medicines([para(dose_mg=dose, frequency_per_day=frequency)], context())
                self.assertEqual("paracetamol_adult_limit" in codes(report), blocked)
                self.assertEqual(report["approval_blocked"], blocked)

    def test_missing_frequency_is_not_dose_verified(self):
        report = review_medicines([para(frequency_per_day=None)], context())
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertIn("dose_not_assessed", codes(report))

    def test_fractional_frequency_requires_schedule_review(self):
        report = review_medicines([para(frequency_per_day=2.5)], context())
        self.assertIn("dose_schedule_review", codes(report))

    def test_no_flags_does_not_claim_dosing_interval_or_full_safety(self):
        report = review_medicines([para()], context())
        self.assertEqual(report["status"], "NO_FLAGS_IN_SUPPORTED_CHECKS")
        self.assertTrue(any("four hours" in item for item in report["not_assessed"]))
        self.assertTrue(any("independent clinical validation" in item for item in report["limitations"]))

    def test_children_and_unknown_age_have_no_adult_clearance(self):
        for age in [0, 12, 17.99, None, True, "unknown"]:
            with self.subTest(age=age):
                report = review_medicines([para()], context(age=age))
                self.assertIn("paracetamol_population_review", codes(report))
                self.assertNotEqual(report["status"], "NO_FLAGS_IN_SUPPORTED_CHECKS")
        self.assertNotIn("paracetamol_population_review", codes(review_medicines([para()], context(age=18))))

    def test_low_or_unknown_weight_does_not_get_ordinary_dose_clearance(self):
        for weight in [49.9, None, 0, True]:
            self.assertIn("paracetamol_weight_review", codes(review_medicines([para()], context(weight_kg=weight))))
        self.assertNotIn("paracetamol_weight_review", codes(review_medicines([para()], context(weight_kg=50))))

    def test_organ_risk_is_explicitly_unassessed(self):
        for field in ["renal_impairment", "liver_disease"]:
            report = review_medicines([para()], context(**{field: True}))
            self.assertEqual(report["status"], "REVIEW_REQUIRED")
            self.assertTrue(any("Organ impairment" in item for item in report["not_assessed"]))

    def test_duration_needs_review_when_missing_or_beyond_three_days(self):
        for duration in [None, 3.01, 7]:
            self.assertIn("paracetamol_duration_review", codes(review_medicines([para(duration_days=duration)], context())))
        self.assertNotIn("paracetamol_duration_review", codes(review_medicines([para(duration_days=3)], context())))

    def test_route_and_combination_doses_are_outside_scope(self):
        for medication in [para(route="IV"), para(route=None), para(name="Panadol Extra")]:
            self.assertIn("dose_not_assessed", codes(review_medicines([medication], context())))

    def test_duplicate_ingredient_across_brands_and_combination_products(self):
        for second in ["Calpol", "Panadol Extra"]:
            report = review_medicines([para(name="Panadol"), {"name": second}], context())
            self.assertIn("duplicate_ingredient", codes(report))
            self.assertEqual(report["status"], "HOLD")
            flag = next(flag for flag in report["flags"] if flag["code"] == "duplicate_ingredient")
            self.assertEqual(flag["citation"], "FDA_ACETAMINOPHEN_DUPLICATES")

    def test_current_proposed_duplicates_receive_distinct_stable_ids(self):
        report = review_medicines([para(id="m1")], context(current_meds=[para(name="Calpol", id="m1")]))
        self.assertEqual(report["medicines"][0]["id"], "m1")
        self.assertNotEqual(report["current_medicines"][0]["id"], "m1")
        flag = next(flag for flag in report["flags"] if flag["code"] == "duplicate_ingredient")
        self.assertEqual(len(flag["medicine_ids"]), 2)

    def test_all_selected_interactions_are_symmetric_across_lists(self):
        pairs = [("rifampicin", "oral contraceptive", "ddi_rifampicin_contraceptive", False),
                 ("warfarin", "ibuprofen", "ddi_warfarin_nsaid", False),
                 ("warfarin", "aspirin", "ddi_warfarin_nsaid", False),
                 ("TMP-SMX", "lisinopril", "ddi_tmp_smx_acei", True)]
        for first, second, code, blocking in pairs:
            for proposed, current in [(first, second), (second, first)]:
                with self.subTest(proposed=proposed, current=current):
                    report = review_medicines([{"name": proposed}], context(current_meds=[current]))
                    flag = next(flag for flag in report["flags"] if flag["code"] == code)
                    self.assertEqual(flag["blocking"], blocking)
                    self.assertEqual(len(flag["medicine_ids"]), 2)
            for order in [[first, second], [second, first]]:
                self.assertIn(code, codes(review_medicines(order, context())))

    def test_generic_contraception_does_not_assert_systemic_hormonal_method(self):
        report = review_medicines([{"name": "rifampicin"}], context(current_meds=["copper IUD"]))
        self.assertNotIn("ddi_rifampicin_contraceptive", codes(report))
        self.assertIn("unrecognized_medicine", codes(report))

    def test_current_current_findings_survive_empty_proposed_list(self):
        report = review_medicines([], context(current_meds=["warfarin", "aspirin"]))
        self.assertIn("ddi_warfarin_nsaid", codes(report))
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_beta_lactam_allergy_requires_clarification_not_assumed_anaphylaxis(self):
        for medication in ["amoxicillin", "Augmentin"]:
            report = review_medicines([{"name": medication}], context(allergies=["penicillin"]))
            self.assertEqual(report["status"], "HOLD")
            flag = next(flag for flag in report["flags"] if flag["code"] == "allergy_match_review")
            self.assertIn("unspecified history does not establish", flag["reason"])
            self.assertEqual(flag["citation"], "DAILYMED_AMOXICILLIN_ALLERGY")

    def test_recorded_intolerance_is_review_not_confirmed_serious_allergy(self):
        report = review_medicines([{"name": "amoxicillin"}], context(allergies=[{"substance": "penicillin", "type": "intolerance", "reaction": "nausea"}]))
        self.assertNotIn("allergy_match_review", codes(report))
        self.assertIn("recorded_intolerance_review", codes(report))
        self.assertFalse(report["approval_blocked"])

    def test_unknown_context_stays_distinct_from_confirmed_none(self):
        unknown = review_medicines([para()], context(allergies=None, current_meds=None, context_confirmed=False))
        self.assertEqual(unknown["status"], "REVIEW_REQUIRED")
        self.assertTrue(any("Allergy history is unknown" in item for item in unknown["not_assessed"]))
        self.assertEqual(review_medicines([para()], context())["status"], "NO_FLAGS_IN_SUPPORTED_CHECKS")

    def test_malformed_history_cannot_become_confirmed_none(self):
        for changes in [{"allergies": "none"}, {"allergies": [{}]}, {"current_meds": "none"}]:
            self.assertEqual(review_medicines([para()], context(**changes))["status"], "HOLD")

    def test_pregnancy_nsaid_week_boundaries(self):
        for weeks, code, blocked in [(19.99, "pregnancy_early_review", False), (20, "pregnancy_nsaid_20_weeks", False), (29.99, "pregnancy_nsaid_20_weeks", False), (30, "pregnancy_nsaid_30_weeks", True)]:
            with self.subTest(weeks=weeks):
                report = review_medicines([{"name": "ibuprofen", "route": "oral"}], context(pregnant=True, gestational_weeks=weeks))
                self.assertIn(code, codes(report))
                self.assertEqual(report["approval_blocked"], blocked)

    def test_pregnancy_unknown_week_or_route_is_unassessed(self):
        for row, weeks, code in [({"name": "ibuprofen", "route": "oral"}, None, "pregnancy_timing_review"), ({"name": "ibuprofen", "route": "ophthalmic"}, 32, "pregnancy_route_review"), ({"name": "ibuprofen"}, 32, "pregnancy_route_review")]:
            report = review_medicines([row], context(pregnant=True, gestational_weeks=weeks))
            self.assertIn(code, codes(report))
            self.assertNotIn("pregnancy_nsaid_30_weeks", codes(report))

    def test_aspirin_exception_requires_explicit_matching_prescribed_regimen(self):
        row = {"name": "aspirin", "dose_mg": 81, "frequency_per_day": 1, "route": "oral"}
        patient = context(pregnant=True, gestational_weeks=32, aspirin_prescribed=True)
        report = review_medicines([row], patient)
        self.assertIn("pregnancy_aspirin_81mg_review", codes(report))
        self.assertFalse(report["approval_blocked"])
        for changed_row, changed_patient in [({**row, "dose_mg": 325}, patient), (row, {**patient, "aspirin_prescribed": None}), ({**row, "frequency_per_day": None}, patient)]:
            self.assertIn("pregnancy_nsaid_30_weeks", codes(review_medicines([changed_row], changed_patient)))

    def test_antimalarial_or_other_pregnancy_suitability_is_not_keyword_cleared(self):
        report = review_medicines([{"name": "Coartem", "indication": "malaria"}], context(pregnant=True, gestational_weeks=12))
        self.assertIn("pregnancy_not_assessed", codes(report))
        self.assertFalse(report["approval_blocked"])

    def test_antibiotics_always_request_indication_specific_review(self):
        for name in ["amoxicillin", "azithromycin", "doxycycline", "ciprofloxacin", "rifampicin"]:
            self.assertIn("antibiotic_indication_review", codes(review_medicines([{"name": name}], context())))
        for indication in ["XDR typhoid", "not XDR typhoid", "severe pneumonia", ""]:
            report = review_medicines([{"name": "azithromycin", "indication": indication}], context())
            self.assertIn("azithromycin_stewardship", codes(report))
            self.assertFalse(report["approval_blocked"])

    def test_formulary_matches_full_product_not_a_single_combination_ingredient(self):
        self.assertFalse(review_medicines([para(name="Panadol")], context(), allowlist=["paracetamol"])["approval_blocked"])
        self.assertTrue(review_medicines([para(name="Panadol Extra")], context(), allowlist=["paracetamol"])["approval_blocked"])
        self.assertTrue(review_medicines([para()], context(), allowlist=[])["approval_blocked"])
        self.assertFalse(review_medicines([para()], context(), allowlist=None)["approval_blocked"])

    def test_every_finding_has_valid_evidence_metadata(self):
        report = review_medicines([para(dose_mg=1500), {"name": "azithromycin"}, {"name": "ibuprofen", "route": "oral"}, {"name": "unknown"}], context(pregnant=True, gestational_weeks=32, current_meds=["warfarin"]))
        for flag in report["flags"]:
            self.assertIn(flag["citation"], SOURCES)
            self.assertEqual(flag["evidence"]["id"], flag["citation"])
            self.assertTrue(flag["evidence"]["section"])
            self.assertEqual(flag["ruleset_version"], RULESET_VERSION)

    def test_reviews_do_not_mutate_inputs_and_fingerprints_bind_clinical_changes(self):
        medicine, patient = [para()], context()
        before = deepcopy((medicine, patient))
        report = review_medicines(medicine, patient)
        self.assertEqual((medicine, patient), before)
        self.assertEqual(report, review_medicines(medicine, patient))
        self.assertEqual(report["input_hash"], review_medicines(normalize_medicines(medicine)[0], patient)["input_hash"])
        for changed in [review_medicines([para(dose_mg=1000)], patient), review_medicines(medicine, context(allergies=["paracetamol"])), review_medicines(medicine, patient, allowlist=["paracetamol"])]:
            self.assertNotEqual(report["input_hash"], changed["input_hash"])

    def test_acknowledgment_and_notes_do_not_clear_findings(self):
        held = review_medicines([para(dose_mg=1500)], context())
        self.assertIsNotNone(approval_error(held, True, "I acknowledge the overdose"))
        review = review_medicines([para()], context(weight_kg=49))
        before = deepcopy(review)
        self.assertIsNotNone(approval_error(review, False, "Review required"))
        self.assertIsNotNone(approval_error(review, True, "ok"))
        self.assertIsNone(approval_error(review, True, "Refer for individualized dose review"))
        self.assertEqual(review, before)

    def test_even_no_flags_requires_ack_but_explicit_no_medicines_does_not(self):
        report = review_medicines([para()], context())
        self.assertIsNotNone(approval_error(report, False, ""))
        self.assertIsNone(approval_error(report, True, ""))
        self.assertIsNone(approval_error(review_medicines([], context()), False, ""))
        self.assertIsNotNone(approval_error(None, True, ""))
        self.assertIsNotNone(approval_error({**report, "ruleset_version": "old"}, True, ""))

    def test_dawa_facade_produces_identical_shared_findings(self):
        medicines, patient = [para(), {"name": "Calpol"}], context()
        self.assertEqual(check_medicines(medicines, patient), review_medicines(medicines, patient)["medicines"])


if __name__ == "__main__":
    unittest.main()
