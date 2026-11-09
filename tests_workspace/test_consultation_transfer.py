"""Transfer/delivery guards use invented data and mocked outbound networking."""
import base64
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch
import zipfile

from smartsehat_workspace.consultation.clinical import danger_questions
from smartsehat_workspace.consultation.privacy import Cipher, PREFIX
from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.safety_capture import screen_scope
from smartsehat_workspace.consultation.transport import package, chunks, assemble, inspect_package, encoded


class TransferTests(unittest.TestCase):
    def fixture(self):
        return {"version": 7, "verification": {"status": "VERIFIED"}, "stale": False,
                "text": "Fictional compact text with 37.2 C.",
                "full_text": "All fictional clinical text; no truncation.\nنہیں",
                "appendix": {"facts": [{"id": "F1", "value": False}], "media": [
                    {"event_id": 12, "type": "PHOTO_ATTACHED", "path": "C:/local/private/photo.png.enc"},
                    {"event_id": 13, "type": "AUDIO_ATTACHED", "path": "C:/local/private/missing.wav.enc"}]},
                "readbacks": {"English": {"text": "Complete English"}, "Urdu": {"text": "مکمل خلاصہ"}}}

    def test_lossless_original_media_and_text_and_portable_paths(self):
        original = os.urandom(180_000)
        def reader(path):
            if "missing" in path:
                raise OSError("Fixture unavailable")
            return original, ".png"
        blob, stats = package(self.fixture(), media_reader=reader)
        checked = inspect_package(blob)
        manifest, packet = checked["manifest"], checked["packet"]
        self.assertEqual(packet["full_text"], self.fixture()["full_text"])
        self.assertNotIn("C:/local", json.dumps(packet))
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            photo = next(row for row in manifest["media"] if row["available"])
            self.assertEqual(archive.read(photo["file"]), original)
            self.assertEqual(archive.read("readback-Urdu.txt").decode(), "مکمل خلاصہ")
        self.assertEqual(stats["media_available"], 1)
        self.assertEqual(stats["media_total"], 2)
        self.assertFalse(manifest["media"][1]["available"])

    def test_resume_out_of_order_duplicate_parts_reports_missing_then_reassembles(self):
        blob, _ = package(self.fixture(), media_reader=lambda path: (os.urandom(100_000), ".png"))
        parts = chunks(blob)
        self.assertGreater(len(parts), 1)
        partial, status = assemble([parts[-1], parts[-1]])
        self.assertIsNone(partial)
        self.assertIn(0, status["missing"])
        restored, status = assemble(list(reversed(parts)) + [parts[0]])
        self.assertEqual(restored, blob)
        self.assertEqual(status["missing"], [])

    def test_corruption_mixed_transfer_and_malformed_parts_fail_closed(self):
        blob, _ = package(self.fixture())
        parts = chunks(blob)
        bad = deepcopy(parts)
        bad[0]["data"] = base64.b64encode(b"x" * len(blob)).decode()
        with self.assertRaisesRegex(ValueError, "checksum"):
            assemble(bad)
        other = deepcopy(parts[0])
        other["transfer_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "different"):
            assemble(parts + [other])
        for malformed in ([None], [{}], "not a list"):
            with self.assertRaises(ValueError):
                assemble(malformed)

    def test_archive_unsafe_paths_modified_content_and_bad_manifest_are_rejected(self):
        blob, _ = package(self.fixture())
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        for edit in (lambda data: data.update({"../outside.txt": b"no"}),
                     lambda data: data.update({"SBAR.txt": b"altered"}),
                     lambda data: data.update({"manifest.json": b"[]"})):
            content = deepcopy(files)
            edit(content)
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                for name, value in content.items():
                    archive.writestr(name, value)
            with self.assertRaises(ValueError):
                inspect_package(output.getvalue())
        with self.assertRaises(ValueError):
            package({**self.fixture(), "stale": True})


class DeliveryAndQuickScreenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for replacement in (patch("smartsehat_workspace.consultation.store.ROOT", self.root),
                            patch("smartsehat_workspace.consultation.privacy.media_cipher", return_value=Cipher(str(self.root / "media"))),
                            patch.dict(os.environ, {"CONSULTATION_DISABLE_SCHEDULER": "1", "CONSULTATION_DELIVERY_URL": "https://gateway.invalid/deliver", "CONSULTATION_DELIVERY_TOKEN": "synthetic-token"})):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.service = Store(self.root / "transfer.db")
        self.cid = self.service.load_ayaan()

    def sent(self):
        packet = self.service.review(self.cid)
        self.service.acknowledge_medcheck(self.cid, self.service.medcheck(self.cid)["version"])
        self.service.readback(self.cid, True, True)
        self.service.send(self.cid)
        return packet

    def queue(self):
        self.sent()
        return self.service.queue_delivery(self.cid, "case_packet", "fictional-care-team", "care_team")

    def client(self, response=None, error=None):
        factory = MagicMock()
        client = factory.return_value.__enter__.return_value
        if error:
            client.post.side_effect = error
        else:
            client.post.return_value = response or Mock(status_code=202, content=b"{}", json=lambda: {"accepted": True, "receipt_id": "fixture-receipt"})
        return factory, client

    def test_delivery_needs_current_sent_case_confirmation_and_exact_idempotency(self):
        with self.assertRaises(ValueError):
            self.service.queue_delivery(self.cid, "case_packet", "fictional-care-team", "care_team")
        identity = self.queue()
        self.assertEqual(identity, self.service.queue_delivery(self.cid, "case_packet", "fictional-care-team", "care_team"))
        factory, client = self.client()
        with patch("httpx.Client", factory):
            with self.assertRaises(ValueError):
                self.service.dispatch_delivery(self.cid, identity)
            self.assertFalse(client.post.called)
            first = self.service.dispatch_delivery(self.cid, identity, True)
            second = self.service.dispatch_delivery(self.cid, identity, True)
        self.assertEqual(first["status"], "gateway accepted")
        self.assertEqual(second["status"], "gateway accepted")
        self.assertEqual(client.post.call_count, 1)
        self.assertEqual(client.post.call_args.kwargs["headers"]["Idempotency-Key"], identity)
        self.assertFalse(factory.call_args.kwargs["follow_redirects"])
        self.assertFalse(factory.call_args.kwargs["trust_env"])
        with self.service.connect() as db:
            row = db.execute("SELECT data FROM consultation_events WHERE type='DELIVERY_QUEUED'").fetchone()[0]
            self.assertTrue(row.startswith(PREFIX))

    def test_stale_and_withdrawn_cases_do_not_call_gateway(self):
        identity = self.queue()
        factory, client = self.client()
        self.service.add_fact(self.cid, "worker_request", "New fictional question")
        with patch("httpx.Client", factory), self.assertRaises(ValueError):
            self.service.dispatch_delivery(self.cid, identity, True)
        self.assertFalse(client.post.called)
        self.service.consent(self.cid, False)
        with patch("httpx.Client", factory), self.assertRaises(ValueError):
            self.service.dispatch_delivery(self.cid, identity, True)
        self.assertFalse(client.post.called)

    def test_timeout_and_success_without_receipt_stay_unknown_and_reuse_key(self):
        identity = self.queue()
        factory, client = self.client(error=TimeoutError("synthetic-token must never leak"))
        with patch("httpx.Client", factory):
            unknown = self.service.dispatch_delivery(self.cid, identity, True)
        self.assertEqual(unknown["status"], "outcome unknown")
        self.assertNotIn("synthetic-token", json.dumps(unknown))
        factory, client = self.client(response=Mock(status_code=200, content=b"{}", json=lambda: {}))
        with patch("httpx.Client", factory):
            self.assertEqual(self.service.dispatch_delivery(self.cid, identity, True)["status"], "outcome unknown")
        self.assertEqual(client.post.call_args.kwargs["headers"]["Idempotency-Key"], identity)

    def test_cancelled_and_insecure_gateway_requests_never_dispatch(self):
        identity = self.queue()
        self.service.cancel_delivery(self.cid, identity)
        factory, client = self.client()
        with patch("httpx.Client", factory), self.assertRaises(ValueError):
            self.service.dispatch_delivery(self.cid, identity, True)
        with patch.dict(os.environ, {"CONSULTATION_DELIVERY_URL": "http://gateway.invalid"}), patch("httpx.Client", factory), self.assertRaises(ValueError):
            self.service.dispatch_delivery(self.cid, identity, True)
        self.assertFalse(client.post.called)

    def test_emergency_prealert_does_not_wait_for_full_history(self):
        self.service.add_fact(self.cid, "cannot_drink", True)
        self.service.emergency_referral(self.cid, "SYN-THQ")
        identity = self.service.queue_delivery(self.cid, "emergency_prealert", "fictional-facility", "care_team")
        row = next(row for row in self.service.deliveries(self.cid) if row["delivery_id"] == identity)
        self.assertEqual(row["payload"]["content"]["urgency"]["level"], "RED")
        self.assertEqual(row["payload"]["content"]["facility"]["id"], "SYN-THQ")

    def test_patient_guidance_is_current_then_stale_after_worker_update(self):
        self.sent()
        self.service.reply(self.cid, {"advice": "Fictional doctor advice", "counselling_script": "Fictional counselling"})
        identity = self.service.queue_delivery(self.cid, "patient_guidance", "fictional-patient", "sms")
        row = next(row for row in self.service.deliveries(self.cid) if row["delivery_id"] == identity)
        self.assertEqual(row["payload"]["content"]["counselling_script"], "Fictional counselling")
        self.service.measurement(self.cid, "spo2", 89, "%")
        factory, client = self.client()
        with patch("httpx.Client", factory), self.assertRaises(ValueError):
            self.service.dispatch_delivery(self.cid, identity, True)
        self.assertFalse(client.post.called)

    def response(self, answers):
        return {"case_id": self.cid, "scope": screen_scope(self.service.case(self.cid), self.service.facts(self.cid)),
                "answers": answers, "elapsed_seconds": 4.25}

    def test_quick_positive_interrupt_is_atomic_and_duplicate_does_not_append(self):
        response = self.response({"cannot_drink": True})
        self.assertTrue(self.service.record_quick_screen(self.cid, response))
        count = len(self.service.facts(self.cid, False))
        self.assertEqual(self.service.case(self.cid)["stage"], "EMERGENCY")
        self.assertEqual(self.service.output(self.cid, "urgency")["level"], "RED")
        self.assertFalse(self.service.record_quick_screen(self.cid, response))
        self.assertEqual(len(self.service.facts(self.cid, False)), count)
        capture = next(e["data"] for e in self.service.events(self.cid) if e["type"] == "QUICK_DANGER_CAPTURE")
        self.assertTrue(capture["device_reported"])
        self.assertFalse(capture["independently_observed"])

    def test_quick_stale_foreign_incomplete_and_invalid_answers_are_rejected(self):
        response = self.response({"cannot_drink": True})
        self.service.add_fact(self.cid, "cannot_drink", None, reason_missing="Fixture unsure")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.record_quick_screen(self.cid, response)
        for answers in ({"not_a_danger": True}, {"cannot_drink": "yes"}, {"cannot_drink": False}):
            with self.assertRaises(ValueError):
                self.service.record_quick_screen(self.cid, self.response(answers))
        with self.assertRaises(ValueError):
            self.service.record_quick_screen(self.cid, {**self.response({"cannot_drink": True}), "elapsed_seconds": float("nan")})
        current = {q["key"]: None for q in danger_questions(self.service.age(self.cid), False)}
        self.assertTrue(self.service.record_quick_screen(self.cid, self.response(current)))
        self.assertTrue(self.service.danger_complete(self.cid))
        self.assertNotEqual(self.service.output(self.cid, "urgency")["level"], "GREEN")


if __name__ == "__main__":
    unittest.main()
