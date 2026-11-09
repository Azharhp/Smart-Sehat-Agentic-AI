"""Credential-free checks for workspace provider boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from smartsehat_workspace.router import ModelRouter, QwenProvider

class RouterBoundaryTests(TestCase):
    def test_offline_guard_precedes_provider_resolution(self):
        router = ModelRouter.__new__(ModelRouter)
        with patch.dict("os.environ", {"SMARTSEHAT_OFFLINE": "1"}):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                router._route("vision_rx", "vision", "unused.png")

    def test_qwen_keeps_original_asr_evidence(self):
        text = "सीने में दर्द और पसीना"
        response = SimpleNamespace(raise_for_status=lambda: None,
            json=lambda: {"output": {"choices": [{"message": {"content": [{"text": text}]}}]}})
        provider = QwenProvider.__new__(QwenProvider)
        provider.api_key = "fictional-test-key"
        provider.asr_model = "fixture"
        provider.asr_endpoint = "https://example.invalid/asr"
        with TemporaryDirectory() as folder:
            audio = Path(folder) / "fixture.wav"
            audio.write_bytes(b"synthetic")
            with patch("requests.post", return_value=response):
                self.assertEqual(provider.transcribe(str(audio), "ur"), text)
