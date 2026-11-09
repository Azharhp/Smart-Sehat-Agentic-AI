"""Cache compatibility, renewed handoffs and simulated routing regressions."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from smartsehat_workspace.consultation import ai
from smartsehat_workspace.consultation.clinical import Age, config
from smartsehat_workspace.consultation.metrics import effort
from smartsehat_workspace.consultation.privacy import Cipher, PREFIX
from smartsehat_workspace.consultation.routing import select_reviewer
from smartsehat_workspace.consultation.store import Store
from scripts.warm_consultation_cache import warm


class CachePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for mock in (patch.object(ai, "ROOT", self.root),
                     patch("smartsehat_workspace.consultation.privacy.media_cipher", return_value=Cipher(str(self.root / "media")))):
            mock.start()
            self.addCleanup(mock.stop)

    def test_warmed_age_specific_requests_are_consumed_and_encrypted(self):
        with patch.object(ai, "_bounded_json", side_effect=AssertionError("No provider calls")):
            files = warm(include_image=False)
            for pathway in (None, "child", "adult"):
                facts, mode = ai.extract("Cough for 4 days. Fever for 2 days. No vomiting.", pathway=pathway)
                self.assertEqual(mode, "cached")
                self.assertEqual({f["key"]: f["value"] for f in facts},
                                 {"cough": True, "cough_days": 4, "fever": True, "fever_days": 2, "vomiting": False})
            facts, mode = ai.extract("khansi aur bukhar, no vomiting", pathway="child")
            self.assertEqual(mode, "cached")
            self.assertIs(next(f["value"] for f in facts if f["key"] == "vomiting"), False)
            _, mode = ai.extract("Another uncached statement", pathway="child")
            self.assertEqual(mode, "offline deterministic fallback")
        self.assertTrue(files)
        self.assertTrue(all(p.read_text(encoding="utf-8").startswith(PREFIX) for p in files))
        self.assertEqual(list(self.root.rglob("*.db")), [])

    def test_existing_response_preserved_and_plaintext_migrates(self):
        payload = {"transcript": "Synthetic fixture"}
        original = {"facts": [{"key": "cough", "value": False, "quote": "Synthetic", "confidence": .8}]}
        fixture = {"facts": []}
        path = ai.json_cache_path("extract", payload, ai.Extraction)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(original), encoding="utf-8")
        ai.seed_json_cache("extract", payload, ai.Extraction, fixture)
        self.assertTrue(path.read_text(encoding="utf-8").startswith(PREFIX))
        self.assertFalse(ai._cached(ai.Extraction, path)["facts"][0]["value"])
        encrypted = path.read_bytes()
        ai.seed_json_cache("extract", payload, ai.Extraction, fixture)
        self.assertEqual(path.read_bytes(), encrypted)
        ai.seed_json_cache("extract", payload, ai.Extraction, fixture, overwrite=True)
        self.assertEqual(ai._cached(ai.Extraction, path), fixture)

    def test_invalid_manifest_has_no_partial_writes(self):
        invalid = [
            {"synthetic": False, "entries": []},
            {"synthetic": True, "entries": [{"task": "extract", "input": {}, "output": {"facts": "invalid"}}]},
        ]
        for manifest in invalid:
            with self.assertRaises(ValueError):
                warm(manifest=manifest, include_image=False)
        with self.assertRaises(ValueError):
            ai.seed_json_cache("../escape", {}, ai.Extraction, {"facts": []})
        self.assertFalse((self.root / "data").exists())


class HandoffTimingTests(unittest.TestCase):
    @staticmethod
    def event(seq, minute, kind, handoff=None, **data):
        stamp = f"2026-10-22T00:{minute:02}:00+00:00"
        if handoff:
            data["handoff_id"] = handoff
        if kind == "HANDOFF_STARTED":
            data["sent_at"] = stamp
        return {"seq": seq, "ts": stamp, "type": kind, "data": data}

    def test_new_handoff_ignores_previous_replies_and_late_stale_ack(self):
        events = [self.event(1, 0, "HANDOFF_STARTED", "A"), self.event(2, 1, "DOCTOR_SEEN", "A"),
                  self.event(3, 2, "DOCTOR_REPLY", "A"), self.event(4, 10, "HANDOFF_STARTED", "B"),
                  self.event(5, 11, "DOCTOR_SEEN", "A")]
        case = {"sent": events[3]["ts"], "clock_minutes": 999}
        pending = effort(case, events)
        self.assertIsNone(pending["seconds_to_acknowledge"])
        self.assertIsNone(pending["seconds_to_reply"])
        self.assertEqual(pending["handoffs"][0]["seconds_to_reply"], 120)
        events += [self.event(6, 13, "DOCTOR_SEEN", "B"), self.event(7, 15, "DOCTOR_REPLY", "B")]
        current = effort(case, events)
        self.assertEqual(current["seconds_to_acknowledge"], 180)
        self.assertEqual(current["seconds_to_reply"], 300)
        self.assertEqual(current["current_handoff_id"], "B")
        self.assertEqual(current["simulated_clock_minutes"], 999)

    def test_legacy_microsecond_packet_event_and_withdrawal(self):
        packet = self.event(1, 20, "PACKET_SENT")
        packet["ts"] = "2026-10-22T00:20:00.010+00:00"
        events = [packet, self.event(2, 25, "DOCTOR_REPLY")]
        case = {"sent": "2026-10-22T00:20:00+00:00", "clock_minutes": 0}
        result = effort(case, events)
        self.assertEqual(len(result["handoffs"]), 1)
        self.assertEqual(result["seconds_to_reply"], 300)
        events.append(self.event(3, 26, "SHARING_WITHDRAWN"))
        result = effort({**case, "sent": None}, events)
        self.assertIsNone(result["seconds_to_reply"])
        self.assertTrue(result["handoffs"][0]["sharing_withdrawn"])


class RoutingOperationsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.s = Store(Path(temporary.name) / "operations.db")

    def sent_case(self):
        cid = self.s.load_ayaan()
        self.s.review(cid)
        self.s.acknowledge_medcheck(cid, self.s.medcheck(cid)["version"])
        self.s.readback(cid, True, True)
        self.s.send(cid)
        return cid

    def assigned(self, cid):
        return (self.s.routing_status(cid).get("reviewer") or {}).get("id")

    def test_availability_reassigns_without_resetting_handoff_and_survives_restart(self):
        cid = self.sent_case()
        handoff = self.s.handoff(cid)
        self.assertEqual(self.assigned(cid), "SYN-DOC-1")
        self.s.reviewer_availability("SYN-DOC-1", "offline", "Synthetic roster change")
        self.assertEqual(self.assigned(cid), "SYN-DOC-2")
        self.assertEqual(self.s.handoff(cid), handoff)
        count = len(self.s.events(cid))
        self.s.reviewer_availability("SYN-DOC-1", "offline", "Synthetic roster change")
        self.s.tick()
        self.assertEqual(len(self.s.events(cid)), count)
        resumed = Store(self.s.path)
        self.assertEqual(resumed.reviewers()[0]["availability"], "offline")
        with resumed.connect() as db:
            self.assertTrue(db.execute("SELECT data FROM consultation_roster_events").fetchone()[0].startswith(PREFIX))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM consultation_roster_events")
        self.s.reviewer_availability("SYN-DOC-2", "busy")
        self.assertIsNone(self.assigned(cid))
        self.s.reviewer_availability("SYN-DOC-1", "available")
        self.assertEqual(self.assigned(cid), "SYN-DOC-1")

    def test_escalation_selects_next_reviewer_and_ack_stops_later_actions(self):
        cid = self.sent_case()
        self.s.advance_clock(cid, 10)
        self.assertEqual(self.assigned(cid), "SYN-DOC-2")
        self.assertEqual(len([e for e in self.s.events(cid) if e["type"] == "ESCALATION"]), 1)
        self.s.record(cid, "DOCTOR_SEEN", {"handoff_id": self.s.handoff(cid)["handoff_id"]}, "doctor (simulated)")
        self.s.advance_clock(cid, 30)
        self.assertEqual(len([e for e in self.s.events(cid) if e["type"] == "ESCALATION"]), 1)

    def test_returned_task_renews_handoff_and_rejects_stale_doctor_actions(self):
        cid = self.sent_case()
        first = self.s.handoff(cid)
        self.s.reply(cid, {"advice": "Synthetic response before returned measurement"})
        self.s.task(cid, "spo2", "Return an oxygen reading")
        self.s.complete_task(cid, self.s.tasks(cid)[0]["task_id"], 95)
        second = self.s.handoff(cid)
        self.assertNotEqual(first["handoff_id"], second["handoff_id"])
        self.assertIsNone(self.s.effort(cid)["seconds_to_reply"])
        with self.assertRaisesRegex(ValueError, "new worker handoff"):
            self.s.record(cid, "DOCTOR_SEEN", {"handoff_id": first["handoff_id"]})
        with self.assertRaisesRegex(ValueError, "new worker handoff"):
            self.s.reply(cid, {"advice": "Stale draft", "reviewed_handoff_id": first["handoff_id"]})
        self.s.advance_clock(cid, 10)
        escalated = [e for e in self.s.events(cid) if e["type"] == "ESCALATION"]
        self.assertEqual(len(escalated), 1)
        self.assertEqual(escalated[0]["data"]["handoff_id"], second["handoff_id"])
        self.s.reply(cid, {"advice": "Reviewed new measurement", "reviewed_handoff_id": second["handoff_id"]})
        self.assertIsNotNone(self.s.effort(cid)["seconds_to_reply"])

    def test_withdrawal_and_specialty_limits_preserved(self):
        cid = self.sent_case()
        self.s.consent(cid, False)
        self.s.reviewer_availability("SYN-DOC-1", "offline")
        self.s.advance_clock(cid, 40)
        self.assertIsNone(self.s.case(cid)["sent"])
        self.assertFalse(any(e["type"] == "ESCALATION" for e in self.s.events(cid)))
        self.s.reviewer_availability("SYN-DOC-3", "offline")
        route = select_reviewer({"data": {"pregnant": True, "language": "Urdu"}}, Age.from_input(24, "years"), self.s.reviewers())
        self.assertIsNone(route["reviewer"])

    def test_urgency_policy_is_used_and_invalid_policy_rolls_back_send(self):
        cid = self.s.load_ayaan()
        self.s.review(cid)
        self.s.acknowledge_medcheck(cid, self.s.medcheck(cid)["version"])
        self.s.readback(cid, True, True)
        level = self.s.output(cid, "urgency")["level"]
        policy = deepcopy(config())
        policy["routing"]["escalation_by_urgency"][level] = [3, 3, 8]
        with patch("smartsehat_workspace.consultation.routing.config", return_value=policy):
            with self.assertRaisesRegex(ValueError, "increasing escalation"):
                self.s.send(cid)
        self.assertIsNone(self.s.case(cid)["sent"])
        self.assertFalse(any(e["type"] == "HANDOFF_STARTED" for e in self.s.events(cid)))
        policy["routing"]["escalation_by_urgency"][level] = [3, 6, 8]
        with patch("smartsehat_workspace.consultation.routing.config", return_value=policy):
            self.s.send(cid)
        with self.s.connect() as db:
            jobs = [self.s._decode(r["data"]) for r in db.execute("SELECT data FROM consultation_jobs WHERE kind LIKE 'escalation-%' ORDER BY kind")]
        self.assertEqual([j["delay_minutes"] for j in jobs], [3, 6, 8])
        self.assertTrue(all(j["urgency"] == level for j in jobs))

    def test_roster_controls_and_assignment_filter_render(self):
        from streamlit.testing.v1 import AppTest
        cid = self.sent_case()
        with patch.dict(os.environ, {"CONSULTATION_DB": str(self.s.path), "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            app = AppTest.from_string("from smartsehat_workspace.consultation.ui import doctor\ndoctor()\n", default_timeout=25).run()
            app.selectbox(key="reviewer_status_SYN-DOC-1_configured").set_value("offline").run()
            app.button(key="save_reviewer_SYN-DOC-1_configured").click().run()
            self.assertEqual(list(app.exception), [])
            self.assertEqual(self.assigned(cid), "SYN-DOC-2")
            app.selectbox(key="doctor_reviewer_filter").set_value("SYN-DOC-1").run()
            self.assertFalse(any(c.label.startswith("Doctor queue") for c in app.selectbox))
            app.selectbox(key="doctor_reviewer_filter").set_value("SYN-DOC-2").run()
            self.assertTrue(any(cid in c.options[0] for c in app.selectbox if c.label.startswith("Doctor queue")))
            self.assertTrue(any("Assigned: Simulated general reviewer" in item.value for item in app.caption))
            self.s.reviewer_availability("SYN-DOC-2", "offline")
            app.selectbox(key="doctor_reviewer_filter").set_value("All reviewers").run()
            self.assertTrue(any("No available simulated reviewer" in item.value for item in app.warning))
            self.assertEqual(list(app.exception), [])


if __name__ == "__main__":
    unittest.main()
