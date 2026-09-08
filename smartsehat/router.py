"""Provider-agnostic inference router — final chain configuration.

Priority chains are configured in config.yaml and shared by voice and agents.

STT chain:
  1. Google AI Studio gemini-3.6-flash
  2. AIHubMix gemini-3.8-flash-free
  3. TokenHarbor mimo-v2.5:free
  4. Qwen qwen3-asr-flash (native DashScope)
  5. Groq whisper-large-v3

Prescription and X-ray vision: Qwen, Google Gemini, then AIHubMix.
Reasoning: Groq, Qwen, Google Gemini, then AIHubMix.

Carried laws:
- Bug #2: when ALL providers in a route fail, the router RAISES —
  never an error string disguised as model output.
- 503/overload: one quick retry (2s pause) before giving a provider up.
- Language-aware STT: 'ur' | 'en' | 'hi' selects the transcription
  prompt. The Qwen tier does NOT transliterate: per A03 its `language`
  param is intentionally unused and the ORIGINAL ASR text is returned
  verbatim (any devanagari_to_urdu conversion is display-only, never
  applied to the clinical transcript or triage).
- Per-spec model selection binds a cloned AIHubMix provider to the
  model id selected in config.yaml.
"""
from __future__ import annotations
import base64
import mimetypes
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
import yaml

# Tests/offline runs can prevent local secrets from being loaded at import time.
if os.getenv("DOTENV_DISABLED") != "1":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass


def load_config() -> dict:
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    mode = os.getenv("SMARTSEHAT_MODE")
    if os.getenv("SMARTSEHAT_OFFLINE") == "1":
        mode = "stub"
    if mode:
        mode = mode.strip().lower()
        if mode not in {"stub", "real"}:
            raise ValueError("SMARTSEHAT_MODE must be 'stub' or 'real'.")
        config["agents"] = mode
    return config


def _image_data_url(image_path: str) -> str:
    """Encode a local image file as a data: URL for OpenAI-compatible vision."""
    mime, _ = mimetypes.guess_type(image_path)
    if not mime:
        mime = "image/jpeg"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _image_mime(image_path: str) -> str:
    mime, _ = mimetypes.guess_type(image_path)
    return mime or "image/jpeg"


def _is_transient_overload(e: Exception) -> bool:
    """503 / UNAVAILABLE / 'high demand' — worth one quick retry."""
    msg = str(e)
    return ("503" in msg or "UNAVAILABLE" in msg or "high demand" in msg
            or "overloaded" in msg)


# Language-aware STT prompts shared by Gemini-style providers.
_STT_PROMPTS = {
    "ur": "Transcribe this Urdu speech verbatim, in Urdu script. Output ONLY the transcript.",
    "en": "Transcribe this speech verbatim, in English. Output ONLY the transcript.",
    "hi": "Transcribe this Hindi speech verbatim, in Devanagari script. Output ONLY the transcript.",
}


class InferenceProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, messages: list[dict], temperature: float = 0.2) -> str: ...

    @abstractmethod
    def transcribe(self, audio_path: str, language: str = "ur") -> str: ...

    @abstractmethod
    def vision(self, image_path: str, prompt: str, temperature: float = 0.2) -> str: ...

    @abstractmethod
    def healthy(self) -> bool: ...


# ======================================================================
# AIHubMix — Gemini-native proxy (both free models)
# ======================================================================

