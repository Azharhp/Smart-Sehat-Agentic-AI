"""Application payload/media encryption with locally held keys.

This protects stored content, not device ownership or unauthenticated network
access. Deployment authentication and TLS remain separate requirements.
"""
from __future__ import annotations
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from cryptography.fernet import Fernet, InvalidToken
from .clinical import ROOT

PREFIX = "ssenc1:"
MEDIA_PREFIX = b"SSMEDIA1\n"


class Cipher:
    def __init__(self, database_path: str):
        path = Path(database_path).resolve()
        folder = path.parent / ".consultation-keys"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".gitignore").write_text("*\n", encoding="utf-8")
        key_path = folder / (hashlib.sha256(str(path).encode()).hexdigest()[:20] + ".key")
        if not key_path.exists():
            temporary = folder / (uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_bytes(Fernet.generate_key())
                try:
                    # Publish a complete key atomically without overwriting a
                    # key concurrently created by another process.
                    os.link(temporary, key_path)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        self.fernet = Fernet(key_path.read_bytes())

    def encode(self, text: str) -> str:
        return PREFIX + self.fernet.encrypt(text.encode("utf-8")).decode("ascii")

    def decode(self, text: str) -> str:
        if not text.startswith(PREFIX):
            return text
        try:
            return self.fernet.decrypt(text[len(PREFIX):].encode("ascii")).decode("utf-8")
        except InvalidToken:
            raise ValueError("Stored content cannot be decrypted with this installation's key.") from None


@lru_cache(maxsize=1)
def media_cipher() -> Cipher:
    return Cipher(str(ROOT / "data/consultation-media"))


def save_media(path: Path, content: bytes) -> Path:
    encrypted = path.with_suffix(path.suffix + ".enc")
    encrypted.write_bytes(MEDIA_PREFIX + media_cipher().fernet.encrypt(content))
    return encrypted


def read_media(path: Path | str) -> bytes:
    content = Path(path).read_bytes()
    return media_cipher().fernet.decrypt(content[len(MEDIA_PREFIX):]) if content.startswith(MEDIA_PREFIX) else content


@contextmanager
def readable_media(path: Path | str):
    path = Path(path)
    if not path.name.endswith(".enc"):
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="smartsehat-media-") as folder:
        target = Path(folder) / path.name.removesuffix(".enc")
        target.write_bytes(read_media(path))
        yield target


class EncryptedSerializer:
    def __init__(self, database_path: str):
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        self.base = JsonPlusSerializer(pickle_fallback=False)
        self.cipher = Cipher(database_path)

    def dumps_typed(self, obj):
        kind, data = self.base.dumps_typed(obj)
        return "encrypted:" + kind, self.cipher.fernet.encrypt(data)

    def loads_typed(self, value):
        kind, data = value
        if kind.startswith("encrypted:"):
            return self.base.loads_typed((kind[len("encrypted:"):], self.cipher.fernet.decrypt(data)))
        return self.base.loads_typed(value)
