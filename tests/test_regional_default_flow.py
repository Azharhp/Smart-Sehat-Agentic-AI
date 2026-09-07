"""Regional demo: the DEFAULT/primary Consultation start uses structured intake.

Offline, socket-guarded Streamlit ``AppTest``. Proves Task 2 / Task 8#2: the
guided structured-intake button is the prominent ``type="primary"`` action and
routes through the structured intake path (``intake_required=True``), while the
legacy unstructured workflow is preserved but demoted (not primary, now nested in
a collapsed expander).

This is an engineering check of the software journey only, not clinical
validation. It intentionally does NOT duplicate ``test_regional_consultation.py``
coverage (RED-before-model-work, unsupported scope, blank allergies UNASSESSED,
critical-transcript confirmation are tested there).
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import seed

ROOT = Path(__file__).resolve().parents[1]


class RegionalDefaultFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "audit.db"
        self.ckpt = str(Path(self.temp.name) / "checkpoints.db")
        environment = patch.dict("os.environ", {
            "SMARTSEHAT_OFFLINE": "1", "SMARTSEHAT_MODE": "stub",
            "SMARTSEHAT_AUTH_MODE": "demo",
            "SMARTSEHAT_MEDIA_ROOT": str(Path(self.temp.name) / "media"),
            "DOTENV_DISABLED": "1"}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        patcher = patch("smartsehat.audit.DB_PATH", str(self.db))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Hard socket guard: any real network access fails the test loudly.
        self.network = patch(
            "socket.socket.connect",
            side_effect=AssertionError("Network forbidden in regional default-flow test"))
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)
        self.create_network = patch(
            "socket.create_connection",
            side_effect=AssertionError("Network forbidden in regional default-flow test"))
        self.create_network_mock = self.create_network.start()
        self.addCleanup(self.create_network.stop)
        # Isolate LangGraph checkpoints into the temp dir (no shared state).
        from smartsehat import dawa_graph, graph
        for target, original in (("smartsehat.graph.start_case", graph.start_case),
                                 ("smartsehat.graph.resume_case", graph.resume_case),
                                 ("smartsehat.dawa_graph.start_dawa_case",
                                  dawa_graph.start_dawa_case),
                                 ("smartsehat.dawa_graph.resume_dawa_case",
                                  dawa_graph.resume_dawa_case)):
            def isolated(*args, _original=original, **kwargs):
                kwargs["checkpoint_db"] = self.ckpt
                return _original(*args, **kwargs)
            checkpoint_patcher = patch(target, isolated)
            checkpoint_patcher.start()
            self.addCleanup(checkpoint_patcher.stop)
        seed.seed(str(self.db))
        self.app = AppTest.from_file(str(ROOT / "smartsehat" / "app.py"), default_timeout=20)

    def tearDown(self):
        self.network_mock.assert_not_called()
        self.create_network_mock.assert_not_called()

    def button(self, label):
        return next(b for b in self.app.button if b.label == label)

    def assert_renders(self):
        self.assertEqual(list(self.app.exception), [])

    def test_default_primary_consultation_start_uses_structured_intake(self):
        self.app.run()
        self.button("Start demo session").click().run()
        self.assert_renders()
        self.button("Create consultation encounter for selected patient").click().run()
        self.assert_renders()

        # The guided structured-intake action is the prominent PRIMARY button;
        # the legacy unstructured workflow is preserved but demoted (not primary).
        guided = self.button("Start guided consultation (structured intake)")
        legacy = self.button("Start consultation workflow")
        self.assertEqual(guided.proto.type, "primary")
        self.assertNotEqual(legacy.proto.type, "primary")

        # Stage a typed complaint, then start via the PRIMARY (guided) action.
        if self.app.radio:
            source = next(r for r in self.app.radio if r.label == "Complaint source")
            source.set_value("Typed complaint").run()
        tarea = next(t for t in self.app.text_area
                     if t.label == "Patient complaint — type the original statement")
        tarea.set_value("patient has cough and fever").run()
        self.assert_renders()
        guided.click().run()
        self.assert_renders()

        # Confirm history -> the guided path routes to STRUCTURED intake
        # (intake_required=True), not the legacy unstructured "structure" step.
        self.button("Confirm transcript + answers").click().run()
        self.assert_renders()
        self.assertTrue(any(s.value == "Step 3 — Assessment" for s in self.app.subheader))
        self.assertTrue(any(c.label.startswith("I confirm this supported pathway")
                            for c in self.app.checkbox))
        # Pathway-confirmation truthfulness caption is surfaced (Task 5 wording).
        self.assertTrue(any("remains pending" in str(c.value) for c in self.app.caption))


if __name__ == "__main__":
    unittest.main()