class AIHubMixProvider(InferenceProvider):
    """AIHubMix — Gemini-native proxy (free gemini-3.8/3.7-flash).
    Probe-verified live: full Urdu-script STT of both demo clips,
    including code-switching. Audio via multimodal generate_content
    against https://aihubmix.com/gemini."""
    name = "aihubmix"

    def __init__(self, model: str = "gemini-3.8-flash-free"):
        self.model = model
        self.api_key = os.getenv("AIHUBMIX_API_KEY")
        self._client = None
        if self.api_key:
            try:
                from google import genai
                self._client = genai.Client(
                    api_key=self.api_key,
                    http_options={"base_url": "https://aihubmix.com/gemini"})
            except Exception:
                self._client = None

    def with_model(self, model: str) -> "AIHubMixProvider":
        """Return a shallow copy bound to a specific model id — lets the
        router treat gemini-3.8-flash-free and gemini-3.7-flash-free as
        separate chain entries on the same provider."""
        clone = AIHubMixProvider.__new__(AIHubMixProvider)
        clone.model = model
        clone.api_key = self.api_key
        clone._client = self._client
        return clone

    def healthy(self) -> bool:
        return self._client is not None

    def generate(self, messages, temperature: float = 0.2) -> str:
        if not self.healthy():
            raise RuntimeError("AIHubMix not configured")
        prompt = "\n".join(m.get("content", "") for m in messages)
        r = self._client.models.generate_content(
            model=self.model, contents=prompt,
            config={"max_output_tokens": 4096})
        return r.text

    def transcribe(self, audio_path: str, language: str = "ur") -> str:
        if not self.healthy():
            raise RuntimeError("AIHubMix not configured")
        from google.genai import types
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
        part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav")
        prompt = _STT_PROMPTS.get(language, _STT_PROMPTS["ur"])
        r = self._client.models.generate_content(
            model=self.model, contents=[part, prompt],
            config={"max_output_tokens": 2048})
        try:
            return r.text
        except Exception:
            texts = []
            for cand in (r.candidates or []):
                for p in (getattr(cand.content, "parts", None) or []):
                    t = getattr(p, "text", None)
                    if t:
                        texts.append(t)
            if texts:
                return "\n".join(texts)
            raise RuntimeError(f"AIHubMix STT returned no text: {r}")

    def vision(self, image_path: str, prompt: str, temperature: float = 0.2) -> str:
        if not self.healthy():
            raise RuntimeError("AIHubMix not configured")
        from google.genai import types
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        part = types.Part.from_bytes(data=image_bytes,
                                     mime_type=_image_mime(image_path))
        r = self._client.models.generate_content(
            model=self.model, contents=[part, prompt],
            config={"max_output_tokens": 4096})
        return r.text


# ======================================================================
# Google AI Studio — native Gemini
# ======================================================================

class GeminiProvider(InferenceProvider):
    name = "gemini"

    def __init__(self, model: str = "gemini-3.6-flash"):
        self.model = model
        self.api_key = os.getenv("GOOGLE_API_KEY")
        self._client = None
        if self.api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except Exception:
                self._client = None

    def healthy(self) -> bool:
        return self._client is not None

    def generate(self, messages: list[dict], temperature: float = 0.2) -> str:
        if not self.healthy():
            raise RuntimeError("Gemini not configured")
        prompt = "\n".join(m.get("content", "") for m in messages)
        r = self._client.models.generate_content(model=self.model,
                                                 contents=prompt)
        return r.text

    def transcribe(self, audio_path: str, language: str = "ur") -> str:
        if not self.healthy():
            raise RuntimeError("Gemini not configured")
        from google.genai import types
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
        part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav")
        prompt = _STT_PROMPTS.get(language, _STT_PROMPTS["ur"])
        r = self._client.models.generate_content(model=self.model,
                                                 contents=[part, prompt])
        try:
            return r.text
        except Exception:
            texts = []
            for cand in (r.candidates or []):
                for p in (getattr(cand.content, "parts", None) or []):
                    t = getattr(p, "text", None)
                    if t:
                        texts.append(t)
            if texts:
                return "\n".join(texts)
            raise RuntimeError(f"Gemini STT returned no text: {r}")

    def vision(self, image_path: str, prompt: str, temperature: float = 0.2) -> str:
        if not self.healthy():
            raise RuntimeError("Gemini not configured")
        from google.genai import types
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        part = types.Part.from_bytes(data=image_bytes,
                                     mime_type=_image_mime(image_path))
        r = self._client.models.generate_content(model=self.model,
                                                 contents=[part, prompt])
        return r.text


# ======================================================================
# TokenHarbor — mimo-v2.5:free (voice-capable)
# ======================================================================

class TokenHarborProvider(InferenceProvider):
    """TokenHarbor — OpenAI-compatible endpoint, voice-capable Mimo.
    Probe-verified live: Urdu-script STT of both clips; one-letter slip
    on the pregnancy clip tolerated by the keyword floor + confirm gate.
    deepseek-v4-flash:free also available as a text model."""
    name = "tokenharbor"

    def __init__(self, model: str = "mimo-v2.5:free"):
        self.model = model
        self.api_key = os.getenv("TOKENHARBOR_API_KEY")
        self._client = None
        if self.api_key:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key,
                                      base_url="https://tokenharbor.ai/v1")
            except Exception:
                self._client = None

    def healthy(self) -> bool:
        return self._client is not None

    def generate(self, messages, temperature: float = 0.2) -> str:
        if not self.healthy():
            raise RuntimeError("TokenHarbor not configured")
        r = self._client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature)
        return r.choices[0].message.content

    def transcribe(self, audio_path: str, language: str = "ur") -> str:
        # Probe-verified: audio via OpenAI-style input_audio parts;
        # Urdu-script output natively.
        if not self.healthy():
            raise RuntimeError("TokenHarbor not configured")
        import base64 as _b64
        with open(audio_path, "rb") as f:
            b64 = _b64.b64encode(f.read()).decode("ascii")
        r = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": [
                {"type": "text",
                 "text": _STT_PROMPTS.get(language, _STT_PROMPTS["ur"])},
                {"type": "input_audio",
                 "input_audio": {"data": b64, "format": "wav"}},
            ]}],
            max_tokens=2048)
        return r.choices[0].message.content

    def vision(self, image_path: str, prompt: str, temperature: float = 0.2) -> str:
        raise RuntimeError("no vision on tokenharbor in this chain")


