"""A01 regressions for every configured deterministic RED category."""
import unittest
from unittest.mock import patch

from smartsehat.agents import emergency_node
from smartsehat.emergency import build_emergency_protocol
from smartsehat.rules_triage import evaluate


class EmergencyProtocolTests(unittest.TestCase):
    CASES = {
        "chest_pain_with_ACS_features": "chest pain with sweating",
        "severe_breathlessness": "patient has severe breathlessness",
        "altered_consciousness_or_fits": "patient is unconscious",
        "pregnancy_with_bleeding": "pregnant patient with bleeding",
        "infant_lt_2mo_with_fever": "newborn with fever",
    }

    def test_every_red_category_uses_safe_unsupported_referral_without_medicine(self):
        for expected_rule, transcript in self.CASES.items():
            with self.subTest(rule=expected_rule):
                triage = evaluate({"transcript_confirmed": transcript})
                self.assertEqual(triage["level"], "RED")
                self.assertEqual(triage["triggered_rules"][0], expected_rule)
                protocol = build_emergency_protocol(triage["level"], triage["triggered_rules"])
                self.assertEqual(protocol["status"], "UNSUPPORTED_EMERGENCY_PROTOCOL")
                self.assertEqual(protocol["required_action"], "REQUIRES_URGENT_REFERRAL")
                self.assertIn(expected_rule, protocol["triggered_rules"])
                self.assertEqual(protocol["medicine_orders"], [])
                text = protocol["instruction"].casefold()
                self.assertNotIn("aspirin", text)
                self.assertNotRegex(text, r"\b\d+\s*(mg|mcg|g)\b")
                with patch("smartsehat.agents.audit.log_event") as log:
                    graph_output = emergency_node({"case_id": "synthetic",
                                                   "triage_level": triage["level"],
                                                   "triage_rules": triage["triggered_rules"]})
                self.assertEqual(graph_output["emergency_protocol"], protocol)
                self.assertEqual(graph_output["prescription_draft"], protocol["instruction"])
                self.assertEqual(graph_output["proposed_medicines"], [])
                self.assertEqual(log.call_args.args[2], protocol)

    def test_non_red_input_cannot_be_misrepresented_as_red_protocol(self):
        protocol = build_emergency_protocol("YELLOW", ["fever_gt_3days"])
        self.assertEqual(protocol["status"], "REQUIRES_CLINICAL_REVIEW")
        self.assertEqual(protocol["medicine_orders"], [])


if __name__ == "__main__":
    unittest.main()
