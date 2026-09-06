"""Offline regression checks for medication widgets and their data contract."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from streamlit.testing.v1 import AppTest
from smartsehat.medication_ui import _clean_medicines


def run_ui(source: str) -> AppTest:
    bootstrap = (
        "import sys\nimport streamlit as st\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from smartsehat.medication_ui import medication_editor, patient_context_editor, render_medication_review\n"
    )
    return AppTest.from_string(bootstrap + source, default_timeout=20).run()


def checkbox(at: AppTest, label: str):
    return next(item for item in at.checkbox if item.label == label)


class MedicationEditorTests(unittest.TestCase):
    def test_nullable_numeric_values_are_json_safe_and_pack_strength_is_not_a_dose(self):
        records = [
            {"id": "m1", "name": "Example", "strength": "500 mg", "dose_mg": float("nan"),
             "frequency_per_day": pd.NA, "duration_days": float("inf")},
            {"id": "blank", "name": " ", "dose_mg": None},
            {"name": "Other", "dose_mg": -1, "frequency_per_day": True, "duration_days": "3"},
        ]
        cleaned = _clean_medicines(records)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0]["id"], "m1")
        self.assertEqual(cleaned[0]["strength"], "500 mg")
        self.assertIsNone(cleaned[0]["dose_mg"])
        self.assertIsNone(cleaned[0]["frequency_per_day"])
        self.assertIsNone(cleaned[0]["duration_days"])
        self.assertIsNone(cleaned[1]["dose_mg"])
        self.assertIsNone(cleaned[1]["frequency_per_day"])
        self.assertEqual(cleaned[1]["duration_days"], 3)
        json.dumps(cleaned, allow_nan=False)

    def test_empty_and_duplicate_name_editors_render_with_namespaced_keys(self):
        at = run_ui('''
st.session_state["empty"] = medication_editor([], "case-empty")
st.session_state["duplicate"] = medication_editor([
    {"id": "m1", "name": "Example", "strength": "500 mg"},
    {"id": "m2", "name": "Example", "strength": "250 mg"},
], "case-duplicates")
''')
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["empty"], [])
        self.assertEqual(len(at.session_state["duplicate"]), 2)
        self.assertIsNone(at.session_state["duplicate"][0]["dose_mg"])
        self.assertTrue(any("administered each time in mg" in item.value for item in at.caption))


class PatientContextTests(unittest.TestCase):
    def test_default_context_remains_unknown_not_zero_or_false(self):
        at = run_ui('st.session_state["result"] = patient_context_editor({}, "case-unknown")\n')
        self.assertEqual(len(at.exception), 0)
        result = at.session_state["result"]
        for field in ("age", "weight_kg", "allergies", "current_meds", "pregnant",
                      "gestational_weeks", "aspirin_prescribed", "renal_impairment", "liver_disease"):
            self.assertIsNone(result[field], field)
        self.assertIs(result["context_confirmed"], False)
        self.assertEqual([item.value for item in at.selectbox], ["Unknown"] * 3)
        self.assertEqual([item.value for item in at.number_input], [None, None])

    def test_explicit_empty_histories_are_distinct_from_unknown(self):
        at = run_ui('st.session_state["result"] = patient_context_editor({}, "case-known")\n')
        checkbox(at, "Allergy history confirmed (empty list means no known allergies)").check().run()
        checkbox(at, "Current medicines confirmed (empty list means taking none)").check().run()
        result = at.session_state["result"]
        self.assertEqual(result["allergies"], [])
        self.assertEqual(result["current_meds"], [])
        self.assertIsNone(result["pregnant"])

    def test_structured_current_doses_survive_unchanged_names(self):
        context = {"current_meds": [{"id": "c1", "name": "Example", "dose_mg": 250,
                                     "frequency_per_day": 2}], "sex": "F"}
        original = deepcopy(context)
        at = run_ui(f'st.session_state["result"] = patient_context_editor({context!r}, "case-existing")\n')
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["result"]["current_meds"], original["current_meds"])
        self.assertEqual(at.session_state["result"]["sex"], "F")
        at.text_area[0].set_value("Changed medicine\nSecond medicine").run()
        self.assertEqual(at.session_state["result"]["current_meds"], ["Changed medicine", "Second medicine"])

    def test_structured_allergy_reaction_survives_context_save(self):
        context = {"allergies": [{"substance": "penicillin", "type": "allergy",
                                  "reaction": "anaphylaxis"}]}
        at = run_ui(f'st.session_state["result"] = patient_context_editor({context!r}, "case-allergy")\n')
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["result"]["allergies"], context["allergies"])
        allergy_input = next(item for item in at.text_input if item.label.startswith("Allergies"))
        self.assertEqual(allergy_input.value, "penicillin")
        self.assertTrue(any("anaphylaxis" in item.value for item in at.caption))
        allergy_input.set_value("amoxicillin, ibuprofen").run()
        self.assertEqual(at.session_state["result"]["allergies"], ["amoxicillin", "ibuprofen"])

    def test_context_edit_requires_new_encounter_confirmation(self):
        at = run_ui('st.session_state["result"] = patient_context_editor({}, "case-confirm")\n')
        checkbox(at, "I reviewed this patient context for this encounter").check().run()
        self.assertIs(at.session_state["result"]["context_confirmed"], True)
        at.number_input[0].set_value(35.0).run()
        self.assertIs(at.session_state["result"]["context_confirmed"], False)

    def test_pregnancy_context_is_explicit_and_conditional(self):
        at = run_ui('st.session_state["result"] = patient_context_editor({}, "case-pregnancy")\n')
        next(item for item in at.selectbox if item.label == "Pregnant").select("Yes").run()
        self.assertTrue(any("Gestational age" in item.label for item in at.number_input))
        prescribed = next(item for item in at.selectbox if "Clinician prescribed" in item.label)
        self.assertEqual(prescribed.value, "Unknown")
        prescribed.select("Yes").run()
        self.assertIs(at.session_state["result"]["aspirin_prescribed"], True)
        next(item for item in at.selectbox if item.label == "Pregnant").select("No").run()
        self.assertIsNone(at.session_state["result"]["aspirin_prescribed"])
        self.assertFalse(any("Clinician prescribed" in item.label for item in at.selectbox))


class ReviewPresentationTests(unittest.TestCase):
    def test_current_medicines_are_visible_and_global_flags_are_not_repeated(self):
        finding = {"id": "finding-1", "reason": "Duplicate ingredient requires reconciliation",
                   "severity": "RED", "blocking": True}
        report = {"status": "HOLD", "medicines": [{"name": "Panadol", "flags": [finding]}],
                  "current_medicines": [{"name": "Paracetamol", "dose_mg": 500,
                                         "frequency_per_day": 3}], "flags": [finding]}
        at = run_ui(f"render_medication_review({report!r})\n")
        self.assertEqual(len(at.exception), 0)
        rendered = "\n".join(item.value for item in at.markdown)
        self.assertIn("Reported current medicines", rendered)
        self.assertIn("Paracetamol", rendered)
        self.assertEqual(rendered.count(finding["reason"]), 1)
        self.assertTrue(any("not an instruction to stop" in item.value for item in at.caption))

    def test_duplicate_medicines_show_status_dose_and_deduplicated_evidence(self):
        source = {"id": "EXAMPLE_SOURCE", "title": "Example clinical source",
                  "url": "https://example.org/source", "section": "Section 2"}
        policy = {"id": "SMARTSEHAT_RULE_SCOPE", "title": "Application review scope",
                  "url": "docs/MEDICATION_EVIDENCE.md", "section": "Coverage"}
        row = {"name": "Example", "generic": "example", "strength": "500 mg",
               "dose_mg": 250, "frequency_per_day": 2,
               "flags": [{"severity": "RED", "blocking": True, "reason": "Example finding",
                          "evidence": source}, {"severity": "YELLOW", "reason": "Limited coverage", "evidence": policy}]}
        report = {"status": "HOLD", "ruleset_version": "test-1", "medicines": [row, deepcopy(row)],
                  "not_assessed": ["Other medicines"], "coverage": {"recognized": 2},
                  "limitations": ["Example limited rule set"]}
        at = run_ui(f"render_medication_review({report!r})\n")
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("HOLD" in item.value for item in at.error))
        self.assertTrue(any("Rule version: test-1" in item.value for item in at.caption))
        self.assertTrue(any("Application review policy" in item.value for item in at.caption))
        links = at.get("link_button")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].proto.url, "https://example.org/source")
        rendered = "\n".join(item.value for item in at.markdown)
        self.assertIn("Administered dose (mg / dose): 250", rendered)
        self.assertIn("Pack / label strength: 500 mg", rendered)
        self.assertIn("Other medicines", rendered)

    def test_all_non_hold_statuses_have_honest_bounded_messages(self):
        for status in ("REVIEW_REQUIRED", "NO_FLAGS_IN_SUPPORTED_CHECKS", "NO_MEDICINES_PROPOSED"):
            with self.subTest(status=status):
                at = run_ui(f'render_medication_review({{"status": {status!r}}})\n')
                self.assertEqual(len(at.exception), 0)
                messages = [item.value for item in at.info] + [item.value for item in at.warning]
                self.assertEqual(len(messages), 1)
                self.assertEqual(len(at.success), 0)


if __name__ == "__main__":
    unittest.main()
