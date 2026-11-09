"""Focused integration checks for unified case totals, freshness and map coverage."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from smartsehat_workspace import audit
from smartsehat_workspace.consultation.clinical import Age
from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.district_data import load_cases, filter_cases, map_counts


class DistrictDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.legacy = str(Path(self.tmp.name) / "legacy.db")
        self.path = str(Path(self.tmp.name) / "consultations.db")
        self.env = patch.dict(os.environ, {"CONSULTATION_DB": self.path, "CONSULTATION_DISABLE_SCHEDULER": "1"})
        self.env.start()
        audit.init_db(self.legacy)
        self.store = Store(self.path)
        self.first = self.store.create("Fictional current", Age.from_input(28, "years"), "Female", False, "Synthetic village A", "English", True)
        self.store.add_fact(self.first, "severe_breathlessness", True)
        self.store.add_fact(self.first, "severe_breathlessness", False)
        self.second = self.store.create("Fictional unmapped", Age.from_input(30, "years"), "Male", False, "Unconfigured village", "English", True)
        # The same ID in a different source is a separate stored encounter;
        # multiple audit/output rows in one encounter must not multiply it.
        audit.create_case(self.first, "P1", "W1", "BHU-0442", path=self.legacy)
        audit.update_case(self.first, path=self.legacy, started_at=(datetime.now(timezone.utc) - timedelta(days=90)).isoformat(), triage_level="YELLOW", outcome="routine_referral")
        audit.create_case("L2", "P2", "W1", "BHU-0511", path=self.legacy)
        audit.log_event("L2", "ONE", {"synthetic": True}, path=self.legacy)
        audit.log_event("L2", "TWO", {"synthetic": True}, path=self.legacy)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_one_row_per_source_encounter_with_filters_and_unmapped_cases(self):
        cases, _ = load_cases(self.legacy, self.store)
        self.assertEqual(len(cases), 4)
        self.assertEqual(len(cases[cases.case_id == self.first]), 2)
        self.assertEqual(cases.loc[(cases.case_id == self.first) & (cases.source == "Consultation"), "urgency"].iloc[0], "AMBER")
        self.assertEqual(len(filter_cases(cases, "Last 30 days")), 3)
        self.assertEqual(len(filter_cases(cases, source="Consultation")), 2)
        self.assertEqual(len(filter_cases(cases, source="Legacy ledger", urgency="AMBER")), 1)
        self.assertEqual(int(map_counts(cases).Cases.sum()), 3)
        self.assertEqual(len(cases[cases.lat.isna()]), 1)
        self.assertEqual(len(filter_cases(cases, query="Unconfigured")), 1)
        self.store.create("Fictional new", Age.from_input(35, "years"), "Male", False, "Synthetic village B", "English", True)
        fresh, _ = load_cases(self.legacy, self.store)
        self.assertEqual(len(fresh), 5)
        self.assertEqual(int(map_counts(fresh).Cases.sum()), 4)

    def test_dashboard_renders_filters_empty_states_and_fresh_totals(self):
        with patch.object(audit, "DB_PATH", self.legacy):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "smartsehat_workspace/app.py"), default_timeout=25)
            app.session_state["workflow"] = "District View"
            app.run()
            self.assertEqual(list(app.exception), [])
            self.assertEqual(next(m.value for m in app.metric if m.label == "Total cases"), "4")
            self.assertEqual(next(m.value for m in app.metric if m.label == "Consultation cases"), "2")
            self.assertTrue(any(s.value == "District Case Map" for s in app.subheader))
            self.assertFalse(any(s.label == "Patient" for s in app.selectbox))
            self.store.create("Fictional newly saved", Age.from_input(40, "years"), "Male", False, "Synthetic village", "English", True)
            next(b for b in app.button if b.label == "Refresh").click().run()
            self.assertEqual(next(m.value for m in app.metric if m.label == "Total cases"), "5")
            app.selectbox(key="district_source").set_value("Consultation").run()
            self.assertEqual(next(m.value for m in app.metric if m.label == "Total cases"), "3")
            app.text_input(key="district_search").set_value("No matching case").run()
            self.assertEqual(list(app.exception), [])
            self.assertEqual(next(m.value for m in app.metric if m.label == "Total cases"), "0")
            app.text_input(key="district_search").set_value("Fictional current").run()
            app.selectbox(key="district_open_case").set_value(self.first).run()
            next(b for b in app.button if b.label == "Open selected consultation").click().run()
            self.assertEqual(list(app.exception), [])
            self.assertEqual(app.radio(key="workflow").value, "Consultation")
            self.assertEqual(app.query_params["case"], [self.first])


if __name__ == "__main__":
    unittest.main()
