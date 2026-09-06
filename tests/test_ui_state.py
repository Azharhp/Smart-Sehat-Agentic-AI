"""Case-scoped UI state acceptance tests."""
from __future__ import annotations

import unittest

from smartsehat import ui_state


class EncounterStateTests(unittest.TestCase):
    def test_case_drafts_are_isolated_and_restored_without_rebinding(self):
        state = {}
        case_a = ui_state.register(
            state, "consultation", "CASE-A", thread_id="CASE-A", patient_id="PATIENT-A",
            facility_id="FACILITY-A", actor_id="WORKER-A",
        )
        ui_state.set_draft(state, "consultation", "CASE-A", "followups", {"pain": "yes"})
        ui_state.set_draft(state, "consultation", "CASE-A", "transcript", "draft A")
        ui_state.set_draft(state, "consultation", "CASE-A", "medicine_edits", ["medicine A"])

        case_b = ui_state.register(
            state, "consultation", "CASE-B", thread_id="CASE-B", patient_id="PATIENT-B",
            facility_id="FACILITY-B", actor_id="WORKER-B",
        )
        self.assertEqual(case_b["drafts"], {})
        self.assertEqual(ui_state.current(state, "consultation")["patient_id"], "PATIENT-B")

        restored = ui_state.select(state, "consultation", "CASE-A")
        self.assertEqual(restored["drafts"]["followups"], {"pain": "yes"})
        self.assertEqual(restored["drafts"]["transcript"], "draft A")
        self.assertEqual(restored["drafts"]["medicine_edits"], ["medicine A"])
        self.assertEqual(restored["patient_id"], "PATIENT-A")
        self.assertEqual(state["active_consultation_id"], "CASE-A")
        self.assertEqual(case_a["patient_id"], "PATIENT-A")

    def test_ownership_fields_cannot_be_changed_by_sidebar_selection(self):
        state = {}
        ui_state.register(
            state, "dawa", "DAWA-A", thread_id="DAWA-A", patient_id="PATIENT-A",
            facility_id="FACILITY-A", actor_id="WORKER-A",
        )
        state["selected_patient_id"] = "PATIENT-B"
        for field in ("patient_id", "facility_id", "actor_id", "encounter_id"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ui_state.update(state, "dawa", "DAWA-A", **{field: "CHANGED"})
        self.assertEqual(ui_state.current(state, "dawa")["patient_id"], "PATIENT-A")

    def test_backend_normalized_gate_values_replace_composite_ui_drafts(self):
        state = {}
        ui_state.register(
            state, "consultation", "CASE-A", thread_id="CASE-A", patient_id="PATIENT-A",
            facility_id="FACILITY-A", actor_id="WORKER-A",
        )
        ui_state.set_draft(state, "consultation", "CASE-A", "medicine_edits",
                           [{"name": "paracetamol"}])
        normalized = [{"id": "MED-1", "name": "paracetamol", "dose_mg": 500}]
        context = {"weight_kg": 49, "context_confirmed": True}
        ui_state.reconcile_gate_drafts(
            state, "consultation", "CASE-A",
            {"proposed_medicines": normalized, "medication_context": context},
        )
        drafts = ui_state.current(state, "consultation")["drafts"]
        self.assertEqual(drafts["medicine_edits"], normalized)
        self.assertEqual(drafts["patient_context"], context)

        ui_state.register(
            state, "dawa", "DAWA-A", thread_id="DAWA-A", patient_id=None,
            facility_id="FACILITY-A", actor_id="WORKER-A",
        )
        ui_state.reconcile_gate_drafts(
            state, "dawa", "DAWA-A", {"medicines": normalized, "patient": context},
        )
        dawa_drafts = ui_state.current(state, "dawa")["drafts"]
        self.assertEqual(dawa_drafts["medicines"], normalized)
        self.assertEqual(dawa_drafts["patient_context"], context)

    def test_action_scoped_reconciliation_preserves_other_unsaved_composite(self):
        state = {}
        ui_state.register(
            state, "consultation", "CASE-A", thread_id="CASE-A", patient_id="PATIENT-A",
            facility_id="FACILITY-A", actor_id="WORKER-A",
        )
        unsaved_context = {"allergies": ["penicillin"], "context_confirmed": False}
        ui_state.set_draft(state, "consultation", "CASE-A", "patient_context",
                           unsaved_context)
        normalized = [{"id": "MED-1", "name": "amoxicillin"}]
        ui_state.reconcile_gate_drafts(
            state, "consultation", "CASE-A",
            {"proposed_medicines": normalized,
             "medication_context": {"allergies": []}},
            {"proposed_medicines"},
        )
        drafts = ui_state.current(state, "consultation")["drafts"]
        self.assertEqual(drafts["medicine_edits"], normalized)
        self.assertEqual(drafts["patient_context"], unsaved_context)


if __name__ == "__main__":
    unittest.main()
