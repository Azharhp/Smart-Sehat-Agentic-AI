"""Authored location/month fixtures, not local epidemiology or clinical review."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smartsehat_workspace.consultation.epidemiology import FeverContext, context_for
from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.timeutils import local_today


LOCATION = {"country": "Pakistan", "province": "Fictional province", "district": "Fictional district", "observed_on": "2026-01-12", "notes": "Reported fictional exposure", "unknown_reason": ""}


def table():
    return {"review_status": "reviewed", "version": "SOFTWARE-FIXTURE-ONLY", "entries": [{
        "id": "SYN-CONTEXT-1", "country": LOCATION["country"], "province": LOCATION["province"], "district": LOCATION["district"],
        "months": [1], "valid_from": "2026-01-01", "valid_through": "2026-12-31", "condition": "malaria",
        "context": "INVENTED context for automated matching test; no real district risk claim.",
        "reviewed_by": "Automated fixture; not a clinician", "reviewed_on": "2026-01-01", "source": "Invented software-only fixture"}]}


def snapshot(data):
    return data, hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def facts(location=LOCATION):
    return [{"id": "F1", "key": "fever", "value": True, "confirmed": True},
            {"id": "F2", "key": "fever_context", "value": location, "confirmed": True}]


class ContextMatchingTests(unittest.TestCase):
    def test_only_confirmed_context_and_exact_location_month_validity_match(self):
        result = context_for(facts(), snapshot=snapshot(table()))
        self.assertEqual(result["status"], "REVIEWED_CONTEXT")
        self.assertEqual(result["entries"][0]["id"], "SYN-CONTEXT-1")
        for change in ({"country": "Other country"}, {"district": "Different fictional district"}, {"province": "Different province"},
                       {"observed_on": "2026-02-12"}, {"observed_on": "2025-01-12"}):
            self.assertEqual(context_for(facts({**LOCATION, **change}), snapshot=snapshot(table()))["status"], "NO_REVIEWED_MATCH")
        proposed = facts()
        proposed[1]["confirmed"] = False
        self.assertEqual(context_for(proposed, snapshot=snapshot(table()))["status"], "INPUT_REQUIRED")
        no_fever = facts()
        no_fever[0]["value"] = False
        self.assertEqual(context_for(no_fever, snapshot=snapshot(table()))["status"], "NOT_APPLICABLE")

    def test_pending_ambiguous_and_invalid_tables_never_supply_context(self):
        pending = {**table(), "review_status": "pending"}
        self.assertEqual(context_for(facts(), snapshot=snapshot(pending))["entries"], [])
        ambiguous = table()
        ambiguous["entries"].append({**deepcopy(ambiguous["entries"][0]), "id": "SYN-CONTEXT-2"})
        self.assertEqual(context_for(facts(), snapshot=snapshot(ambiguous))["status"], "AMBIGUOUS_REVIEWED_CONTEXT")
        for field, value in (("months", [True]), ("months", [1, 1]), ("months", [13]), ("reviewed_on", "2999-01-01"), ("reviewed_by", "")):
            invalid = table()
            invalid["entries"][0][field] = value
            self.assertEqual(context_for(facts(), snapshot=snapshot(invalid))["status"], "CONFIG_INVALID")

    def test_unknown_location_needs_reason_and_future_date_rejected(self):
        with self.assertRaises(ValueError):
            FeverContext.model_validate({**LOCATION, "district": ""})
        with self.assertRaises(ValueError):
            FeverContext.model_validate({**LOCATION, "observed_on": (local_today() + timedelta(days=1)).isoformat()})
        unknown = {**LOCATION, "district": "", "unknown_reason": "Patient cannot identify district"}
        self.assertEqual(context_for(facts(unknown), snapshot=snapshot(table()))["status"], "LOCATION_UNKNOWN")


class ContextWorkflowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = Store(Path(tmp.name) / "context.db")
        self.cid = self.s.load_ayaan()

    def test_context_survives_restart_readback_and_export_without_changing_urgency(self):
        before = self.s.output(self.cid, "urgency")["level"]
        fact = self.s.confirm_fever_context(self.cid, LOCATION)
        self.assertEqual(self.s.confirm_fever_context(self.cid, LOCATION, fact["id"])["id"], fact["id"])
        with self.assertRaisesRegex(ValueError, "context changed"):
            self.s.confirm_fever_context(self.cid, {**LOCATION, "district": "Changed"})
        packet = Store(self.s.path).review(self.cid)
        self.assertEqual(packet["fever_context"]["status"], "UNAVAILABLE_PENDING_REVIEW")
        for language in ("English", "Urdu"):
            self.assertIn(LOCATION["district"], packet["readbacks"][language]["text"])
            self.assertIn(LOCATION["observed_on"], packet["readbacks"][language]["text"])
        self.assertIn(fact["id"], packet["fever_context"]["fact_ids"])
        self.assertEqual(self.s.output(self.cid, "urgency")["level"], before)
        from smartsehat_workspace.consultation.fhir import bundle
        exported = bundle(self.s.case(self.cid), self.s.facts(self.cid), packet, [], None)
        self.assertIn(LOCATION["district"], json.dumps(exported))

    def test_reference_change_invalidates_packet_draft_reply_and_publication(self):
        original = snapshot(table())
        changed = snapshot({**table(), "version": "CHANGED-FIXTURE"})
        with patch("smartsehat_workspace.consultation.epidemiology.catalogue_snapshot", return_value=original):
            self.s.confirm_fever_context(self.cid, LOCATION)
            packet = self.s.review(self.cid)
            self.s.acknowledge_medcheck(self.cid, self.s.medcheck(self.cid)["version"])
            self.s.readback(self.cid, True, True)
            self.s.send(self.cid)
        with patch("smartsehat_workspace.consultation.epidemiology.catalogue_snapshot", return_value=changed):
            self.assertIsNone(self.s.output(self.cid, "packet"))
            with self.assertRaisesRegex(ValueError, "reference content changed"):
                self.s.reply(self.cid, {"advice": "Stale reference response", "reviewed_fever_catalogue_hash": original[1]})
        from smartsehat_workspace.consultation.pipeline import review as original_review
        def alter_during_review(*args, **kwargs):
            result = original_review(*args, **kwargs)
            result["fever_context"]["catalogue_hash"] = "stale-fixture-hash"
            return result
        with patch("smartsehat_workspace.consultation.pipeline.review", side_effect=alter_during_review):
            with self.assertRaisesRegex(ValueError, "content changed during review"):
                self.s.review(self.cid)
        self.assertIn("SYN-CONTEXT-1", packet["full_text"])

    def test_context_correction_requires_new_readback_before_resend(self):
        fact = self.s.confirm_fever_context(self.cid, LOCATION)
        self.s.review(self.cid)
        self.s.acknowledge_medcheck(self.cid, self.s.medcheck(self.cid)["version"])
        self.s.readback(self.cid, True, True)
        self.s.confirm_fever_context(self.cid, {**LOCATION, "district": "Corrected district"}, fact["id"])
        with self.assertRaises(ValueError):
            self.s.send(self.cid)
        packet = self.s.review(self.cid)
        self.assertIn("Corrected district", packet["full_text"])
        with self.assertRaises(ValueError):
            self.s.send(self.cid)

    def test_worker_draft_stays_unconfirmed_until_explicit_context_confirmation(self):
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ, {"CONSULTATION_DB": self.s.path, "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            source = f"from smartsehat_workspace.consultation.ui import _fever_context\n_fever_context({self.cid!r})\n"
            app = AppTest.from_string(source, default_timeout=25).run()
            app.text_input(key=f"fever_context_province_{self.cid}_new").set_value(LOCATION["province"]).run()
            app.text_input(key=f"fever_context_district_{self.cid}_new").set_value(LOCATION["district"]).run()
            self.assertFalse(any(f["key"] == "fever_context" for f in self.s.facts(self.cid)))
            restarted = AppTest.from_string(source, default_timeout=25).run()
            self.assertEqual(restarted.text_input(key=f"fever_context_district_{self.cid}_new").value, LOCATION["district"])
            restarted.button(key=f"confirm_fever_context_{self.cid}").click().run()
            self.assertEqual(list(restarted.exception), [])
            self.assertTrue(any(f["key"] == "fever_context" for f in self.s.facts(self.cid)))

    def test_doctor_sees_current_context_and_must_review_changed_catalogue(self):
        from streamlit.testing.v1 import AppTest
        original = snapshot(table())
        changed_table = table()
        changed_table["entries"][0]["context"] = "CHANGED SOFTWARE FIXTURE, no real clinical claim"
        changed = snapshot(changed_table)
        with patch.dict(os.environ, {"CONSULTATION_DB": self.s.path, "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            with patch("smartsehat_workspace.consultation.epidemiology.catalogue_snapshot", return_value=original):
                self.s.confirm_fever_context(self.cid, LOCATION)
                self.s.review(self.cid)
                self.s.acknowledge_medcheck(self.cid, self.s.medcheck(self.cid)["version"])
                self.s.readback(self.cid, True, True)
                self.s.send(self.cid)
                source = f"from smartsehat_workspace.consultation.ui import _doctor_case\n_doctor_case({self.cid!r})\n"
                app = AppTest.from_string(source, default_timeout=25).run()
            with patch("smartsehat_workspace.consultation.epidemiology.catalogue_snapshot", return_value=changed):
                app.run()
                self.assertTrue(app.button(key=f"save_doctor_reply_{self.cid}").disabled)
                self.assertTrue(any("CHANGED SOFTWARE FIXTURE" in item.value for item in app.markdown))
                app.button(key=f"review_changes_{self.cid}").click().run()
                self.assertFalse(app.button(key=f"save_doctor_reply_{self.cid}").disabled)
                self.assertEqual(list(app.exception), [])


if __name__ == "__main__":
    unittest.main()
