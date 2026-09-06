"""A48/A51: explicit offline mode and direct router fail-closed behavior."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from smartsehat.router import ModelRouter, load_config


class _Provider:
    name = "fake"

    def generate(self, *args, **kwargs):
        raise AssertionError("provider must not run offline")


class OfflineFoundationTests(unittest.TestCase):
    def test_repository_ignore_file_is_utf8_and_covers_runtime_artifacts(self):
        path = Path(__file__).resolve().parents[1] / ".gitignore"
        raw = path.read_bytes()
        self.assertNotIn(b"\x00", raw)
        text = raw.decode("utf-8")
        for pattern in ("assets/uploaded/", "runtime/", "*.db", "*.db-wal", "*.db-shm", "*.db-journal"):
            self.assertIn(pattern, text.splitlines())

    def test_offline_environment_overrides_real_project_configuration(self):
        with patch.dict(os.environ, {"SMARTSEHAT_OFFLINE": "1", "SMARTSEHAT_MODE": "real"}):
            self.assertEqual(load_config()["agents"], "stub")

    def test_explicit_stub_mode_overrides_project_configuration(self):
        with patch.dict(os.environ, {"SMARTSEHAT_MODE": "stub"}, clear=False):
            with patch.dict(os.environ, {"SMARTSEHAT_OFFLINE": "0"}, clear=False):
                self.assertEqual(load_config()["agents"], "stub")

    def test_invalid_mode_fails_loudly(self):
        with patch.dict(os.environ, {"SMARTSEHAT_MODE": "automatic",
                                     "SMARTSEHAT_OFFLINE": "0"}, clear=False):
            with self.assertRaisesRegex(ValueError, "stub.*real"):
                load_config()

    def test_router_refuses_direct_external_route_in_stub_mode(self):
        router = object.__new__(ModelRouter)
        router.config = {"agents": "stub", "role_routes": {"reasoning": ["fake"]}}
        router.providers = {"fake": _Provider()}
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            router.route_generate("reasoning", [])

    def test_provider_health_does_not_probe_in_stub_mode(self):
        router = object.__new__(ModelRouter)
        router.config = {"agents": "stub"}
        provider = unittest.mock.Mock()
        router.providers = {"fake": provider}
        self.assertEqual(router.provider_health(), {"fake": False})
        provider.healthy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
