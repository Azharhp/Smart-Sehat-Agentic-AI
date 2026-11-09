"""Packet-bound recordings of the complete worker/patient read-back."""
from .speech_audio import text_identity


class ReadbackWorkflow:
    def readback_view(self, cid: str, packet: dict, language: str) -> dict | None:
        if language in packet["readbacks"]:
            return packet
        source_hash = text_identity(packet["readbacks"]["English"]["text"], "English")
        for event in reversed(self.events(cid)):
            data = event["data"]
            if event["type"] == "READBACK_TRANSLATION_CONFIRMED" and data["packet_version"] == packet["version"] and data["language"] == language and data["source_hash"] == source_hash:
                return {**packet, "readbacks": {**packet["readbacks"], language: {"text": data["text"], "language": language, "fact_ids": packet["fact_ids"], "interpreter": data["interpreter"]}}}
        return None

    def confirm_readback_translation(self, cid: str, version: int, language: str, text: str, interpreter: str, confirmed: bool = False) -> None:
        from .languages import VOICE_LANGUAGES
        from .patient import _numbers
        if language not in VOICE_LANGUAGES or language in ("English", "Urdu") or not text.strip() or len(text) > 30000 or len(interpreter.strip()) < 2 or confirmed is not True:
            raise ValueError("A named interpreter must confirm the complete translated read-back in a listed language.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            packet = self._output(db, cid, "packet")
            if not packet or packet["version"] != version:
                raise ValueError("The packet changed. Translate the current complete read-back.")
            source = packet["readbacks"]["English"]["text"]
            medicines = next((f["value"] for f in self._facts(db, cid) if f["key"] == "medicines"), []) or []
            if _numbers(source) != _numbers(text) or any(name.casefold() not in text.casefold() for name in medicines):
                raise ValueError("Preserve all written numbers and medicine names in the translated read-back.")
            self._event(db, cid, "READBACK_TRANSLATION_CONFIRMED", {"packet_version": version, "language": language,
                "text": text.strip(), "source_hash": text_identity(source, "English"), "interpreter": interpreter.strip(), "identity_verified": False,
                "scope": "Human-reported translation; numeric/name text guard is not a semantic accuracy validation."})

    def attach_readback_audio(self, cid: str, packet_version: int, language: str, path: str, confirmed: bool = False) -> None:
        if confirmed is not True:
            raise ValueError("Listen and confirm that this recording matches the entire current read-back.")
        path = self._media_path(cid, path, (".wav", ".mp3", ".webm", ".m4a", ".ogg"))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            packet = self._output(db, cid, "packet")
            if not packet or packet["version"] != packet_version:
                raise ValueError("The packet changed. Record and confirm its current read-back.")
            packet = self.readback_view(cid, packet, language)
            if not packet:
                raise ValueError("Confirm the interpreter's complete translated read-back first.")
            identity = text_identity(packet["readbacks"][language]["text"], language)
            self._event(db, cid, "READBACK_AUDIO_CONFIRMED", {"packet_version": packet_version, "language": language,
                "text_hash": identity, "path": str(path), "confirmed_by": "worker", "fact_ids": packet["fact_ids"]})

    def readback_audio(self, cid: str, packet: dict, language: str) -> dict | None:
        identity = text_identity(packet["readbacks"][language]["text"], language)
        for event in reversed(self.events(cid)):
            data = event["data"]
            if event["type"] == "READBACK_AUDIO_CONFIRMED" and data["packet_version"] == packet["version"] and data["language"] == language and data["text_hash"] == identity:
                return data
        return None
