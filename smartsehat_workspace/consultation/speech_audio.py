"""Content-bound optional TTS cache. Generated audio always needs human review."""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import wave

from .clinical import ROOT
from .privacy import save_media, read_media


class SpeechProviderError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        super().__init__("Speech provider returned HTTP " + str(status))


def text_identity(text: str, language: str) -> str:
    return hashlib.sha256(json.dumps([language, text], ensure_ascii=False).encode()).hexdigest()


def recorded_prompt(text: str, language: str) -> Path | None:
    from .ai import _cache_read
    try:
        manifest = ROOT / "data/consultation_prompt_recordings" / (text_identity(text, language) + ".json")
        data = json.loads(_cache_read(manifest))
        path = Path(data["path"]).resolve()
        if data["text_hash"] == text_identity(text, language) and path.is_relative_to((ROOT / "assets/prompt_audio").resolve()) and path.is_file():
            return path
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _save_prompt(text: str, language: str, content: bytes, suffix: str, operator_confirmed: bool, origin: str) -> Path:
    from .ai import _cache_write
    if language not in ("English", "Urdu") or suffix not in (".wav", ".mp3", ".webm", ".m4a", ".ogg") or not content or len(content) > 20 * 1024 * 1024:
        raise ValueError("Confirm a supported prompt recording smaller than 20 MB.")
    signatures = {".wav": content[:4] == b"RIFF" and content[8:12] == b"WAVE", ".mp3": content[:3] == b"ID3" or content[:1] == b"\xff",
                  ".ogg": content[:4] == b"OggS", ".webm": content[:4] == b"\x1aE\xdf\xa3", ".m4a": content[4:8] == b"ftyp"}
    if not signatures[suffix]:
        raise ValueError("The recording does not match its audio file type.")
    identity = text_identity(text, language)
    target = ROOT / "assets/prompt_audio" / (identity + "-" + hashlib.sha256(content).hexdigest()[:16] + suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    saved = save_media(target, content)
    _cache_write(ROOT / "data/consultation_prompt_recordings" / (identity + ".json"), json.dumps({"path": str(saved), "language": language, "text_hash": identity,
        "operator_confirmed_wording": operator_confirmed, "origin": origin, "clinical_and_native_speaker_validation": "pending"}))
    return saved


def save_prompt_recording(text: str, language: str, content: bytes, suffix: str, confirmed: bool = False) -> Path:
    if confirmed is not True:
        raise ValueError("Listen and confirm the exact prompt recording before saving it.")
    return _save_prompt(text, language, content, suffix, True, "operator supplied recording")


def save_generated_prompt(text: str, language: str, content: bytes, origin: str) -> Path:
    return _save_prompt(text, language, content, ".wav", False, origin)


def synthesize(text: str, language: str, live: bool = False) -> tuple[Path | None, str]:
    """No call without explicit live=True. Never mutates an encounter."""
    if language not in ("English", "Urdu") or not text.strip() or len(text) > 5000:
        return None, "Use a human recording for this language or long read-back. No text is truncated."
    model = os.environ.get("CONSULTATION_TTS_MODEL", "gemini-3.8-flash-lite-tts")
    identity = hashlib.sha256((model + ":Kore:" + text_identity(text, language)).encode()).hexdigest()
    base = ROOT / "data/consultation_speech_cache" / (identity + ".wav")
    path = base.with_suffix(".wav.enc")
    legacy = path.with_suffix(".enc.enc")
    if not path.exists() and legacy.exists():
        save_media(base, read_media(legacy))
    if path.exists():
        return path, "Cached generated audio; listen and confirm the exact wording before use."
    if not live:
        return None, "No cached audio. Read aloud, record it, or explicitly request generation."
    from .ai import _bounded_json
    def request():
        import httpx
        from dotenv import load_dotenv
        load_dotenv()
        key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("Speech provider is not configured.")
        body = {"contents": [{"role": "user", "parts": [{"text": text, "speech_metadata": {"style": "Clear, steady reading in " + language + "."}}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"voice": "Kore"}}}}
        response = httpx.post("https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent", headers={"x-goog-api-key": key}, json=body, timeout=30)
        if response.status_code != 200:
            # Never surface raw provider messages, headers or credentials.
            raise SpeechProviderError(response.status_code)
        parts = response.json()["candidates"][0]["content"]["parts"]
        audio = next(part["inlineData"] for part in parts if "inlineData" in part)
        content = base64.b64decode(audio["data"], validate=True)
        if len(content) > 20 * 1024 * 1024:
            raise ValueError("Generated recording exceeds the size limit.")
        with wave.open(io.BytesIO(content), "rb") as clip:
            if not clip.getnframes() or clip.getnchannels() != 1:
                raise ValueError("Speech provider did not return a supported WAV recording.")
        path.parent.mkdir(parents=True, exist_ok=True)
        saved = save_media(base, content)
        return str(saved), "Generated audio; human listening/wording confirmation required."
    try:
        result, mode = _bounded_json("tts:" + identity, request)
        return Path(result), mode
    except Exception as exc:
        category = "provider HTTP " + str(exc.status) if isinstance(exc, SpeechProviderError) else type(exc).__name__
        return None, "Speech generation unavailable (" + category + "). Use a human recording; no case was changed."


def offline_readback(name: str, packet: dict, language: str, audio: bytes | None = None, audio_mime: str = "audio/wav") -> str:
    import html
    summary = packet["readbacks"][language]["text"]
    if audio_mime not in ("audio/wav", "audio/mpeg", "audio/webm", "audio/mp4", "audio/ogg"):
        raise ValueError("Unsupported read-back audio type.")
    player = '<audio controls src="data:' + audio_mime + ';base64,' + base64.b64encode(audio).decode() + '"></audio>' if audio else ""
    return '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Consultation read-back</title><style>body{font:20px system-ui;max-width:800px;margin:24px auto;padding:20px;white-space:pre-wrap}audio{width:100%}</style><p>SYNTHETIC · Research prototype · Complete read-back</p><h1>' + html.escape(name) + '</h1><p>Packet ' + str(packet["version"]) + " · " + html.escape(language) + '</p>' + player + '<article dir="' + ("ltr" if language == "English" else "rtl") + '">' + html.escape(summary) + '</article><p>Keep this exact version. Corrections require a new packet and new confirmation.</p>'
