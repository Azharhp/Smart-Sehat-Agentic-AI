"""Worker UI guards for portable transfers and inactive outbound integrations."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.privacy import Cipher


class TransferUITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for replacement in (patch("smartsehat_workspace.consultation.store.ROOT", self.root),
                            patch("smartsehat_workspace.consultation.privacy.media_cipher", return_value=Cipher(str(self.root / "media")))):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.service = Store(self.root / "transfer-ui.db")
        self.cid = self.service.load_ayaan()
        self.packet = self.service.review(self.cid)
        environment = patch.dict(os.environ, {"CONSULTATION_DB": self.service.path, "CONSULTATION_DISABLE_SCHEDULER": "1",
            "CONSULTATION_DELIVERY_URL": "", "CONSULTATION_DELIVERY_TOKEN": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def app(self, body):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_string(body, default_timeout=35).run()

    def test_portable_download_is_hidden_after_case_correction(self):
        source = f"from smartsehat_workspace.consultation.ui import _transfer_panel,store\n_transfer_panel({self.cid!r},store().output({self.cid!r},'packet',True),'test')"
        app = self.app(source)
        app.button(key=f"transfer_prepare_{self.cid}_test").click().run()
        self.assertEqual(list(app.exception), [])
        self.assertTrue(any(event["type"] == "TRANSFER_PREPARED" for event in self.service.events(self.cid)))
        self.assertTrue(any(row.label == "Download portable case ZIP" for row in app.get("download_button")))
        self.service.add_fact(self.cid, "worker_request", "Corrected fictional request")
        app.run()
        self.assertTrue(app.button(key=f"transfer_prepare_{self.cid}_test").disabled)
        self.assertFalse(any(row.label == "Download portable case ZIP" for row in app.get("download_button")))
        self.assertEqual(list(app.exception), [])

    def test_prepared_outbox_cannot_send_when_gateway_is_inactive(self):
        self.service.acknowledge_medcheck(self.cid, self.service.medcheck(self.cid)["version"])
        self.service.readback(self.cid, True, True)
        self.service.send(self.cid)
        identity = self.service.queue_delivery(self.cid, "case_packet", "fictional-team", "care_team")
        with patch("httpx.Client") as network:
            app = self.app(f"from smartsehat_workspace.consultation.ui import _delivery_panel\n_delivery_panel({self.cid!r},'test')")
            next(row for row in app.checkbox if row.label.startswith("I checked this recipient")).check().run()
            self.assertTrue(app.button(key=f"dispatch_delivery_{self.cid}_test").disabled)
            network.assert_not_called()
            self.assertEqual(self.service.deliveries(self.cid)[0]["status"], "queued")
            self.assertEqual(self.service.deliveries(self.cid)[0]["delivery_id"], identity)
            self.assertEqual(list(app.exception), [])

    def test_quick_component_positive_records_once_and_enters_emergency(self):
        response = {"case_id": self.cid, "scope": self.service.quick_screen_snapshot(self.cid)["scope"],
                    "answers": {"cannot_drink": True}, "elapsed_seconds": 2.7}
        with patch("smartsehat_workspace.consultation.ui.quick_danger", return_value=response):
            app = self.app(f"from smartsehat_workspace.consultation.ui import _danger\n_danger({self.cid!r})")
            app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(self.service.case(self.cid)["stage"], "EMERGENCY")
        records = [e for e in self.service.events(self.cid) if e["type"] == "QUICK_DANGER_CAPTURE"]
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["data"]["independently_observed"])


if __name__ == "__main__":
    unittest.main()