# ======================================================================
# Qwen — native DashScope ASR + vision/reasoning via OpenAI-compatible
# ======================================================================

class QwenProvider(InferenceProvider):
    name = "qwen"
    DEFAULT_ENDPOINT = "https://ws-ah8evok5ajq7vsm0.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    # Qwen3-ASR does NOT work via the OpenAI-compatible /audio endpoint —
    # only the native DashScope multimodal-generation shape works.
    DEFAULT_ASR_ENDPOINT = "https://ws-ah8evok5ajq7vsm0.ap-southeast-1.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

    def __init__(self, reasoning_model: str = "qwen3-max",
                 vision_model: str = "qwen3-vl-plus",
                 endpoint: str | None = None,
                 asr_endpoint: str | None = None,
                 asr_model: str | None = None):
        self.reasoning_model = reasoning_model
        self.vision_model = vision_model
        self.endpoint = endpoint or self.DEFAULT_ENDPOINT
        self.asr_endpoint = asr_endpoint or self.DEFAULT_ASR_ENDPOINT
        self.asr_model = asr_model or "qwen3-asr-flash"
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
        self._client = None
        if self.api_key:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key,
                                      base_url=self.endpoint)
            except Exception:
                self._client = None

    def healthy(self) -> bool:
        return self._client is not None

    def generate(self, messages: list[dict], temperature: float = 0.2) -> str:
        if not self.healthy():
            raise RuntimeError("Qwen not configured")
        r = self._client.chat.completions.create(
            model=self.reasoning_model, messages=messages,
            temperature=temperature)
        return r.choices[0].message.content

    def transcribe(self, audio_path: str, language: str = "ur") -> str:
        # `language` is intentionally unused by the Qwen tier after A03; it is
        # retained only for signature/contract stability with the other STT
        # providers (the DashScope payload below sends no language hint).
        # Native DashScope ASR path. Qwen3-ASR emits Devanagari for Urdu
        # speech (bake-off caveat #4). SAFETY (A03): the string returned here
        # becomes TranscriptProvenance.raw_text -> accepted_text, which the
        # deterministic triage (rules_triage._haystack) reads. rules_triage
        # already covers Devanagari tokens, so the ORIGINAL ASR text must be
        # preserved verbatim. Any script conversion is display-only and must
        # never feed triage.
        if not self.api_key:
            raise RuntimeError("Qwen not configured")
        import requests
        mime, _ = mimetypes.guess_type(audio_path)
        if not mime or not mime.startswith("audio/"):
            mime = "audio/wav"
        with open(audio_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        payload = {
            "model": self.asr_model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "audio",
                             "audio": f"data:{mime};base64,{b64}"},
                        ],
                    }
                ]
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(self.asr_endpoint, json=payload,
                             headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        for choice in (data.get("output", {}) or {}).get("choices", []):
            for part in (choice.get("message", {}) or {}).get("content", []):
                if isinstance(part, dict) and part.get("text"):
                    # A03 safety fix: return the ORIGINAL ASR text unchanged.
                    # The previous lossy devanagari_to_urdu(text) conversion
                    # produced broken mixed-script text that dropped the urgent
                    # Devanagari evidence (e.g. सीने/दर्द/पसीना), silently
                    # downgrading RED chest-pain emergencies to
                    # NO_CONFIGURED_FLAG. Transliteration is display-only and is
                    # deliberately NOT applied to the clinical transcript here.
                    return part["text"]
        raise RuntimeError(f"Qwen ASR returned no transcript: {str(data)[:300]}")

    def vision(self, image_path: str, prompt: str, temperature: float = 0.2) -> str:
        if not self.healthy():
            raise RuntimeError("Qwen not configured")
        data_url = _image_data_url(image_path)
        r = self._client.chat.completions.create(
            model=self.vision_model,
            temperature=temperature,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
        )
        return r.choices[0].message.content


# ======================================================================
# Groq — whisper STT (last resort) + gpt-oss reasoning (primary)
# ======================================================================

class GroqProvider(InferenceProvider):
    name = "groq"

    def __init__(self, reasoning_model: str = "gpt-oss-120b",
                 stt_model: str = "whisper-large-v3"):
        self.reasoning_model = reasoning_model
        self.stt_model = stt_model
        self.api_key = os.getenv("GROQ_API_KEY")
        self._client = None
        if self.api_key:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key,
                                      base_url="https://api.groq.com/openai/v1")
            except Exception:
                self._client = None

    def healthy(self) -> bool:
        return self._client is not None

    def generate(self, messages: list[dict], temperature: float = 0.2) -> str:
        if not self.healthy():
            raise RuntimeError("Groq not configured")
        r = self._client.chat.completions.create(
            model=self.reasoning_model, messages=messages,
            temperature=temperature)
        return r.choices[0].message.content

    def transcribe(self, audio_path: str, language: str = "ur") -> str:
        if not self.healthy():
            raise RuntimeError("Groq not configured")
        with open(audio_path, "rb") as f:
            r = self._client.audio.transcriptions.create(
                model=self.stt_model, file=f)
        return r.text

    def vision(self, image_path: str, prompt: str, temperature: float = 0.2) -> str:
        raise RuntimeError("no vision on groq")


