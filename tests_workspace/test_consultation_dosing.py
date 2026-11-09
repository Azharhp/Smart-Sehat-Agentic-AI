"""Software-only product matching fixtures; every bound below is invented.

These fixtures are not dosing guidance and are never added to the app's table.
"""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smartsehat_workspace.consultation.clinical import Age
from smartsehat_workspace.consultation.dosing import check_amount
from smartsehat_workspace.consultation.store import Store


PRODUCT = {"formulation": "tablet", "strength": "FICTIONAL STRENGTH A", "route": "oral"}


def synthetic_table():
    return {"status": "reviewed", "entries": [{"id": "SYNTHETIC-TEST-ONLY", "generic": "synthetic-ingredient",
        **PRODUCT, "min_age_days": 0, "max_age_days": 50000, "min_mg_per_kg_per_dose": 100.,
        "max_mg_per_kg_per_dose": 200., "reviewed_by": "Automated fixture; no real clinical review",
        "reviewed_on": "2026-01-01", "source": "Invented software fixture, not medical guidance"}]}


class ProductMatchingTests(unittest.TestCase):
    def setUp(self):
        self.age = Age.from_input(14, "months")

    def check(self, table=None, product=None, ingredients=None, amount=1370., weight=10., age=None):
        return check_amount(table if table is not None else synthetic_table(),
                            ingredients if ingredients is not None else ["synthetic-ingredient"],
                            amount, weight, age or self.age, PRODUCT if product is None else product)

    def test_pending_table_never_approves_amount(self):
        result = self.check({"status": "pending", "entries": synthetic_table()["entries"]})
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["flags"][0]["rule_id"], "RX-DOSE-PENDING")

    def test_exact_product_and_age_required_and_combinations_do_not_borrow_band(self):
        for product in ({**PRODUCT, "formulation": "syrup"}, {**PRODUCT, "strength": "FICTIONAL STRENGTH B"},
                        {**PRODUCT, "route": "intravenous"}):
            self.assertEqual(self.check(product=product)["status"], "NO_MATCH")
        self.assertEqual(self.check(product={})["status"], "INPUT_REQUIRED")
        self.assertEqual(self.check(ingredients=["synthetic-ingredient", "other-ingredient"])["status"], "NO_MATCH")
        table = synthetic_table()
        table["entries"][0]["max_age_days"] = 100
        self.assertEqual(self.check(table)["status"], "NO_MATCH")

    def test_incomplete_ambiguous_and_invalid_reviewed_tables_fail_closed(self):
        missing = synthetic_table()
        missing["entries"][0].pop("strength")
        future = synthetic_table()
        future["entries"][0]["reviewed_on"] = "2999-01-01"
        unordered = synthetic_table()
        unordered["entries"][0]["min_mg_per_kg_per_dose"] = 999.
        for table in (missing, future, unordered):
            self.assertEqual(self.check(table)["status"], "UNAVAILABLE")
            self.assertEqual(self.check(table)["flags"][0]["rule_id"], "RX-DOSE-CONFIG")
        overlap = synthetic_table()
        overlap["entries"].append({**deepcopy(overlap["entries"][0]), "id": "OTHER-OVERLAPPING-TEST"})
        self.assertEqual(self.check(overlap)["status"], "NO_MATCH")

    def test_only_entered_amount_is_compared_without_a_recommended_dose(self):
        result = self.check()
        self.assertEqual(result["status"], "WITHIN_REVIEWED_BAND")
        self.assertEqual(result["entry"]["id"], "SYNTHETIC-TEST-ONLY")
        self.assertNotIn("dose_mg", result)
        self.assertNotIn("recommended", result)
        self.assertEqual(self.check(amount=5000.)["status"], "OUTSIDE_REVIEWED_BAND")
        self.assertEqual(self.check(amount=None)["status"], "INPUT_REQUIRED")
        self.assertEqual(self.check(weight=None)["status"], "INPUT_REQUIRED")
        for amount in (True, float("nan"), float("inf"), -1, 0):
            with self.assertRaises(ValueError):
                self.check(amount=amount)


class RegimenMatchingTests(unittest.TestCase):
    def setUp(self):
        self.age = Age.from_input(14, "months")
        self.table = synthetic_table()
        self.table["entries"][0].update({"max_mg_per_dose": 1800., "max_mg_per_day": 5000., "max_mg_per_kg_per_day": 450.,
            "min_doses_per_day": 1., "max_doses_per_day": 3., "min_duration_days": 1., "max_duration_days": 10., "indication": "FICTIONAL INDICATION"})
        self.regimen = {"doses_per_day": 2., "duration_days": 5., "indication": "FICTIONAL INDICATION", "as_needed": False}

    def check(self, *, table=None, regimen=None, amount=1370.):
        return check_amount(table if table is not None else self.table, ["synthetic-ingredient"], amount, 10., self.age, PRODUCT, self.regimen if regimen is None else regimen)

    def test_every_configured_check_and_stricter_daily_cap_is_used(self):
        self.assertEqual(self.check()["regimen_status"], "WITHIN_CONFIGURED_LIMITS")
        scenarios = [({"doses_per_day": 4.}, 1370., "RX-FREQUENCY"), ({"duration_days": 11.}, 1370., "RX-DURATION"),
                     ({}, 1900., "RX-ABSOLUTE-DOSE"), ({"doses_per_day": 3., "as_needed": True}, 1600., "RX-DAILY-MAX")]
        for change, amount, rule in scenarios:
            result = self.check(regimen={**self.regimen, **change}, amount=amount)
            self.assertEqual(result["regimen_status"], "OUTSIDE_REVIEWED_LIMITS")
            self.assertIn(rule, {f["rule_id"] for f in result["flags"]})
        absolute = deepcopy(self.table)
        absolute["entries"][0]["max_mg_per_day"] = 2500.
        self.assertIn("RX-DAILY-MAX", {f["rule_id"] for f in self.check(table=absolute)["flags"]})

    def test_missing_indication_or_limits_never_approve_complete_regimen(self):
        self.assertEqual(self.check(regimen={**self.regimen, "indication": ""})["status"], "INPUT_REQUIRED")
        self.assertEqual(self.check(regimen={**self.regimen, "indication": "OTHER FICTIONAL INDICATION"})["status"], "NO_MATCH")
        self.assertEqual(self.check(regimen={**self.regimen, "doses_per_day": None})["regimen_status"], "INPUT_REQUIRED")
        self.assertEqual(self.check(table=synthetic_table())["regimen_status"], "INCOMPLETE_REVIEWED_CHECKS")

    def test_invalid_regimen_values_and_partial_reviewed_ranges_fail_closed(self):
        for value in (True, float("inf"), float("nan"), -1., 0., .5):
            with self.assertRaises(ValueError):
                self.check(regimen={**self.regimen, "doses_per_day": value})
        invalid = deepcopy(self.table)
        invalid["entries"][0].pop("max_duration_days")
        self.assertEqual(self.check(table=invalid)["status"], "UNAVAILABLE")


class PrescriptionWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.s = Store(Path(temporary.name) / "products.db")
        self.cid = self.s.load_ayaan()
        self.s.review(self.cid)
        self.s.acknowledge_medcheck(self.cid, self.s.medcheck(self.cid)["version"])
        self.s.readback(self.cid, True, True)
        self.s.send(self.cid)

    def test_product_and_check_provenance_survive_restart_without_approving_pending_table(self):
        reply = {"advice": "Synthetic doctor-entered plan", "medicine": "Panadol", "dose_mg": 1370.,
                 "prescription_product": PRODUCT, "prescription_regimen": {"doses_per_day": 2., "duration_days": 5., "indication": "FICTIONAL", "as_needed": True}}
        before = self.s.facts(self.cid)
        with self.assertRaisesRegex(ValueError, "prescription safety flags"):
            self.s.reply(self.cid, reply)
        self.assertEqual(self.s.facts(self.cid), before)
        self.s.reply(self.cid, {**reply, "safety_acknowledged": True})
        saved = Store(self.s.path).latest_reply(self.cid)
        self.assertEqual(saved["prescription_product"], PRODUCT)
        self.assertEqual(saved["prescription_checks"]["dose_check"]["status"], "UNAVAILABLE")
        plan = next(f["value"] for f in self.s.facts(self.cid) if f["key"] == "doctor_plan")
        self.assertEqual(plan["prescription_product"], PRODUCT)
        self.assertEqual(plan["medicine"], "Panadol")
        self.assertEqual(saved["prescription_regimen"], reply["prescription_regimen"])
        self.assertEqual(plan["prescription_regimen"], reply["prescription_regimen"])

    def test_product_draft_survives_restart_and_acknowledgement_resets_after_product_edit(self):
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ, {"CONSULTATION_DB": self.s.path, "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            source = f"from smartsehat_workspace.consultation.ui import _doctor_case\n_doctor_case({self.cid!r})\n"
            app = AppTest.from_string(source, default_timeout=25).run()
            app.text_input(key=f"rx_medicine_{self.cid}").set_value("Panadol").run()
            app.selectbox(key=f"rx_product_formulation_{self.cid}").set_value("tablet").run()
            app.text_input(key=f"rx_product_strength_{self.cid}").set_value(PRODUCT["strength"]).run()
            app.selectbox(key=f"rx_product_route_{self.cid}").set_value("oral").run()
            app.number_input(key=f"rx_regimen_doses_per_day_{self.cid}").set_value(2).run()
            app.number_input(key=f"rx_regimen_duration_days_{self.cid}").set_value(5.).run()
            app.text_input(key=f"rx_regimen_indication_{self.cid}").set_value("FICTIONAL INDICATION").run()
            self.assertEqual(self.s.draft(self.cid, "doctor_reply")["prescription_product"], PRODUCT)
            label = "Doctor has independently reviewed the prescription flags and unvalidated dose check"
            next(c for c in app.checkbox if c.label == label).check().run()
            app.text_input(key=f"rx_product_strength_{self.cid}").set_value("FICTIONAL STRENGTH B").run()
            self.assertFalse(next(c for c in app.checkbox if c.label == label).value)
            restarted = AppTest.from_string(source, default_timeout=25).run()
            self.assertEqual(restarted.text_input(key=f"rx_product_strength_{self.cid}").value, "FICTIONAL STRENGTH B")
            self.assertEqual(restarted.selectbox(key=f"rx_product_route_{self.cid}").value, "oral")
            self.assertEqual(restarted.number_input(key=f"rx_regimen_doses_per_day_{self.cid}").value, 2.)
            self.assertEqual(restarted.number_input(key=f"rx_regimen_duration_days_{self.cid}").value, 5.)
            next(c for c in restarted.checkbox if c.label == label).check().run()
            restarted.number_input(key=f"rx_regimen_duration_days_{self.cid}").set_value(6.).run()
            self.assertFalse(next(c for c in restarted.checkbox if c.label == label).value)
            self.assertEqual(list(restarted.exception), [])


if __name__ == "__main__":
    unittest.main()
