"""Private, immutable, encounter-owned clinical media storage."""
from __future__ import annotations
import hashlib
from io import BytesIO
import os
from pathlib import Path
import tempfile
import uuid
import wave
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from . import audit
from .access import AuthorizationError, get_encounter, require_permission
from .domain import ActorContext


class MediaValidationError(ValueError):
    pass


MIME_EXTENSIONS = {"audio/wav": ".wav", "image/jpeg": ".jpg", "image/png": ".png"}
PURPOSE_MIMES = {
    "audio": {"audio/wav"},
    "xray_image": {"image/jpeg", "image/png"},
    "prescription_image": {"image/jpeg", "image/png"},
}


def detect_mime(data: bytes) -> str | None:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def validate_media_bytes(data: bytes, detected_mime: str) -> None:
    """Reject truncated/spoofed files after signature-based type detection."""
    try:
        if detected_mime == "audio/wav":
            with wave.open(BytesIO(data), "rb") as source:
                if source.getnchannels() < 1 or source.getframerate() < 1:
                    raise MediaValidationError("Invalid WAV audio parameters.")
                source.readframes(1)
        elif detected_mime in {"image/jpeg", "image/png"}:
            with Image.open(BytesIO(data)) as image:
                expected = "JPEG" if detected_mime == "image/jpeg" else "PNG"
                if image.format != expected:
                    raise MediaValidationError("Image content does not match its detected media type.")
                image.verify()
        else:
            raise MediaValidationError("Unsupported media content.")
    except MediaValidationError:
        raise
    except (EOFError, OSError, ValueError, UnidentifiedImageError, wave.Error) as exc:
        raise MediaValidationError("The uploaded media is corrupt or incomplete.") from exc


class PrivateMediaStore:
    def __init__(self, root: str | Path | None = None, max_bytes: int | None = None):
        configured = root or os.getenv("SMARTSEHAT_MEDIA_ROOT") or (
            Path(__file__).resolve().parent.parent / "runtime" / "private-media"
        )
        self.root = Path(configured).resolve()
        self.max_bytes = max_bytes or int(os.getenv("SMARTSEHAT_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

    def store(self, actor: ActorContext, encounter_id: str, source: bytes | bytearray | BinaryIO,
              *, original_filename: str | None, purpose: str,
              modality_confirmed: bool = False) -> dict:
        encounter = get_encounter(actor, encounter_id, "encounter:read")
        require_permission(actor, "media:create", encounter.facility_id)
        if encounter.status == "completed":
            raise AuthorizationError("Completed encounter media is immutable.")
        if purpose not in PURPOSE_MIMES:
            raise MediaValidationError("Unsupported media purpose.")
        data = bytes(source) if isinstance(source, (bytes, bytearray)) else source.read(self.max_bytes + 1)
        if not data:
            raise MediaValidationError("Empty upload rejected.")
        if len(data) > self.max_bytes:
            raise MediaValidationError(f"Upload exceeds the {self.max_bytes}-byte limit.")
        mime = detect_mime(data)
        if mime not in PURPOSE_MIMES[purpose]:
            raise MediaValidationError(f"Detected media type {mime or 'unknown'} is not allowed for {purpose}.")
        validate_media_bytes(data, mime)
        if purpose in {"xray_image", "prescription_image"} and modality_confirmed is not True:
            raise MediaValidationError("Worker confirmation of the image purpose is required.")
        artifact_id = f"MEDIA-{uuid.uuid4().hex}"
        directory = (self.root / encounter.facility_id / encounter_id).resolve()
        try:
            directory.relative_to(self.root)
        except ValueError as exc:
            raise MediaValidationError("Unsafe media storage path.") from exc
        directory.mkdir(parents=True, exist_ok=True)
        final_path = directory / f"{artifact_id}{MIME_EXTENSIONS[mime]}"
        digest = hashlib.sha256(data).hexdigest()
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".upload-", delete=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, final_path)
            temp_path = None
            now = audit._now()
            conn = audit._connect()
            try:
                conn.execute(
                    "INSERT INTO media_artifacts(artifact_id,case_id,patient_id,identity_kind,"
                    "uploader_actor_id,bhu_id,original_filename,detected_mime,size_bytes,sha256,"
                    "created_at,storage_path,purpose,status,modality_confirmed,confirmation_actor_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (artifact_id, encounter_id, encounter.patient_id, encounter.identity_kind,
                     actor.actor_id, encounter.facility_id, Path(original_filename).name if original_filename else None,
                     mime, len(data), digest, now, str(final_path), purpose, "active",
                     int(modality_confirmed), actor.actor_id if modality_confirmed else None),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            if temp_path and temp_path.exists():
                temp_path.unlink()
            if final_path.exists():
                final_path.unlink()
            raise
        return self.get_metadata(actor, artifact_id)

    def get_metadata(self, actor: ActorContext, artifact_id: str) -> dict:
        conn = audit._connect()
        try:
            row = conn.execute("SELECT * FROM media_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            raise LookupError("Media artifact not found.")
        value = dict(row)
        get_encounter(actor, value["case_id"], "encounter:read")
        require_permission(actor, "media:read", value["bhu_id"])
        return value

    def read(self, actor: ActorContext, artifact_id: str) -> bytes:
        metadata = self.get_metadata(actor, artifact_id)
        path = Path(metadata["storage_path"]).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise AuthorizationError("Artifact storage reference is outside the private store.") from exc
        data = path.read_bytes()
        if len(data) != metadata["size_bytes"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise IOError("Stored media integrity check failed.")
        return data

    def authorized_path(self, actor: ActorContext, artifact_id: str,
                        *, expected_purpose: str | None = None,
                        expected_encounter_id: str | None = None) -> str:
        metadata = self.get_metadata(actor, artifact_id)
        if (expected_encounter_id is not None
                and metadata["case_id"] != expected_encounter_id):
            raise AuthorizationError("Artifact belongs to a different encounter.")
        if expected_purpose is not None and metadata["purpose"] != expected_purpose:
            raise MediaValidationError(
                f"Artifact purpose {metadata['purpose']} cannot be used as {expected_purpose}."
            )
        self.read(actor, artifact_id)
        return metadata["storage_path"]

    def list_for_encounter(self, actor: ActorContext, encounter_id: str) -> list[dict]:
        get_encounter(actor, encounter_id, "encounter:read")
        conn = audit._connect()
        try:
            ids = [row[0] for row in conn.execute(
                "SELECT artifact_id FROM media_artifacts WHERE case_id=? ORDER BY created_at", (encounter_id,)
            ).fetchall()]
        finally:
            conn.close()
        return [self.get_metadata(actor, artifact_id) for artifact_id in ids]