# ======================================================================
# The Router
# ======================================================================

class ModelRouter:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        endpoints = self.config.get("endpoints", {})
        asr_cfg = self.config.get("asr", {})
        self.providers = {
            "groq": GroqProvider(),
            "gemini": GeminiProvider(),
            "qwen": QwenProvider(
                endpoint=endpoints.get("qwen"),
                asr_endpoint=endpoints.get("qwen_asr"),
                asr_model=asr_cfg.get("qwen_model"),
            ),
            "aihubmix": AIHubMixProvider(),
            "tokenharbor": TokenHarborProvider(),
        }

    def _resolve(self, spec: str) -> InferenceProvider:
        provider_name = spec.split("/", 1)[0]
        return self.providers[provider_name]

    def _attempt(self, prov: InferenceProvider, op: str, *args, **kwargs):
        if op == "generate":
            return prov.generate(*args, **kwargs)
        if op == "transcribe":
            return prov.transcribe(*args, **kwargs)
        if op == "vision":
            return prov.vision(*args, **kwargs)
        raise ValueError(f"unknown op {op}")

    def _route(self, role: str, op: str, *args, **kwargs) -> tuple[Any, str]:
        if (self.config.get("agents") or "stub").lower() != "real":
            raise RuntimeError(
                "External providers are disabled in offline/stub mode. "
                "Use the explicit demo/manual workflow or opt into real mode."
            )
        chain = self.config["role_routes"][role]
        last_err: Exception | None = None
        for spec in chain:
            prov = self._resolve(spec)
            # Bind an AIHubMix provider clone to the configured model id.
            try:
                if prov.name == "aihubmix" and "/" in spec:
                    prov = prov.with_model(spec.split("/", 1)[1])
                return self._attempt(prov, op, *args, **kwargs), spec
            except Exception as e:
                last_err = e
                # 503 / overloaded: one quick retry (2s pause) before
                # giving this provider up — demand spikes are often
                # seconds-long.
                if _is_transient_overload(e):
                    time.sleep(2)
                    try:
                        return self._attempt(prov, op, *args, **kwargs), spec
                    except Exception as e2:
                        last_err = e2
                continue
        # Never return an error string disguised as model output — callers
        # must catch this and enter an explicit degraded mode.
        raise RuntimeError(f"All providers failed for role '{role}': {last_err}")

    def route_generate(self, role: str, messages: list[dict],
                       temperature: float = 0.2):
        return self._route(role, "generate", messages, temperature)

    def route_transcribe(self, role: str, audio_path: str,
                         language: str = "ur"):
        return self._route(role, "transcribe", audio_path, language)

    def route_vision(self, role: str, image_path: str, prompt: str,
                     temperature: float = 0.2):
        return self._route(role, "vision", image_path, prompt, temperature)

    def provider_health(self) -> dict[str, bool]:
        if (self.config.get("agents") or "stub").lower() != "real":
            return {name: False for name in self.providers}
        return {name: p.healthy() for name, p in self.providers.items()}
