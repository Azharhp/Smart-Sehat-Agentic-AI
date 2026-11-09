"""Steps 1–7 workflow checks; fixtures do not represent clinical validation."""
import base64
from copy import deepcopy
import io
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock
import wave

from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.privacy import Cipher, save_media, read_media, MEDIA_PREFIX, PREFIX
from smartsehat_workspace.consultation.questions import collection_status
from smartsehat_workspace.consultation.reported_doses import record_hash
from smartsehat_workspace.consultation.speech_audio import synthesize, recorded_prompt, save_prompt_recording, offline_readback
from smartsehat_workspace.consultation.content_review import review_pack
from smartsehat_workspace.consultation.analysis_verifier import verify_analysis


def wav_fixture():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\x00\x00" * 2400)
    return buffer.getvalue()


class StepWorkflowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for replacement in (patch("smartsehat_workspace.consultation.store.ROOT", self.root),
                            patch("smartsehat_workspace.consultation.speech_audio.ROOT", self.root),
                            patch("smartsehat_workspace.consultation.privacy.media_cipher", return_value=Cipher(str(self.root / "media")))):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.s = Store(self.root / "steps.db")
        self.cid = self.s.load_ayaan()

    def packet(self):
        packet = self.s.review(self.cid)
        self.s.acknowledge_medcheck(self.cid, self.s.medcheck(self.cid)["version"])
        return packet

    def audio_path(self):
        path = self.root / "assets/uploaded/consultation" / self.cid / "readback.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        return save_media(path, wav_fixture())

    def test_generated_audio_exists_encrypted_and_cache_avoids_another_call(self):
        response = Mock(status_code=200)
        response.json.return_value = {"candidates": [{"content": {"parts": [{"inlineData": {"data": base64.b64encode(wav_fixture()).decode()}}]}}]}
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "synthetic-test-key"}), patch("httpx.post", return_value=response) as provider:
            path, mode = synthesize("Synthetic question", "Urdu", True)
            self.assertIsNotNone(path, mode)
            self.assertTrue(path.is_file())
            self.assertTrue(path.read_bytes().startswith(MEDIA_PREFIX))
            self.assertEqual(read_media(path), wav_fixture())
            cached, _ = synthesize("Synthetic question", "Urdu")
            self.assertEqual(cached, path)
            self.assertEqual(provider.call_count, 1)
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "synthetic-test-key"}), patch("httpx.post", return_value=Mock(status_code=429)):
            path, mode = synthesize("Another synthetic question", "Urdu", True)
            self.assertIsNone(path)
            self.assertIn("HTTP 429", mode)
            self.assertNotIn("synthetic-test-key", mode)

    def test_operator_prompt_is_bound_to_wording_language_and_file_type(self):
        with self.assertRaises(ValueError):
            save_prompt_recording("Question", "Urdu", b"not audio", ".wav", True)
        path = save_prompt_recording("Question", "Urdu", wav_fixture(), ".wav", True)
        self.assertEqual(recorded_prompt("Question", "Urdu"), path.resolve())
        self.assertIsNone(recorded_prompt("Changed question", "Urdu"))
        self.assertIsNone(recorded_prompt("Question", "English"))

    def test_readback_recording_is_case_packet_language_and_confirmation_bound(self):
        packet = self.packet()
        path = self.audio_path()
        with self.assertRaises(ValueError):
            self.s.attach_readback_audio(self.cid, packet["version"], "Urdu", str(path))
        self.s.attach_readback_audio(self.cid, packet["version"], "Urdu", str(path), True)
        saved = Store(self.s.path).readback_audio(self.cid, packet, "Urdu")
        self.assertEqual(saved["path"], str(path))
        html = offline_readback("<fictional>", packet, "Urdu", read_media(path))
        self.assertIn("data:audio/wav;base64,", html)
        self.assertIn("&lt;fictional&gt;", html)
        newer = self.s.review(self.cid)
        self.assertIsNone(self.s.readback_audio(self.cid, newer, "Urdu"))
        with self.assertRaises(ValueError):
            self.s.attach_readback_audio(self.cid, packet["version"], "Urdu", str(path), True)

    def test_interpreter_translation_cannot_drop_numbers_and_changes_require_reconfirmation(self):
        packet = self.packet()
        source = packet["readbacks"]["English"]["text"]
        with self.assertRaises(ValueError):
            self.s.readback(self.cid, True, True, "Punjabi")
        altered = re.sub(r"\d", lambda m: "8" if m[0] != "8" else "7", source, count=1)
        with self.assertRaisesRegex(ValueError, "numbers"):
            self.s.confirm_readback_translation(self.cid, packet["version"], "Punjabi", altered, "Synthetic interpreter", True)
        # Deliberately English fixture: validates binding, not Punjabi accuracy.
        translated = source + " Synthetic interpreter fixture."
        self.s.confirm_readback_translation(self.cid, packet["version"], "Punjabi", translated, "Synthetic interpreter", True)
        view = self.s.readback_view(self.cid, packet, "Punjabi")
        self.assertEqual(view["readbacks"]["Punjabi"]["text"], translated)
        self.s.attach_readback_audio(self.cid, packet["version"], "Punjabi", str(self.audio_path()), True)
        self.s.readback(self.cid, True, True, "Punjabi")
        self.s.send(self.cid)
        self.s.confirm_readback_translation(self.cid, packet["version"], "Punjabi", source + " Changed synthetic fixture.", "Synthetic interpreter", True)
        with self.assertRaisesRegex(ValueError, "wording changed"):
            self.s.send(self.cid)

    def test_per_medicine_reported_amounts_are_bound_and_do_not_activate_pending_dose_data(self):
        records = [{"name": "Panadol", "strength": "FICTIONAL A", "formulation": "tablet"},
                   {"name": "ibuprofen", "strength": "FICTIONAL B", "formulation": "tablet"}]
        self.s.medicines(self.cid, records, "Synthetic medicine history", False, "none")
        current = next(f["value"] for f in self.s.facts(self.cid) if f["key"] == "medicine_records")
        with self.assertRaises(ValueError):
            self.s.reported_medicine_amount(self.cid, 0, record_hash(current[0]), None, "oral", {})
        for i, row in enumerate(current):
            self.s.reported_medicine_amount(self.cid, i, record_hash(row), 1370., "oral", {"doses_per_day": 2., "duration_days": 5.})
        checks = self.s.medcheck(self.cid)["reported_dose_checks"]
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(item["status"] == "UNAVAILABLE" for item in checks))
        before = len(self.s.facts(self.cid, False))
        self.s.reported_medicine_amount(self.cid, 0, record_hash(current[0]), 1370., "oral", {"doses_per_day": 2., "duration_days": 5.})
        self.assertEqual(len(self.s.facts(self.cid, False)), before)
        self.s.medicines(self.cid, [{**records[0], "strength": "CORRECTED FICTIONAL"}, records[1]], "Synthetic", False, "none")
        remaining = next(f["value"] for f in self.s.facts(self.cid) if f["key"] == "reported_doses")
        self.assertEqual(len(remaining), 1)
        with self.assertRaises(ValueError):
            self.s.reported_medicine_amount(self.cid, 0, record_hash(current[0]), 1370., "oral", {"doses_per_day": 2., "duration_days": 5.})

    def test_review_reports_are_hashed_encrypted_immutable_and_reported_only(self):
        target = next(item for item in review_pack()["targets"] if item["scope"] == "questions")
        report = {"scope": "questions", "content_hash": target["content_hash"], "reviewer": "Synthetic report fixture",
                  "role": "native-speaker", "reviewed_on": "2026-01-01", "decision": "changes requested", "notes": "Automated fixture, not a real review"}
        self.s.report_content_review(report)
        self.s.report_content_review(report)
        self.assertEqual(len(Store(self.s.path).content_reviews()), 1)
        self.assertFalse(self.s.content_reviews()[0]["reviewer_identity_verified"])
        with self.assertRaises(ValueError):
            self.s.report_content_review({**report, "content_hash": "0" * 64})
        with self.s.connect() as db:
            self.assertTrue(db.execute("SELECT data FROM consultation_content_reviews").fetchone()[0].startswith(PREFIX))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM consultation_content_reviews")

    def test_known_negative_unknown_and_not_asked_have_separate_counts(self):
        values = [{"key": "cough", "value": False, "confirmed": True}, {"key": "fever", "value": None, "confirmed": True, "reason_missing": "Patient unsure"}]
        status = collection_status(values, self.s.age(self.cid))
        self.assertEqual(status["known"], 1)
        self.assertEqual(status["unknown"], 1)
        self.assertGreater(status["not asked"], 0)

    def test_consideration_checker_rejects_false_negatives_missing_sources_and_foreign_wording(self):
        self.s.review(self.cid)
        analysis = self.s.output(self.cid, "considerations")["analysis"]
        facts = self.s.facts(self.cid)
        self.assertEqual(verify_analysis(analysis, facts)["status"], "VERIFIED_STRUCTURE")
        self.assertTrue(analysis["considerations"])
        for field, value in (("title", "Unapproved clinical assertion"), ("reference_ids", ["MISSING-SOURCE"]), ("missing", ["cough"])):
            altered = deepcopy(analysis)
            altered["considerations"][0][field] = value
            self.assertEqual(verify_analysis(altered, facts)["status"], "BLOCKED")

    def test_danger_buttons_record_immediately_and_do_not_revert_on_rerun(self):
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ, {"CONSULTATION_DB": self.s.path, "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            app = AppTest.from_string(f"from smartsehat_workspace.consultation.ui import _danger\n_danger({self.cid!r})\n", default_timeout=35).run()
            app.button(key=f"danger_fast_{self.cid}_cannot_drink_Yes").click().run()
            self.assertEqual(self.s.case(self.cid)["stage"], "EMERGENCY")
            app.run()
            self.assertIs(next(f["value"] for f in self.s.facts(self.cid) if f["key"] == "cannot_drink"), True)
            self.assertEqual(list(app.exception), [])

    def test_reported_amount_ui_keeps_drafts_unconfirmed_until_explicit_save(self):
        from streamlit.testing.v1 import AppTest
        self.s.medicines(self.cid, [{"name": "Panadol", "strength": "FICTIONAL", "formulation": "tablet"}], "Synthetic", False, "none")
        row = next(f["value"][0] for f in self.s.facts(self.cid) if f["key"] == "medicine_records")
        identity = record_hash(row)
        before = self.s.facts(self.cid)
        with patch.dict(os.environ, {"CONSULTATION_DB": self.s.path, "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            source = f"from smartsehat_workspace.consultation.ui import _reported_amounts\n_reported_amounts({self.cid!r})\n"
            app = AppTest.from_string(source, default_timeout=35).run()
            app.number_input(key=f"reported_dose_mg_{self.cid}_{identity}").set_value(1370.).run()
            app.number_input(key=f"reported_doses_per_day_{self.cid}_{identity}").set_value(2).run()
            app.number_input(key=f"reported_duration_days_{self.cid}_{identity}").set_value(5.).run()
            self.assertEqual(self.s.facts(self.cid), before)
            app = AppTest.from_string(source, default_timeout=35).run()
            self.assertEqual(app.number_input(key=f"reported_dose_mg_{self.cid}_{identity}").value, 1370.)
            next(c for c in app.checkbox if c.label == "I confirmed these reported amounts or unknowns with the patient").check().run()
            app.button(key=f"save_reported_{self.cid}_{identity}").click().run()
            self.assertTrue(any(f["key"] == "reported_doses" for f in self.s.facts(self.cid)))
            self.assertEqual(list(app.exception), [])

    def test_interpreter_readback_ui_requires_confirmation_and_resets_patient_choice_on_edit(self):
        from streamlit.testing.v1 import AppTest
        packet = self.packet()
        with patch.dict(os.environ, {"CONSULTATION_DB": self.s.path, "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            source = f"from smartsehat_workspace.consultation.ui import _review\n_review({self.cid!r})\n"
            app = AppTest.from_string(source, default_timeout=35).run()
            app.selectbox(key=f"readback_language_{self.cid}_{packet['version']}").set_value("Punjabi").run()
            self.assertFalse(any(c.label == "Patient/caregiver confirms the read-back" for c in app.checkbox))
            text_key = f"translation_text_{self.cid}_{packet['version']}_Punjabi"
            app.text_area(key=text_key).set_value(packet["readbacks"]["English"]["text"] + " Synthetic fixture.").run()
            app.text_input(key=f"translation_name_{self.cid}_{packet['version']}_Punjabi").set_value("Synthetic interpreter").run()
            label = "Interpreter checked the complete meaning, negations, numbers, units and medicine names"
            next(c for c in app.checkbox if c.label == label).check().run()
            app.button(key=f"translation_save_{self.cid}_{packet['version']}_Punjabi").click().run()
            next(c for c in app.checkbox if c.label == "Patient/caregiver confirms the read-back").check().run()
            app.text_area(key=text_key).set_value(packet["readbacks"]["English"]["text"] + " Changed fixture.").run()
            next(c for c in app.checkbox if c.label == label).check().run()
            app.button(key=f"translation_save_{self.cid}_{packet['version']}_Punjabi").click().run()
            self.assertFalse(next(c for c in app.checkbox if c.label == "Patient/caregiver confirms the read-back").value)
            self.assertEqual(list(app.exception), [])


if __name__ == "__main__":
    unittest.main()
