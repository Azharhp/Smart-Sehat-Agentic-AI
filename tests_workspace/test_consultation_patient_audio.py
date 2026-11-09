"""Complete-guidance recording guards; fixtures are not clinical instructions."""
from copy import deepcopy
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from smartsehat_workspace.consultation.patient import complete_guidance
from smartsehat_workspace.consultation.privacy import Cipher, save_media
from smartsehat_workspace.consultation.store import Store


def wav_fixture():
    content = io.BytesIO()
    with wave.open(content, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(8000)
        clip.writeframes(b"\0\0" * 800)
    return content.getvalue()


def sample():
    return {"advice": "SYNTHETIC written advice; preserve the complete wording.", "prescription": "Fictional written prescription, not a patient dose.",
            "medication_schedule": [{"medicine": "Fictional product", "dose": "1.25 invented units", "morning": True, "noon": False,
                                     "evening": False, "night": True, "notes": "SYNTHETIC exact schedule warning."}],
            "feeding": "SYNTHETIC feeding wording.", "ors": "SYNTHETIC ORS wording, no preparation recipe.",
            "counselling_script": "SYNTHETIC worker counselling wording.", "return_if": "SYNTHETIC exact return warning.",
            "local_tests": ["Glucose"], "lab_tests": ["Blood count"], "referral": "Synthetic DHQ referral hospital",
            "request_call": True, "status": "awaiting_information", "safety_acknowledged": True}


class CompleteGuidanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for replacement in (patch("smartsehat_workspace.consultation.store.ROOT", self.root),
                            patch("smartsehat_workspace.consultation.privacy.media_cipher", return_value=Cipher(str(self.root / "media")))):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.s = Store(self.root / "patient-audio.db")
        self.cid = self.s.load_ayaan()
        self.s.review(self.cid)
        self.s.acknowledge_medcheck(self.cid, self.s.medcheck(self.cid)["version"])
        self.s.readback(self.cid, True, True)
        self.s.send(self.cid)

    def recording(self, name="generated.wav"):
        folder = self.root / "assets/uploaded/consultation" / self.cid
        folder.mkdir(parents=True, exist_ok=True)
        return str(save_media(folder / name, wav_fixture()))

    def bind(self, reply, language="Urdu"):
        path = self.recording()
        proposal = {"path": path, "text_hash": hashlib.sha256(complete_guidance(reply, language).encode()).hexdigest(), "language": language}
        self.s.record(self.cid, "DOCTOR_AUDIO_ATTACHED", {**proposal, "purpose": "generated complete patient guidance draft"}, "doctor (simulated)")
        self.s.save_draft(self.cid, "doctor_reply", {"guidance_audio_path": path, "guidance_audio_proposal": proposal})
        return {**reply, "guidance_audio_path": path, "guidance_audio_confirmed": True}

    def test_script_covers_all_written_fields_without_truncation_or_checker_dose_inference(self):
        reply = sample()
        reply.update({"local_language": "ڈاکٹر کی تحریر کردہ مثال۔", "advice": "SYNTHETIC " * 1000,
                      "dose_mg": 987654321, "prescription_regimen": {"doses_per_day": 7654321},
                      "followup_due": "2099-12-31T12:34:56Z"})
        for language in ("English", "Urdu"):
            text = complete_guidance(reply, language)
            for key in ("advice", "local_language", "prescription", "feeding", "ors", "counselling_script", "return_if", "referral"):
                self.assertIn(reply[key], text)
            for value in ("1.25 invented units", "SYNTHETIC exact schedule warning.", "Fictional product", "Glucose", "Blood count"):
                self.assertIn(value, text)
            self.assertNotIn("987654321", text)
            self.assertNotIn("7654321", text)
            self.assertNotIn("2099-12-31", text)
        with self.assertRaises(ValueError):
            complete_guidance(reply, "Unvalidated language")

    def test_each_written_instruction_change_blocks_generated_audio_then_original_saves(self):
        reply = self.bind(sample())
        changes = {"feeding": "Changed feeding wording", "return_if": "Changed warning", "status": "final", "request_call": False,
                   "local_tests": [], "lab_tests": [], "medication_schedule": [{**sample()["medication_schedule"][0], "night": False}]}
        for key, value in changes.items():
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "instructions changed"):
                self.s.reply(self.cid, {**reply, key: value})
        self.assertIsNone(self.s.latest_reply(self.cid))
        self.s.reply(self.cid, reply)
        saved = self.s.latest_reply(self.cid)
        self.assertEqual(saved["guidance_audio_provenance"]["language"], "Urdu")
        self.assertEqual(saved["guidance_audio_provenance"]["text_hash"], hashlib.sha256(complete_guidance(saved, "Urdu").encode()).hexdigest())

    def test_removing_draft_metadata_or_claiming_manual_cannot_bypass_generation_event(self):
        reply = self.bind(sample())
        self.s.save_draft(self.cid, "doctor_reply", {})
        with self.assertRaisesRegex(ValueError, "instructions changed"):
            self.s.reply(self.cid, {**reply, "ors": "Changed ORS wording", "guidance_audio_proposal": None,
                                   "guidance_audio_provenance": {"origin": "manual"}})
        self.assertIsNone(self.s.latest_reply(self.cid))

    def test_replacement_human_recording_remains_supported_with_explicit_listening_confirmation(self):
        reply = self.bind(sample())
        human = self.recording("human.wav")
        self.s.save_draft(self.cid, "doctor_reply", {"guidance_audio_path": human})
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.s.reply(self.cid, {**reply, "guidance_audio_path": human, "guidance_audio_confirmed": False})
        self.s.reply(self.cid, {**reply, "guidance_audio_path": human, "feeding": "Human checked changed feeding wording."})
        self.assertEqual(self.s.latest_reply(self.cid)["guidance_audio_path"], human)
        self.assertNotIn("guidance_audio_provenance", self.s.latest_reply(self.cid))

    def test_doctor_ui_generation_uses_complete_script_and_does_not_save_response(self):
        from streamlit.testing.v1 import AppTest
        draft = sample()
        draft["response_label"] = "Awaiting information / results"
        snapshot = self.s.review_snapshot(self.cid)
        draft.update({"reviewed_" + key: value for key, value in snapshot.items()})
        self.s.save_draft(self.cid, "doctor_reply", draft)
        with patch.dict(os.environ, {"CONSULTATION_DB": self.s.path, "CONSULTATION_DISABLE_SCHEDULER": "1"}), \
             patch("smartsehat_workspace.consultation.speech_audio.synthesize", return_value=(None, "Synthetic unavailable audio fixture; no provider call")) as generate:
            source = f"from smartsehat_workspace.consultation.ui import _doctor_case\n_doctor_case({self.cid!r})\n"
            app = AppTest.from_string(source, default_timeout=35).run()
            self.assertEqual(list(app.exception), [])
            app.button(key=f"generate_guidance_audio_{self.cid}").click().run()
            self.assertEqual(list(app.exception), [])
            generate.assert_called_once()
            script = generate.call_args.args[0]
            for text in (draft["feeding"], draft["ors"], draft["return_if"], "1.25 invented units", "Blood count"):
                self.assertIn(text, script)
            self.assertEqual(generate.call_args.kwargs, {"live": True})
            self.assertIsNone(self.s.latest_reply(self.cid))


if __name__ == "__main__":
    unittest.main()
