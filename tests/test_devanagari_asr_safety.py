"""A03 safety regression: Devanagari ASR evidence must survive preprocessing.

Qwen3-ASR transcribes Urdu speech in Devanagari script (bake-off caveat #4).
The clinical transcript returned by ``QwenProvider.transcribe`` becomes
``TranscriptProvenance.raw_text`` -> ``accepted_text``, which the deterministic
triage (``rules_triage._haystack``) reads. A previous lossy
``devanagari_to_urdu()`` conversion produced broken mixed-script text that
dropped the urgent Devanagari evidence (सीने / दर्द / पसीना) and silently
downgraded a RED chest-pain emergency to ``NO_CONFIGURED_FLAG``.

These tests drive the EXACT audited fixture through the ACTUAL router
preprocessing path (``QwenProvider.transcribe``), mocking only the HTTP
transport boundary (``requests.post``) so the real preprocessing logic runs
with ZERO network calls, then feed the result into the deterministic
triage / early-safety path used by the application.

They are engineering checks of the software journey, not clinical validation.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from smartsehat import clinical_intake as intake
from smartsehat import rules_triage
from smartsehat.router import QwenProvider


# The exact audited fixture: chest pain + sweating (an ACS-feature emergency).
DEVA_RED = "मुझे आधे घंटे से सीने में बहुत दर्द है और पसीना आ रहा है"
CASE_ID = "CASE-DEVA-ASR-SAFETY"
ACTOR_ID = "PM-2213"


class _FakeResponse:
    """Minimal stand-in for the DashScope ``requests.post`` response."""

    def __init__(self, text: str):
        self._text = text

    def raise_for_status(self) -> None:  # pragma: no cover - no-op by design
        return None

    def json(self) -> dict:
        return {
            "output": {
                "choices": [
                    {"message": {"content": [{"text": self._text}]}}
                ]
            }
        }


class DevanagariAsrSafetyTests(unittest.TestCase):
    def setUp(self):
        # Offline test conventions (mirror test_phase3_intake / regional tests).
        self.network_guards = []
        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                       "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError(
                "External connections forbidden in Devanagari ASR safety test"))
            self.network_guards.append(guard.start())
            self.addCleanup(guard.stop)

        environment = patch.dict(os.environ, {
            "SMARTSEHAT_OFFLINE": "1", "SMARTSEHAT_MODE": "stub",
            "DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        }, clear=False)
        environment.start()
        self.addCleanup(environment.stop)

        # A real, on-disk audio file so transcribe()'s base64 read path runs.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audio_path = Path(self.temp.name) / "case_audio.wav"
        self.audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

        # Mock ONLY the HTTP transport boundary; the real preprocessing in
        # QwenProvider.transcribe still executes.
        post_patcher = patch("requests.post",
                             return_value=_FakeResponse(DEVA_RED))
        self.post = post_patcher.start()
        self.addCleanup(post_patcher.stop)

        # Provider without any real credentials/client; transcribe() only needs
        # a truthy api_key to pass its configuration guard.
        self.provider = QwenProvider()
        self.provider.api_key = "test-dummy-key"

    def tearDown(self):
        for guard in self.network_guards:
            guard.assert_not_called()

    def _run_router_preprocessing(self) -> str:
        """Drive the fixture through the ACTUAL router transcribe path."""
        return self.provider.transcribe(str(self.audio_path), language="ur")

    def _triage_from_transcript(self, transcript: str) -> dict:
        """Feed a transcript into the deterministic early-safety triage path.

        Mirrors the application: captured_history -> accept_history ->
        rules_triage.evaluate (the same callable early_safety_node uses via
        ``triage_evaluate``), reading ``history.accepted_text``.
        """
        history = intake.captured_history(
            CASE_ID, source="ASR", source_media_artifact_id="MEDIA-SYNTHETIC",
            raw_text=transcript, declared_language="ur", provider="qwen")
        accepted, error = intake.accept_history(history, transcript, ACTOR_ID)
        self.assertIsNone(error)
        return rules_triage.evaluate({"case_id": CASE_ID, "history": accepted})

    def test_original_devanagari_transcript_preserved_and_triage_red(self):
        transcript = self._run_router_preprocessing()

        # 1. The ORIGINAL ASR text is preserved verbatim (no lossy conversion).
        self.assertEqual(transcript, DEVA_RED)

        # 2. Preprocessing did not remove the urgent Devanagari evidence.
        for token in ("सीने", "दर्द", "पसीना"):
            self.assertIn(token, transcript)

        # 3/4. Deterministic triage is RED with the chest-pain ACS rule.
        result = self._triage_from_transcript(transcript)
        self.assertEqual(result["status"], "RED")
        self.assertEqual(result["level"], "RED")
        self.assertTrue(result["findings"])
        self.assertEqual(result["findings"][0]["rule_id"],
                         "chest_pain_with_ACS_features")

        # 5. The transport was mocked; no live network call occurred.
        self.post.assert_called_once()

    def test_conversion_failure_preserves_original_and_stays_red(self):
        """INVARIANT GUARD — NOT an independent revert-detector.

        Proves the safety invariant that a conversion/preprocessing FAILURE
        still preserves the ORIGINAL clinical transcript and keeps triage RED
        (it can never silently downgrade the emergency). Detecting an actual
        revert to the lossy conversion on the normal path is the sole job of
        ``test_original_devanagari_transcript_preserved_and_triage_red``; this
        test does not duplicate that revert detection.
        """
        # Even if a (display-only) transliteration were attempted and FAILED,
        # the clinical transcript must remain the original and stay RED — it
        # must never be downgraded. With the A03 fix transcribe() no longer
        # routes the transcript through devanagari_to_urdu at all, so a raising
        # conversion cannot touch the returned text.
        with patch("smartsehat.script_utils.devanagari_to_urdu",
                   side_effect=RuntimeError("conversion blew up")), \
             patch("smartsehat.script_utils.contains_devanagari",
                   return_value=True):
            transcript = self._run_router_preprocessing()

        self.assertEqual(transcript, DEVA_RED)
        result = self._triage_from_transcript(transcript)
        self.assertEqual(result["status"], "RED")
        self.assertEqual(result["findings"][0]["rule_id"],
                         "chest_pain_with_ACS_features")

    def test_no_live_network_during_preprocessing(self):
        # The socket guards (installed in setUp, asserted in tearDown) prove no
        # connection was attempted; here we confirm the transcript still flows
        # end-to-end through the mocked transport only.
        transcript = self._run_router_preprocessing()
        self.assertEqual(transcript, DEVA_RED)
        self.post.assert_called_once()
        for guard in self.network_guards:
            guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
