"""Lossless, checksummed case packages and resumable manual transfer.

Receiving verifies bytes; it never confirms facts or imports clinical decisions.
"""
from __future__ import annotations
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

MAX_BYTES = 50 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def package(packet: dict, *, media_reader=None) -> tuple[bytes, dict]:
    if packet.get("stale") or packet["verification"]["status"] != "VERIFIED":
        raise ValueError("Generate and verify a current packet before preparing a transfer.")
    files, media, paths = {}, [], {}
    if media_reader:
        for item in packet.get("appendix", {}).get("media", []):
            path = item.get("path")
            if not path:
                continue
            row = {"event_id": item["event_id"], "type": item["type"], "available": False}
            try:
                content, suffix = media_reader(path)
                if suffix not in (".wav", ".mp3", ".webm", ".m4a", ".ogg", ".jpg", ".jpeg", ".png", ".pdf"):
                    raise ValueError("Unsupported media type")
                name = "media/" + digest(content) + suffix
                if name not in files and sum(map(len, files.values())) + len(content) > MAX_BYTES:
                    raise ValueError("Case media exceeds the 50 MB package limit. Export separately; nothing was truncated.")
                files[name] = content
                paths[path] = name
                row.update({"available": True, "file": name, "sha256": digest(content), "bytes": len(content)})
            except (OSError, ValueError) as exc:
                if "50 MB" in str(exc):
                    raise
                row["reason"] = "Unavailable or outside this case; original audit reference retained locally."
            media.append(row)
    else:
        media = [{"event_id": item["event_id"], "type": item["type"], "available": False,
                  "reason": "Text-only transfer; request the original media separately."} for item in packet.get("appendix", {}).get("media", [])]

    def portable(value):
        if isinstance(value, list):
            return [portable(item) for item in value]
        if isinstance(value, dict):
            return {key: paths.get(item, "Local file reference; see media manifest") if key in ("path", "guidance_audio_path") and isinstance(item, str)
                    else portable(item) for key, item in value.items()}
        return value

    files["packet.json"] = encoded(portable(packet))
    files["SBAR.txt"] = packet.get("full_text", packet["text"]).encode()
    files["compact.txt"] = packet["text"].encode()
    for language, view in packet.get("readbacks", {}).items():
        if language in ("English", "Urdu"):
            files["readback-" + language + ".txt"] = view["text"].encode()
    if sum(map(len, files.values())) > MAX_BYTES:
        raise ValueError("Case exceeds the 50 MB package limit. Nothing was truncated.")
    manifest = {"format": "smartsehat-transfer-1", "packet_version": packet["version"],
                "clinical_status": "Research prototype; recipient must review current source and clinical evidence.",
                "media": media, "files": [{"name": name, "bytes": len(content), "sha256": digest(content)} for name, content in files.items()]}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", encoded(manifest))
        for name, content in files.items():
            archive.writestr(name, content)
    blob = output.getvalue()
    if len(blob) > MAX_BYTES:
        raise ValueError("Compressed transfer exceeds 50 MB. Nothing was truncated.")
    return blob, {"sha256": digest(blob), "bytes": len(blob), "uncompressed_bytes": sum(map(len, files.values())),
                  "files": len(files), "media_available": sum(row["available"] for row in media), "media_total": len(media)}


def chunks(blob: bytes) -> list[dict]:
    if not blob or len(blob) > MAX_BYTES:
        raise ValueError("Choose a nonempty transfer smaller than 50 MB.")
    identity = digest(blob)
    parts = [blob[start:start + CHUNK_BYTES] for start in range(0, len(blob), CHUNK_BYTES)]
    return [{"format": "smartsehat-chunk-1", "transfer_sha256": identity, "total_bytes": len(blob), "count": len(parts), "index": index,
             "sha256": digest(part), "data": base64.b64encode(part).decode()} for index, part in enumerate(parts)]


