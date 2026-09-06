"""Private immutable media acceptance and concurrency tests."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from PIL import Image

import seed
from smartsehat import access, audit, graph
from smartsehat.auth import DemoAuthenticationProvider
from smartsehat.media import MediaValidationError, PrivateMediaStore


def image_bytes(color: tuple[int, int, int], image_format: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format=image_format)
    return output.getvalue()


def wav_bytes(sample: bytes = b"\x00\x01" * 20) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(sample)
    return output.getvalue()


class PrivateMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "audit.db")
        self.media_root = Path(self.temp.name) / "private-runtime-media"
        patcher = patch("smartsehat.audit.DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        seed.seed(self.db)
        conn = audit._connect()
        try:
            conn.execute("INSERT INTO facilities VALUES('BHU-B','Facility B','District')")
            conn.execute("INSERT INTO users VALUES('WORKER-B','Worker B','health_worker','BHU-B')")
            conn.execute("INSERT INTO user_facilities VALUES('WORKER-B','BHU-B')")
            conn.execute(
                "INSERT INTO patients(patient_id,name,age,sex,chronic_conditions_json,allergies_json,bhu_id) "
                "VALUES('PT-B','Patient B',30,'F',?,?,'BHU-B')", (json.dumps([]), json.dumps([]))
            )
            conn.commit()
        finally:
            conn.close()
        provider = DemoAuthenticationProvider()
        self.actor_a = provider.authenticate("PM-2213")
        self.actor_b = provider.authenticate("WORKER-B")
        self.store = PrivateMediaStore(self.media_root)

    def encounter(self, actor=None, patient_id="PT-0001"):
        return access.create_encounter(actor or self.actor_a, "consultation", patient_id=patient_id)

    def test_case_artifacts_survive_later_cases_and_duplicate_names(self):
        case_a = self.encounter()
        case_b = self.encounter()
        bytes_a, bytes_b = image_bytes((255, 0, 0)), image_bytes((0, 0, 255))
        artifact_a = self.store.store(
            self.actor_a, case_a.encounter_id, bytes_a, original_filename="scan.png",
            purpose="xray_image", modality_confirmed=True,
        )
        artifact_b = self.store.store(
            self.actor_a, case_b.encounter_id, bytes_b, original_filename="scan.png",
            purpose="xray_image", modality_confirmed=True,
        )
        audio_a = self.store.store(
            self.actor_a, case_a.encounter_id, wav_bytes(), original_filename="same.wav",
            purpose="audio",
        )
        audio_b = self.store.store(
            self.actor_a, case_b.encounter_id, wav_bytes(b"\x02\x03" * 20),
            original_filename="same.wav", purpose="audio",
        )

        self.assertNotEqual(artifact_a["artifact_id"], artifact_b["artifact_id"])
        self.assertNotEqual(artifact_a["storage_path"], artifact_b["storage_path"])
        self.assertNotEqual(artifact_a["sha256"], artifact_b["sha256"])
        self.assertNotEqual(audio_a["sha256"], audio_b["sha256"])
        self.assertEqual(self.store.read(self.actor_a, artifact_a["artifact_id"]), bytes_a)
        self.assertEqual(self.store.read(self.actor_a, artifact_b["artifact_id"]), bytes_b)
        with self.assertRaises(access.AuthorizationError):
            self.store.authorized_path(
                self.actor_a, artifact_a["artifact_id"],
                expected_purpose="xray_image", expected_encounter_id=case_b.encounter_id,
            )
        with self.assertRaises(access.AuthorizationError):
            graph.start_case(
                case_b.encounter_id, self.actor_a,
                image_artifact_id=artifact_a["artifact_id"], media_store=self.store,
                checkpoint_db=str(Path(self.temp.name) / "unused-checkpoint.db"),
            )
        self.assertEqual(access.get_encounter(self.actor_a, case_b.encounter_id).status,
                         "created")

        case_c = self.encounter()
        self.store.store(
            self.actor_a, case_c.encounter_id, image_bytes((0, 255, 0)),
            original_filename="scan.png", purpose="xray_image", modality_confirmed=True,
        )
        self.assertEqual(self.store.read(self.actor_a, artifact_a["artifact_id"]), bytes_a)
        self.assertEqual(self.store.read(self.actor_a, artifact_b["artifact_id"]), bytes_b)
        self.assertEqual(artifact_a["original_filename"], artifact_b["original_filename"])
        self.assertNotIn("assets", Path(artifact_a["storage_path"]).parts)
        self.assertTrue(Path(artifact_a["storage_path"]).is_relative_to(self.media_root.resolve()))

    def test_cross_facility_actor_cannot_read_or_replace_case_media(self):
        case_a = self.encounter()
        artifact = self.store.store(
            self.actor_a, case_a.encounter_id, image_bytes((10, 20, 30)),
            original_filename="evidence.png", purpose="xray_image", modality_confirmed=True,
        )
        for operation in (
            lambda: self.store.get_metadata(self.actor_b, artifact["artifact_id"]),
            lambda: self.store.read(self.actor_b, artifact["artifact_id"]),
            lambda: self.store.store(
                self.actor_b, case_a.encounter_id, image_bytes((99, 99, 99)),
                original_filename="evidence.png", purpose="xray_image", modality_confirmed=True,
            ),
        ):
            with self.subTest(operation=operation), self.assertRaises(access.AuthorizationError):
                operation()
        self.assertEqual(self.store.read(self.actor_a, artifact["artifact_id"]),
                         image_bytes((10, 20, 30)))

    def test_content_size_and_modality_validation(self):
        encounter = self.encounter()
        invalid_cases = [
            (b"", "audio", False),
            (b"not media", "audio", False),
            (b"\x89PNG\r\n\x1a\ntruncated", "xray_image", True),
            (wav_bytes(), "prescription_image", True),
            (image_bytes((1, 2, 3)), "xray_image", False),
        ]
        for data, purpose, confirmed in invalid_cases:
            with self.subTest(purpose=purpose, confirmed=confirmed), self.assertRaises(MediaValidationError):
                self.store.store(
                    self.actor_a, encounter.encounter_id, data, original_filename="spoofed.jpg",
                    purpose=purpose, modality_confirmed=confirmed,
                )
        tiny_store = PrivateMediaStore(self.media_root / "tiny", max_bytes=20)
        with self.assertRaises(MediaValidationError):
            tiny_store.store(
                self.actor_a, encounter.encounter_id, wav_bytes(), original_filename="large.wav",
                purpose="audio",
            )

    def test_hash_metadata_integrity_and_purpose_are_enforced(self):
        encounter = self.encounter()
        data = image_bytes((20, 30, 40), "JPEG")
        artifact = self.store.store(
            self.actor_a, encounter.encounter_id, data,
            original_filename="..\\unsafe\\prescription.jpg",
            purpose="prescription_image", modality_confirmed=True,
        )
        self.assertEqual(artifact["size_bytes"], len(data))
        self.assertEqual(artifact["detected_mime"], "image/jpeg")
        self.assertEqual(artifact["modality_confirmed"], 1)
        self.assertEqual(artifact["confirmation_actor_id"], self.actor_a.actor_id)
        self.assertEqual(Path(artifact["original_filename"]).name, artifact["original_filename"])
        self.assertEqual(self.store.read(self.actor_a, artifact["artifact_id"]), data)
        with self.assertRaises(MediaValidationError):
            self.store.authorized_path(
                self.actor_a, artifact["artifact_id"], expected_purpose="xray_image"
            )
        stored_path = Path(artifact["storage_path"])
        stored_path.write_bytes(data + b"tampered")
        with self.assertRaises(IOError):
            self.store.read(self.actor_a, artifact["artifact_id"])


if __name__ == "__main__":
    unittest.main()
