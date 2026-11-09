"""Danger-screen source/review/observation guards; not clinical validation."""
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
import zipfile

from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.privacy import Cipher
from smartsehat_workspace.consultation.content_review import review_pack
from smartsehat_workspace.consultation.danger_content import EMERGENCY_WORDING, audio_manifest, inline_prompt, review_archive
from smartsehat_workspace.consultation.speech_audio import save_generated_prompt


def synthetic_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(8000)
        clip.writeframes(b"\0\0" * 800)
    return output.getvalue()


class DangerCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for replacement in (patch("smartsehat_workspace.consultation.store.ROOT", self.root),
                            patch("smartsehat_workspace.consultation.speech_audio.ROOT", self.root),
                            patch("smartsehat_workspace.consultation.privacy.media_cipher", return_value=Cipher(str(self.root / "media")))):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.service = Store(self.root / "danger.db")
        self.cid = self.service.load_ayaan()

    def capture(self):
        self.service.record_quick_screen(self.cid, {"case_id": self.cid, "scope": self.service.quick_screen_snapshot(self.cid)["scope"],
            "answers": {"cannot_drink": True}, "elapsed_seconds": 3.2})
        return next(event for event in self.service.events(self.cid) if event["type"] == "QUICK_DANGER_CAPTURE")

    def report(self, event):
        return {"capture_event": event["seq"], "observer": "SYNTHETIC observer fixture", "participant_code": "TEST-ONLY",
            "device": "Invented test device", "browser": "Mocked test browser", "procedure": "trained repeat",
            "observed_seconds": 32., "expected_outcome": "positive danger reported", "wording_understood": False,
            "notes": "Automated fixture only; not a real human trial or clinical review."}

    def test_observer_reports_require_actual_capture_and_explicit_confirmation_without_claiming_validation(self):
        event = self.capture()
        report = self.report(event)
        with self.assertRaises(ValueError):
            self.service.report_danger_observation(self.cid, report)
        with self.assertRaises(ValueError):
            self.service.report_danger_observation(self.cid, {**report, "capture_event": event["seq"] + 1000}, True)
        self.service.report_danger_observation(self.cid, report, True)
        self.service.report_danger_observation(self.cid, report, True)
        events = [event for event in self.service.events(self.cid) if event["type"] == "DANGER_OBSERVATION_REPORTED"]
        self.assertEqual(len(events), 1)
        saved = events[0]["data"]
        self.assertFalse(saved["reported_under_30_seconds"])
        self.assertFalse(saved["wording_understood"])
        self.assertTrue(saved["outcome_matched"])
        self.assertFalse(saved["observer_identity_verified"])
        self.assertFalse(saved["observation_independently_verified"])
        self.assertEqual(self.service.case(self.cid)["stage"], "EMERGENCY")

    def test_old_content_and_invalid_or_wrong_case_observation_are_rejected(self):
        event = self.capture()
        report = self.report(event)
        for seconds in (float("nan"), float("inf"), -1., 0., True):
            with self.assertRaises(ValueError):
                self.service.report_danger_observation(self.cid, {**report, "observed_seconds": seconds}, True)
        with patch.dict(EMERGENCY_WORDING, {"English": "Changed synthetic wording fixture"}), self.assertRaisesRegex(ValueError, "current"):
            self.service.report_danger_observation(self.cid, report, True)
        with patch("smartsehat_workspace.consultation.danger_content.audio_manifest", return_value=([{"changed_audio_fixture": True}], {})), self.assertRaisesRegex(ValueError, "current"):
            self.service.report_danger_observation(self.cid, report, True)
        other = self.service.create("Fictional other", self.service.age(self.cid), "unknown", False, "Synthetic", "Urdu", False)
        with self.assertRaises(ValueError):
            self.service.report_danger_observation(other, report, True)

    def test_danger_and_emergency_review_requires_the_relevant_role_and_current_exact_content(self):
        target = next(row for row in review_pack()["targets"] if row["scope"] == "emergency_response")
        report = {"scope": target["scope"], "content_hash": target["content_hash"], "reviewer": "SYNTHETIC fixture, not a physician",
                  "role": "native-speaker", "reviewed_on": "2026-01-01", "decision": "changes requested", "notes": "Automated fixture only"}
        with self.assertRaisesRegex(ValueError, "role"):
            self.service.report_content_review(report)
        with patch.dict(EMERGENCY_WORDING, {"Urdu": "Changed synthetic Urdu fixture"}), self.assertRaisesRegex(ValueError, "hash"):
            self.service.report_content_review({**report, "role": "physician"})
        self.service.report_content_review({**report, "role": "physician"})
        self.assertFalse(self.service.content_reviews()[0]["reviewer_identity_verified"])
        self.assertTrue(self.service.content_reviews()[0]["reported_only"])

    def test_offline_prompt_is_exact_text_bound_and_review_archive_contains_audio_and_diagrams(self):
        from smartsehat_workspace.consultation.clinical import config
        text = config()["danger_signs"]["child"][0]["ur"]
        save_generated_prompt(text, "Urdu", synthetic_wav(), "Synthetic offline-engine fixture; unreviewed")
        self.assertTrue(inline_prompt(text, "Urdu").startswith("data:audio/wav;base64,"))
        self.assertIsNone(inline_prompt(text + " changed fixture", "Urdu"))
        manifest, files = audio_manifest()
        self.assertEqual(sum(row["available"] for row in manifest), 1)
        self.assertEqual(next(iter(files.values())), synthetic_wav())
        with zipfile.ZipFile(io.BytesIO(review_archive())) as archive:
            package = json.loads(archive.read("review.json"))
            self.assertEqual({target["scope"] for target in package["targets"]}, {"danger_screen", "emergency_response", "danger_audio"})
            self.assertEqual(package["clinical_approval"], "pending")
            self.assertTrue(any(name.startswith("diagrams/") for name in archive.namelist()))
            self.assertTrue(any(name.startswith("audio/Urdu/") for name in archive.namelist()))
            self.assertIn("report.schema.json", archive.namelist())


if __name__ == "__main__":
    unittest.main()
