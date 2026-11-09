"""Synthetic software checks, not regional speech or clinical validation."""
from array import array
import io
import os
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from smartsehat_workspace.consultation import ai
from smartsehat_workspace.consultation.privacy import Cipher, PREFIX
from smartsehat_workspace.consultation.speech_evaluation import evaluate_dataset, edit_distance, evaluation_template
from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.voice import inspect_audio


def wav_bytes(samples=None):
    output = io.BytesIO()
    with wave.open(output, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(8000)
        clip.writeframes(array("h", samples if samples is not None else [3000, -3000] * 8000).tobytes())
    return output.getvalue()


def dataset():
    return {"format": "smartsehat-speech-evaluation-1", "samples": [{
        "sample_id": "synthetic-fixture", "language": "English", "dialect": "synthetic-text-only",
        "recording_sha256": "1" * 64, "source": "synthetic", "speaker_id": "fixture-01", "consent_recorded": False,
        "reference_reviewer": "Synthetic test", "reference_checked_against_audio": True, "reviewed_on": "2026-01-01",
        "reference_transcript": "No cough for 2 days", "hypothesis_transcript": "Cough for 3 days",
        "provider": "mock", "model": "fixture-only", "device": "no actual device", "noise": "not measured", "network": "not measured", "latency_ms": 25, "facts_annotated": True,
        "reference_facts": [{"key": "cough", "value": False}, {"key": "duration", "value": 2.0, "unit": "days"}, {"key": "medicine_name", "value": "fixture-a"}],
        "proposed_facts": [{"key": "cough", "value": True}, {"key": "duration", "value": 3.0, "unit": "weeks"}, {"key": "medicine_name", "value": "fixture-b"}, {"key": "fever", "value": True}]
    }]}


class VoiceCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = Store(self.root / "voice.db")
        self.cid = self.service.load_ayaan()
        for replacement in (patch("smartsehat_workspace.consultation.ai.ROOT", self.root),
                            patch("smartsehat_workspace.consultation.privacy.media_cipher", return_value=Cipher(str(self.root / "media")))):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.path = self.root / "synthetic.wav"
        self.path.write_bytes(wav_bytes())

    def story(self, text="Cough for 2 days", **kwargs):
        current = next((f for f in self.service.facts(self.cid) if f["key"] == "transcript"), None)
        return self.service.confirm_story(self.cid, text, "English", reviewed=True, expected_fact_id=current["id"] if current else None, **kwargs)

    def proposals(self, source):
        return self.service.propose_story_facts(self.cid, source["id"], [{"key": "cough", "value": True, "quote": "Cough for 2 days", "confidence": .7}], "synthetic mock", review_id=source["review_id"])

    def test_saraiki_balochi_use_explicit_multimodal_route_and_separate_cache(self):
        with patch("smartsehat_workspace.router.GeminiProvider") as gemini, patch("smartsehat_workspace.router.GroqProvider") as groq, patch("smartsehat_workspace.router.ModelRouter") as router:
            gemini.return_value.model = "fixture-model"
            gemini.return_value.transcribe.return_value = "Synthetic placeholder; not native speech"
            for language in ("skr", "bal"):
                text, mode = ai.transcribe(self.path, language, True)
                self.assertIn("Synthetic", text)
                self.assertIn("experimental " + language, mode)
                gemini.return_value.transcribe.assert_called_with(str(self.path), language=language)
            self.assertEqual(gemini.return_value.transcribe.call_count, 2)
            self.assertIn("cached gemini/fixture-model", ai.transcribe(self.path, "bal", True)[1])
            self.assertEqual(gemini.return_value.transcribe.call_count, 2)
            groq.assert_not_called()
            router.assert_not_called()
        self.path.write_bytes(wav_bytes([4000, -4000] * 8000))
        with patch("smartsehat_workspace.router.GeminiProvider") as gemini:
            gemini.return_value.transcribe.return_value = "[unintelligible]"
            self.assertIsNone(ai.transcribe(self.path, "bal", True)[0])

    def test_gemini_prompt_and_audio_mime_match_selected_language_and_format(self):
        from smartsehat_workspace.router import GeminiProvider
        provider = object.__new__(GeminiProvider)
        provider.model, provider._client = "fixture-model", Mock()
        provider._client.models.generate_content.return_value.text = "Synthetic"
        path = self.root / "fixture.m4a"
        path.write_bytes(b"mock-audio-only")
        self.assertEqual(provider.transcribe(str(path), "bal"), "Synthetic")
        contents = provider._client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("Balochi", contents[1])
        self.assertIn("Do not translate", contents[1])
        self.assertEqual(contents[0].inline_data.mime_type, "audio/mp4")
        with self.assertRaises(ValueError):
            provider.transcribe(str(path), "not-supported")

    def test_recording_measurements_reject_silence_without_provider_calls(self):
        quality = inspect_audio(wav_bytes())
        self.assertEqual(quality["duration_seconds"], 2)
        self.assertEqual(quality["sample_rate"], 8000)
        self.assertFalse(quality["silent"])
        self.assertAlmostEqual(quality["rms_dbfs"], -20.8, places=1)
        self.path.write_bytes(wav_bytes([0] * 8000))
        with patch("smartsehat_workspace.router.ModelRouter") as router:
            self.assertIsNone(ai.transcribe(self.path, "ur")[0])
            router.return_value.route_transcribe.assert_not_called()
        self.assertTrue(inspect_audio(b"unknown compressed fixture")["warnings"])

    def test_review_is_enforced_on_server_and_late_extractions_are_rejected(self):
        with self.assertRaises(ValueError):
            self.service.confirm_story(self.cid, "Synthetic", "English", reviewed=False)
        with self.assertRaises(ValueError):
            self.service.confirm_story(self.cid, "Synthetic", "Saraiki", reviewed=True)
        source = self.story()
        self.story("No cough")
        with self.assertRaisesRegex(ValueError, "changed while"):
            self.proposals(source)
        with self.assertRaisesRegex(ValueError, "another session"):
            self.service.confirm_story(self.cid, "Cough", "English", reviewed=True, expected_fact_id=source["id"])

    def test_new_review_or_answer_during_extraction_rejects_late_proposals(self):
        source = self.story()
        self.story()
        with self.assertRaisesRegex(ValueError, "newer transcript review"):
            self.proposals(source)
        source = self.story()
        self.service.add_fact(self.cid, "cough", False)
        with self.assertRaisesRegex(ValueError, "answer changed"):
            self.proposals(source)
        self.assertFalse(self.service.pending(self.cid))

    def test_recordings_and_manual_edits_cannot_be_overwritten_by_late_transcripts(self):
        from smartsehat_workspace.consultation.privacy import save_media, read_media
        encrypted = str(save_media(self.path, self.path.read_bytes()))
        recording = self.service.attach_story_recording(self.cid, encrypted, "English", automatic=True)
        proposal = self.service.save_transcription_draft(self.cid, "English", recording, "Cough for 2 days", "mock")
        self.assertFalse(any(f["key"] == "transcript" for f in self.service.facts(self.cid)))
        with self.assertRaisesRegex(ValueError, "recording changed since"):
            self.service.confirm_story(self.cid, proposal["text"], "English", reviewed=True, proposal_id=proposal["id"])
        source = self.service.confirm_story(self.cid, proposal["text"], "English", reviewed=True, proposal_id=proposal["id"], expected_recording=recording)
        proposed = self.proposals(source)[0]
        newer = self.service.attach_story_recording(self.cid, encrypted, "English", automatic=True)
        self.assertNotEqual(newer["generation"], recording["generation"])
        self.assertNotIn("English", self.service.draft(self.cid, "story")["transcription_proposals"])
        with self.assertRaisesRegex(ValueError, "source recording changed"):
            self.service.confirm(self.cid, proposed["id"])
        with self.assertRaisesRegex(ValueError, "newer recording"):
            self.service.save_transcription_draft(self.cid, "English", recording, "Late result", "mock")
        current = self.service.draft(self.cid, "story")
        self.service.save_draft(self.cid, "story", {**current, "transcript": "Worker correction"})
        with self.assertRaisesRegex(ValueError, "edited while"):
            self.service.save_transcription_draft(self.cid, "English", newer, "Late result", "mock")
        restarted = Store(self.service.path)
        self.assertEqual(restarted.draft(self.cid, "story")["transcript"], "Worker correction")
        self.assertEqual(read_media(restarted.draft(self.cid, "story")["recordings"]["English"]["path"]), wav_bytes())

    def test_pending_and_confirmed_facts_keep_exact_evidence_and_corrections_reopen_them(self):
        source = self.story()
        proposed = self.proposals(source)[0]
        self.assertEqual(proposed["evidence"]["transcript_fact_id"], source["id"])
        self.service.confirm(self.cid, proposed["id"])
        confirmed = next(f for f in self.service.facts(self.cid) if f["key"] == "cough")
        self.assertEqual(confirmed["evidence"], proposed["evidence"])
        self.story("No cough")
        current = next(f for f in self.service.facts(self.cid) if f["key"] == "cough")
        self.assertIsNone(current["value"])
        self.assertIn("recheck", current["reason_missing"])
        self.assertEqual(current["supersedes"], confirmed["id"])
        self.assertTrue(any(e["type"] == "STORY_FACTS_REOPENED" for e in self.service.events(self.cid)))

    def test_edited_draft_and_new_manual_answer_block_old_confirmation(self):
        source = self.story()
        proposed = self.proposals(source)[0]
        self.service.save_draft(self.cid, "story", {"transcript_language": "English", "transcript": "No cough"})
        with self.assertRaisesRegex(ValueError, "editable transcript"):
            self.service.confirm(self.cid, proposed["id"])
        self.service.save_draft(self.cid, "story", {"transcript_language": "English", "transcript": "Cough for 2 days"})
        self.service.add_fact(self.cid, "cough", False)
        with self.assertRaisesRegex(ValueError, "answer changed"):
            self.service.confirm(self.cid, proposed["id"])
        self.story("No cough")
        self.assertFalse(self.service.pending(self.cid))

    def test_story_ui_requires_exact_review_and_disables_confirmation_after_edit(self):
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ, {"CONSULTATION_DB": self.service.path, "CONSULTATION_DISABLE_SCHEDULER": "1"}), patch("smartsehat_workspace.consultation.ai.extract", return_value=([{"key": "cough", "value": True, "quote": "Cough for 2 days", "confidence": .7}], "synthetic mock")):
            app = AppTest.from_string(f"from smartsehat_workspace.consultation.ui import _story\n_story({self.cid!r})", default_timeout=45).run()
            app.selectbox(key=f"voice_language_{self.cid}").set_value("English").run()
            app.text_area(key=f"transcript_{self.cid}").set_value("Cough for 2 days").run()
            self.assertTrue(app.button(key=f"extract_{self.cid}").disabled)
            next(c for c in app.checkbox if c.label.startswith("I checked the entire transcript")).check().run()
            app.button(key=f"extract_{self.cid}").click().run()
            proposal = self.service.pending(self.cid)[0]
            self.assertFalse(app.button(key=f"confirm_{proposal['id']}").disabled)
            app.text_area(key=f"transcript_{self.cid}").set_value("No cough").run()
            self.assertTrue(app.button(key=f"confirm_{proposal['id']}").disabled)
            self.assertTrue(app.button(key=f"extract_{self.cid}").disabled)
            self.assertEqual(list(app.exception), [])

    def test_retries_do_not_duplicate_proposals_and_quotes_numbers_and_negation_are_guarded(self):
        source = self.story()
        self.proposals(source)
        self.proposals(source)
        self.assertEqual(len(self.service.pending(self.cid)), 1)
        proposals = [
            {"key": "cough", "value": True, "quote": "No cough", "confidence": .99},
            {"key": "fever_days", "value": 3.0, "quote": "Fever for 2 weeks", "confidence": .99},
            {"key": "fever_days", "value": 14.0, "quote": "Fever for 2 weeks", "confidence": .7},
            {"key": "vomiting", "value": True, "quote": "not in the transcript", "confidence": .9},
            {"key": "wheeze", "value": True, "quote": "[unclear] wheeze", "confidence": .99}]
        accepted = ai.filter_story_proposals("No cough. Fever for 2 weeks. [unclear] wheeze", proposals)
        self.assertEqual([(p["key"], p["value"]) for p in accepted], [("fever_days", 14.0)])
        self.assertEqual(ai.filter_story_proposals("کھانسی نہیں", [{"key": "cough", "value": True, "quote": "کھانسی نہیں", "confidence": .9}]), [])

    def test_scores_distinguish_negatives_numbers_medicines_and_spurious_facts(self):
        result = evaluate_dataset(dataset())
        score = result["samples"][0]
        self.assertEqual(score["word_errors"], 2)
        self.assertEqual(score["wer"], .4)
        self.assertEqual(score["fact_labels"], {"matched": 0, "wrong_value_or_unit": 3, "omitted": 0, "spurious": 1,
            "negation_errors": 1, "number_or_unit_errors": 1, "medicine_label_errors": 1})
        self.assertFalse(result["clinical_validation_established"])
        with self.assertRaises(ValueError):
            evaluate_dataset(evaluation_template()["dataset_template"])
        data = dataset()
        data["samples"][0]["source"] = "consented-speaker"
        with self.assertRaises(ValueError):
            evaluate_dataset(data)
        data = dataset()
        data["samples"][0]["reference_facts"] = [{"key": "cough", "value": False}]
        data["samples"][0]["proposed_facts"] = [{"key": "cough", "value": 0.0}]
        self.assertEqual(evaluate_dataset(data)["samples"][0]["fact_labels"]["wrong_value_or_unit"], 1)

    def test_bit_vector_distance_matches_independent_small_dynamic_program(self):
        def baseline(a, b):
            row = list(range(len(b) + 1))
            for i, left in enumerate(a, 1):
                next_row = [i]
                for j, right in enumerate(b, 1):
                    next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (left != right)))
                row = next_row
            return row[-1]
        rng = random.Random(42)
        for _ in range(150):
            a = "".join(rng.choices("abcکھ", k=rng.randrange(30)))
            b = "".join(rng.choices("abcکھ", k=rng.randrange(30)))
            self.assertEqual(edit_distance(a, b), baseline(a, b))

    def test_evaluations_are_encrypted_append_only_idempotent_and_do_not_validate_a_language(self):
        self.service.report_speech_evaluation(dataset())
        self.service.report_speech_evaluation(dataset())
        restarted = Store(self.service.path)
        self.assertEqual(len(restarted.speech_evaluations()), 1)
        english = next(r for r in restarted.speech_readiness() if r["language"] == "English")
        self.assertEqual(english["synthetic_recordings"], 1)
        self.assertEqual(english["reported_speaker_recordings"], 0)
        self.assertEqual(english["independent_language_validation"], "pending")
        with restarted.connect() as db:
            self.assertTrue(db.execute("SELECT data FROM consultation_speech_evaluations").fetchone()[0].startswith(PREFIX))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM consultation_speech_evaluations")


if __name__ == "__main__":
    unittest.main()
