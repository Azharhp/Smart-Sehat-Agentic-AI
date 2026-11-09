"""Local calendar dates, preserved DOB provenance and guided task measurements."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smartsehat_workspace.consultation.clinical import Age, config
from smartsehat_workspace.consultation.fhir import bundle
from smartsehat_workspace.consultation.measurements import completed_breath_count
from smartsehat_workspace.consultation.orders import LabReport
from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.timeutils import local_today, display_time


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = Store(Path(self.temp.name) / "capture.db")

    def sent_case(self):
        cid = self.s.load_ayaan()
        self.s.review(cid)
        self.s.acknowledge_medcheck(cid, self.s.medcheck(cid)["version"])
        self.s.readback(cid, True, True)
        self.s.send(cid)
        return cid

    def test_local_calendar_boundary_and_timestamp_display(self):
        with patch.dict(config(), {"timezone": "Asia/Karachi"}):
            self.assertEqual(local_today(datetime(2026, 10, 21, 18, 59, tzinfo=timezone.utc)), date(2026, 10, 21))
            self.assertEqual(local_today(datetime(2026, 10, 21, 19, 0, tzinfo=timezone.utc)), date(2026, 10, 22))
            self.assertEqual(display_time("2026-10-21T20:30:00+00:00"), "22 Oct 2026, 01:30 (Asia/Karachi)")
        with patch.dict(config(), {"timezone": "UTC"}):
            self.assertEqual(local_today(datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc)), date(2026, 10, 21))
        with self.assertRaises(ValueError):
            local_today(datetime(2026, 10, 22))

    def test_dob_reference_survives_restart_packet_and_fhir(self):
        reference = date(2026, 10, 22)
        dob = reference - timedelta(days=59)
        age = Age.from_dob(dob, reference)
        self.assertEqual(age.pathway, "young_infant")
        cid = self.s.create("Fictional DOB case", age, "Female", False, "Synthetic", "Urdu", False)
        with patch("smartsehat_workspace.consultation.timeutils.local_today", return_value=reference + timedelta(days=3)):
            resumed = Store(self.s.path)
            restored = resumed.age(cid)
            self.assertEqual(restored.days, 59)
            self.assertEqual(restored.date_of_birth, dob.isoformat())
            self.assertEqual(restored.as_of, reference.isoformat())
            packet = resumed.review(cid)
        self.assertIn("DOB " + dob.isoformat(), packet["readbacks"]["English"]["text"])
        self.assertIn("تاریخ پیدائش", packet["readbacks"]["Urdu"]["text"])
        doc = bundle(resumed.case(cid), resumed.facts(cid), packet, [], None)
        patient = next(entry["resource"] for entry in doc["entry"] if entry["resource"]["resourceType"] == "Patient")
        self.assertEqual(patient["birthDate"], dob.isoformat())
        invalid = {**age.json(), "value": 60}
        before = resumed.facts(cid)
        with self.assertRaises(ValueError):
            resumed.add_fact(cid, "age", invalid)
        self.assertEqual(resumed.facts(cid), before)

    def test_birth_date_and_report_date_use_local_today(self):
        reference = date(2026, 10, 22)
        with patch("smartsehat_workspace.consultation.timeutils.local_today", return_value=reference):
            self.assertEqual(Age.from_dob(date(2026, 10, 21)).days, 1)
            with self.assertRaises(ValueError):
                Age.from_dob(reference + timedelta(days=1))
        with patch("smartsehat_workspace.consultation.orders.local_today", return_value=reference):
            self.assertEqual(LabReport(result="Synthetic issued report", report_date=reference).report_date, reference)
            with self.assertRaises(ValueError):
                LabReport(result="Synthetic future report", report_date=reference + timedelta(days=1))

    def test_task_temperature_normalises_units_and_keeps_original_entry(self):
        cid = self.sent_case()
        self.s.task(cid, "temperature", "Return temperature with the unit on the device")
        tid = self.s.tasks(cid)[0]["task_id"]
        before = self.s.events(cid)
        with self.assertRaises(ValueError):
            self.s.complete_task(cid, tid, {"value": 102.2, "unit": "%"})
        self.assertEqual(self.s.events(cid), before)
        self.s.complete_task(cid, tid, {"value": 102.2, "unit": "F"})
        fact = next(f for f in self.s.facts(cid) if f["key"] == "temperature")
        self.assertEqual((fact["value"], fact["unit"]), (39, "C"))
        response = self.s.tasks(cid)[0]["response"]["value"]
        self.assertEqual((response["entered_value"], response["entered_unit"]), (102.2, "F"))
        self.assertEqual((response["value"], response["unit"]), (39, "C"))

    def test_full_minute_task_rejects_partial_or_fractional_counts(self):
        cid = self.sent_case()
        self.s.task(cid, "resp_rate", "Count a full minute")
        tid = self.s.tasks(cid)[0]["task_id"]
        for value in ({"value": 46, "unit": "/min", "method": "full-minute-tap-counter", "duration_seconds": 59},
                      {"value": 46.5, "unit": "/min", "method": "full-minute-tap-counter", "duration_seconds": 60},
                      {"value": 46, "unit": "/min", "method": "manual-device-entry", "duration_seconds": 60}):
            before = self.s.events(cid)
            with self.assertRaises(ValueError):
                self.s.complete_task(cid, tid, value)
            self.assertEqual(self.s.events(cid), before)
        self.s.complete_task(cid, tid, {"value": 46, "unit": "/min", "method": "full-minute-tap-counter", "duration_seconds": 60})
        result = self.s.tasks(cid)[0]["response"]["value"]
        self.assertEqual(result["duration_seconds"], 60)
        self.assertEqual(result["method"], "full-minute-tap-counter")
        self.assertIn("60-second", next(f for f in self.s.facts(cid) if f["key"] == "resp_rate")["quote"])

    def test_unavailable_measurement_keeps_reason_without_inventing_zero(self):
        cid = self.sent_case()
        self.s.task(cid, "spo2", "Return SpO2 or explain why unavailable")
        tid = self.s.tasks(cid)[0]["task_id"]
        self.s.complete_task(cid, tid, {"value": "", "unit": "%"}, "Device unavailable")
        response = self.s.tasks(cid)[0]["response"]
        self.assertIsNone(response["value"])
        self.assertEqual(response["reason"], "Device unavailable")
        self.assertIsNone(next(f for f in self.s.facts(cid) if f["key"] == "spo2")["value"])

    def test_counter_results_bind_case_task_and_full_duration(self):
        valid = {"case_id": "C-one:T-one", "count": 46, "complete": True, "duration_seconds": 60}
        self.assertTrue(completed_breath_count(valid, "C-one:T-one"))
        self.assertFalse(completed_breath_count(valid, "C-two:T-one"))
        self.assertFalse(completed_breath_count(valid, "C-one:T-two"))
        self.assertFalse(completed_breath_count({**valid, "complete": False}, "C-one:T-one"))
        self.assertFalse(completed_breath_count({**valid, "duration_seconds": 30}, "C-one:T-one"))


if __name__ == "__main__":
    unittest.main()