def chunk_archive(blob: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for part in chunks(blob):
            archive.writestr(f"part-{part['index']:04d}.json", encoded(part))
    return output.getvalue()


def assemble(parts: list[dict]) -> tuple[bytes | None, dict]:
    if not isinstance(parts, list) or not parts or len(parts) > 1600 or any(not isinstance(part, dict) for part in parts):
        raise ValueError("Supply the saved transfer parts, at most 1600 files.")
    first, received = parts[0], {}
    size, count = first.get("total_bytes"), first.get("count")
    identity = first.get("transfer_sha256")
    if type(size) is not int or not 0 < size <= MAX_BYTES or type(count) is not int or count != (size + CHUNK_BYTES - 1) // CHUNK_BYTES:
        raise ValueError("Invalid transfer size or part count.")
    if not isinstance(identity, str) or len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
        raise ValueError("Invalid transfer identity.")
    for part in parts:
        if part.get("format") != "smartsehat-chunk-1" or any(part.get(key) != first.get(key) for key in ("transfer_sha256", "total_bytes", "count")):
            raise ValueError("Parts belong to different transfers.")
        index = part.get("index")
        if type(index) is not int or not 0 <= index < count or not isinstance(part.get("data"), str) or len(part["data"]) > 90_000:
            raise ValueError("Invalid transfer part.")
        try:
            content = base64.b64decode(part["data"], validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid part encoding.") from None
        expected = CHUNK_BYTES if index < count - 1 else size - CHUNK_BYTES * (count - 1)
        if len(content) != expected or digest(content) != part.get("sha256") or index in received and received[index] != content:
            raise ValueError("A transfer part failed its checksum/size check.")
        received[index] = content
    missing = [index for index in range(count) if index not in received]
    status = {"sha256": identity, "received": len(received), "count": count, "missing": missing, "bytes": size}
    if missing:
        return None, status
    blob = b"".join(received[index] for index in range(count))
    if digest(blob) != identity:
        raise ValueError("The reconstructed transfer failed its full checksum.")
    inspect_package(blob)
    return blob, status


def inspect_package(blob: bytes) -> dict:
    """Read in memory only. Never extract paths or import clinical records."""
    if len(blob) > MAX_BYTES:
        raise ValueError("Transfer exceeds the 50 MB limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(infos) > 1000 or len(set(names)) != len(names) or sum(item.file_size for item in infos) > MAX_BYTES + 1_000_000:
                raise ValueError("Invalid or oversized package.")
            if any(item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for item in infos):
                raise ValueError("Encrypted or unsupported ZIP entries are not accepted.")
            if any(Path(name).is_absolute() or ".." in name.replace("\\", "/").split("/") or "\\" in name or ":" in name for name in names):
                raise ValueError("Unsafe package filename.")
            if archive.getinfo("manifest.json").file_size > 1_000_000:
                raise ValueError("Oversized transfer manifest.")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict) or manifest.get("format") != "smartsehat-transfer-1" or not isinstance(manifest.get("files"), list):
                raise ValueError("Unsupported transfer package.")
            rows = manifest["files"]
            if len({row["name"] for row in rows}) != len(rows) or {row["name"] for row in rows} != set(names) - {"manifest.json"}:
                raise ValueError("Transfer manifest does not match its files.")
            for row in rows:
                content = archive.read(row["name"])
                if len(content) != row["bytes"] or digest(content) != row["sha256"]:
                    raise ValueError("A package file failed its checksum.")
            packet = json.loads(archive.read("packet.json"))
            if packet["version"] != manifest["packet_version"] or packet["verification"]["status"] != "VERIFIED":
                raise ValueError("Packet version/verification does not match the manifest.")
            return {"manifest": manifest, "packet": packet, "sha256": digest(blob)}
    except (zipfile.BadZipFile, KeyError, TypeError, OSError, RuntimeError, NotImplementedError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid transfer archive (" + type(exc).__name__ + ").") from None
