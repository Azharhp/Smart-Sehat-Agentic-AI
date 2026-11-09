"""Exact danger-screen material and available recordings for external review."""
import base64
import hashlib
import json
from pathlib import Path
from cryptography.fernet import InvalidToken

from .clinical import config
from .guidance import guide
from .illustrations import illustration

EMERGENCY_WORDING = {
    "English": "Contact emergency services and the doctor. Follow your local emergency protocol. Keep the patient warm; give nothing by mouth if unconscious. Oxygen and positioning must follow trained local practice.",
    "Urdu": "ہنگامی خدمات اور ڈاکٹر سے رابطہ کریں۔ اپنے مقامی ہنگامی طریقۂ کار پر عمل کریں۔ مریض کو گرم رکھیں؛ بے ہوشی کی حالت میں منہ سے کچھ نہ دیں۔ آکسیجن اور مریض کو مناسب حالت میں رکھنے کے لیے اپنی تربیت اور مقامی طریقۂ کار پر عمل کریں۔",
}


def prompt_clip(text: str, language: str) -> dict | None:
    """Read available exact-text audio only; never invoke a live provider."""
    from .speech_audio import recorded_prompt, synthesize, text_identity
    from .speech_audio import ROOT as speech_root
    from .ai import _cache_read
    from .privacy import read_media
    path = recorded_prompt(text, language) or synthesize(text, language)[0]
    if path is None:
        return None
    try:
        content = read_media(path)
        suffix = Path(path.name.removesuffix(".enc")).suffix.lower()
        mime = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".webm": "audio/webm"}[suffix]
        origin = "Generated provider cache; pronunciation and clinical review pending"
        try:
            manifest = json.loads(_cache_read(speech_root / "data/consultation_prompt_recordings" / (text_identity(text, language) + ".json")))
            if manifest["text_hash"] == text_identity(text, language) and Path(manifest["path"]).resolve() == path.resolve():
                origin = manifest.get("origin", "Supplied recording; independent review pending")
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return {"content": content, "suffix": suffix, "mime": mime, "text_hash": text_identity(text, language),
                "audio_hash": hashlib.sha256(content).hexdigest(), "origin": origin, "review_status": "pending"}
    except (OSError, ValueError, KeyError, InvalidToken):
        return None


def inline_prompt(text: str, language: str) -> str | None:
    clip = prompt_clip(text, language)
    return "data:" + clip["mime"] + ";base64," + base64.b64encode(clip["content"]).decode() if clip else None


def screen_content() -> dict:
    groups = {}
    for group, questions in config()["danger_signs"].items():
        groups[group] = [{**q, "collection_guidance": guide(q["key"])[1:],
                          "diagram_sha256": hashlib.sha256(illustration(q["key"], q["en"]).encode()).hexdigest()} for q in questions]
    return {"groups": groups, "behavior": "Any confirmed Yes triggers RED immediately; Unknown is not a negative. No full history is required before emergency referral.",
            "clinical_review": "pending"}


def content_hash() -> str:
    return hashlib.sha256(json.dumps({"screen": screen_content(), "emergency_wording": EMERGENCY_WORDING,
        "audio": audio_manifest()[0]}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def audio_manifest() -> tuple[list[dict], dict[str, bytes]]:
    from .speech_audio import text_identity
    rows, files, seen = [], {}, set()
    for group in config()["danger_signs"].values():
        for question in group:
            for language, text in (("English", question["en"]), ("Urdu", question["ur"])):
                identity = text_identity(text, language)
                if identity in seen:
                    continue
                seen.add(identity)
                clip = prompt_clip(text, language)
                row = {"language": language, "text": text, "text_hash": identity, "available": clip is not None, "review_status": "pending"}
                if clip:
                    name = "audio/" + language + "/" + identity + clip["suffix"]
                    files[name] = clip["content"]
                    row.update({"file": name, "audio_hash": clip["audio_hash"], "origin": clip["origin"]})
                rows.append(row)
    return rows, files


def review_archive() -> bytes:
    import io
    import zipfile
    from .content_review import ReviewReport
    manifest, files = audio_manifest()
    material = {"danger_screen": screen_content(), "emergency_response": EMERGENCY_WORDING, "danger_audio": manifest}
    targets = [{"scope": scope, "content_hash": hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), "content": value} for scope, value in material.items()]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("review.json", json.dumps({"format": "smartsehat-danger-review-1", "targets": targets, "clinical_approval": "pending"}, ensure_ascii=False, indent=2))
        archive.writestr("report.schema.json", json.dumps(ReviewReport.model_json_schema(), ensure_ascii=False, indent=2))
        archive.writestr("README.txt", "A real physician must review danger-screen wording, grouping, observation guidance, diagrams and emergency wording against the applicable local protocol. A native speaker must listen to every exact recording. Submit actual scope/hash/reviewer/role/date/decision/notes JSON. No review or identity verification is implied by creating this package. Browser timing and automated clicks are not independent field usability evidence.")
        for group in config()["danger_signs"].values():
            for q in group:
                archive.writestr("diagrams/" + q["id"] + ".svg", illustration(q["key"], q["en"]))
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()
