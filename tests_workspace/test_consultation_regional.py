"""Speech routing/confirmation checks; no language-accuracy claim or live calls."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from smartsehat_workspace.consultation import ai
from smartsehat_workspace.consultation.privacy import Cipher
from smartsehat_workspace.consultation.store import Store


class RegionalSpeechTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "fictional.wav"
        self.path.write_bytes(b"RIFF-fake-audio-used-only-with-mocked-provider")
        for replacement in (patch("smartsehat_workspace.consultation.ai.ROOT", self.root),
                            patch("smartsehat_workspace.consultation.store.ROOT", self.root),
                            patch("smartsehat_workspace.consultation.privacy.media_cipher", return_value=Cipher(str(self.root / "media")))):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_regional_routes_require_explicit_opt_in_and_do_not_guess_unsupported_languages(self):
        with patch("smartsehat_workspace.router.GroqProvider") as provider, patch("smartsehat_workspace.router.ModelRouter") as router:
            for code, experimental in (("sd", False), ("pa", False), ("ps", False), ("skr", False), ("bal", False), ("unsupported", True)):
                text, mode = ai.transcribe(self.root / "missing.wav", code, experimental)
                self.assertIsNone(text)
                self.assertIn("manual/interpreter", mode)
            provider.assert_not_called()
            router.assert_not_called()

    def test_regional_draft_uses_only_explicit_language_provider_and_separate_cache(self):
        with patch("smartsehat_workspace.router.GroqProvider") as provider, patch("smartsehat_workspace.router.ModelRouter") as router:
            provider.return_value.transcribe.return_value = "Synthetic script fixture; not real language evaluation"
            text, mode = ai.transcribe(self.path, "sd", True)
            self.assertIn("Synthetic", text)
            self.assertIn("experimental sd", mode)
            provider.return_value.transcribe.assert_called_once_with(str(self.path), language="sd")
            router.assert_not_called()
            ai.transcribe(self.path, "sd", True)
            self.assertEqual(provider.return_value.transcribe.call_count, 1)
            ai.transcribe(self.path, "pa", True)
            self.assertEqual(provider.return_value.transcribe.call_count, 2)
            provider.return_value.transcribe.assert_called_with(str(self.path), language="pa")
        with patch("smartsehat_workspace.router.GroqProvider") as provider:
            provider.return_value.transcribe.side_effect = RuntimeError("synthetic provider unavailable")
            text, _ = ai.transcribe(self.path, "ps", True)
            self.assertIsNone(text)
            provider.return_value.transcribe.assert_called_once()

    def test_urdu_and_english_keep_the_normal_router_and_empty_provider_results_are_rejected(self):
        with patch("smartsehat_workspace.router.ModelRouter") as router, patch("smartsehat_workspace.router.GroqProvider") as regional:
            router.return_value.route_transcribe.return_value = ("Synthetic English", "mock-router")
            text, _ = ai.transcribe(self.path, "en")
            self.assertEqual(text, "Synthetic English")
            router.return_value.route_transcribe.assert_called_once_with("stt", str(self.path), language="en")
            regional.assert_not_called()
            router.return_value.route_transcribe.return_value = (None, "mock-router")
            self.assertIsNone(ai.transcribe(self.path, "ur")[0])

    def test_groq_receives_requested_language_in_the_actual_api_arguments(self):
        from smartsehat_workspace.router import GroqProvider
        provider = object.__new__(GroqProvider)
        provider._client = Mock()
        provider.stt_model = "whisper-large-v3"
        provider._client.audio.transcriptions.create.return_value.text = "Synthetic"
        self.assertEqual(provider.transcribe(str(self.path), "ps"), "Synthetic")
        arguments = provider._client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(arguments["language"], "ps")
        self.assertEqual(arguments["model"], "whisper-large-v3")

    def app(self, cid):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_string(f"from smartsehat_workspace.consultation.ui import _story\n_story({cid!r})\n", default_timeout=45).run()

    def test_regional_ui_requires_named_review_and_drafts_survive_without_confirming_facts(self):
        service = Store(self.root / "regional.db")
        cid = service.load_ayaan()
        before = service.facts(cid)
        with patch.dict(os.environ, {"CONSULTATION_DB": service.path, "CONSULTATION_DISABLE_SCHEDULER": "1"}), patch("smartsehat_workspace.consultation.ai.extract", return_value=([{"key": "cough", "value": True, "confidence": .5, "quote": "Synthetic cough"}], "mocked")):
            app = self.app(cid)
            app.text_area(key=f"transcript_{cid}").set_value("Original Urdu fixture").run()
            app.selectbox(key=f"voice_language_{cid}").set_value("Punjabi").run()
            self.assertEqual(app.text_area(key=f"transcript_{cid}").value, "")
            self.assertFalse(app.toggle(key=f"experimental_stt_{cid}_Punjabi").value)
            app.text_area(key=f"transcript_{cid}").set_value("Synthetic cough; binding fixture only").run()
            self.assertTrue(app.button(key=f"extract_{cid}").disabled)
            app.text_input(key=f"story_interpreter_{cid}_Punjabi").set_value("Synthetic reviewer").run()
            self.assertTrue(app.button(key=f"extract_{cid}").disabled)
            self.assertEqual(service.facts(cid), before)
            app = self.app(cid)
            self.assertEqual(app.text_input(key=f"story_interpreter_{cid}_Punjabi").value, "Synthetic reviewer")
            self.assertIn("binding fixture", app.text_area(key=f"transcript_{cid}").value)
            next(c for c in app.checkbox if c.label.startswith("I checked the entire transcript")).check().run()
            app.button(key=f"extract_{cid}").click().run()
            values = {f["key"]: f["value"] for f in service.facts(cid)}
            self.assertEqual(values["transcript_language"], "Punjabi")
            self.assertEqual(values["transcript_interpreter"]["name"], "Synthetic reviewer")
            self.assertTrue(any(not f["confirmed"] and f["key"] == "cough" for f in service.pending(cid)))
            self.assertEqual(list(app.exception), [])

    def test_transcription_proposal_is_not_a_confirmed_fact_and_edited_text_requires_new_review(self):
        service = Store(self.root / "proposal.db")
        cid = service.load_ayaan()
        before = service.facts(cid)
        proposal = {"id": "synthetic-proposal", "text": "Unconfirmed synthetic transcription", "language": "Sindhi", "path": str(self.path), "provider": "mocked experimental sd", "experimental": True}
        service.save_draft(cid, "story", {"voice_language": "Sindhi", "transcription_proposals": {"Sindhi": proposal}, "interpreters": {"Sindhi": "Synthetic reviewer"}})
        with patch.dict(os.environ, {"CONSULTATION_DB": service.path, "CONSULTATION_DISABLE_SCHEDULER": "1"}):
            app = self.app(cid)
            self.assertEqual(app.text_area(key=f"transcript_{cid}").value, proposal["text"])
            self.assertEqual(service.facts(cid), before)
            self.assertTrue(app.button(key=f"extract_{cid}").disabled)
            next(c for c in app.checkbox if c.label.startswith("I checked the entire transcript")).check().run()
            self.assertFalse(app.button(key=f"extract_{cid}").disabled)
            app.text_area(key=f"transcript_{cid}").set_value("Edited synthetic transcription").run()
            self.assertTrue(app.button(key=f"extract_{cid}").disabled)
            app.selectbox(key=f"voice_language_{cid}").set_value("Saraiki").run()
            self.assertFalse(app.toggle(key=f"experimental_stt_{cid}_Saraiki").value)
            self.assertEqual(app.text_area(key=f"transcript_{cid}").value, "")
            self.assertEqual(list(app.exception), [])


if __name__ == "__main__":
    unittest.main()
