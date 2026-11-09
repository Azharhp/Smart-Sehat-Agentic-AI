"""Reviewable source snapshots. No clinician or approval is invented."""
import hashlib
import json
from datetime import date
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


def _fixed_audio_files() -> tuple[list[dict], dict[str, bytes]]:
    from .clinical import config
    from .questions import QUESTIONS
    from .speech_audio import recorded_prompt, synthesize, text_identity
    from .privacy import read_media
    prompts = [(q["text_en"], q["text_ur"]) for q in QUESTIONS]
    prompts += [(q["en"], q["ur"]) for group in config()["danger_signs"].values() for q in group]
    seen, manifest, files = set(), [], {}
    for english, urdu in prompts:
        for language, text in (("English", english), ("Urdu", urdu)):
            identity = text_identity(text, language)
            if identity in seen:
                continue
            seen.add(identity)
            path = recorded_prompt(text, language)
            if path is None:
                path, _ = synthesize(text, language)
            row = {"language": language, "text": text, "text_hash": identity, "available": False, "review_status": "pending"}
            if path is not None:
                try:
                    content = read_media(path)
                    suffix = path.name.removesuffix(".enc").rsplit(".", 1)[-1]
                    filename = f"audio/{language}/{identity}.{suffix}"
                    row.update({"available": True, "file": filename, "audio_hash": hashlib.sha256(content).hexdigest()})
                    files[filename] = content
                except (OSError, ValueError):
                    pass
            manifest.append(row)
    return manifest, files


def review_pack(include_audio: bool = False) -> dict:
    from .clinical import config
    from .questions import QUESTIONS
    from .reasoning import catalogue
    from .medicines import resources
    from .epidemiology import catalogue_snapshot
    from .guidance import GUIDES, PHOTO_GUIDES
    from .danger_content import screen_content, EMERGENCY_WORDING, audio_manifest
    sources = {"rules": config(), "questions": QUESTIONS, "considerations": catalogue(),
               "medicines": resources(), "regional_context": catalogue_snapshot()[0], "observation_wording": GUIDES, "photo_wording": PHOTO_GUIDES,
               "danger_screen": screen_content(), "emergency_response": EMERGENCY_WORDING}
    if include_audio:
        sources["fixed_audio"] = _fixed_audio_files()[0]
        sources["danger_audio"] = audio_manifest()[0]
    targets = [{"scope": scope, "content_hash": hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest(), "content": data} for scope, data in sources.items()]
    return {"format": "smartsehat-content-review-1", "clinical_approval": "pending", "targets": targets,
            "report_fields": ["scope", "content_hash", "reviewer", "role", "reviewed_on", "decision", "notes"],
            "instructions": "A real physician/pharmacist/native-speaker must review the relevant exact content. Uploading a report records a reported review, not verified identity, medical validation or automatic activation of dosing/epidemiology entries."}


def review_archive() -> bytes:
    import io
    import zipfile
    from .clinical import config
    from .illustrations import illustration
    manifest, files = _fixed_audio_files()
    pack = review_pack()
    pack["targets"].append({"scope": "fixed_audio", "content_hash": hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest(), "content": manifest})
    from .danger_content import audio_manifest
    danger_audio = audio_manifest()[0]
    pack["targets"].append({"scope": "danger_audio", "content_hash": hashlib.sha256(json.dumps(danger_audio, ensure_ascii=False, sort_keys=True).encode()).hexdigest(), "content": danger_audio})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("review.json", json.dumps(pack, ensure_ascii=False, indent=2))
        for filename, content in files.items():
            archive.writestr(filename, content)
        for group in config()["danger_signs"].values():
            for question in group:
                archive.writestr("diagrams/" + question["id"] + ".svg", illustration(question["key"], question["en"]))
    return output.getvalue()


class ReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    scope: str
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=2, max_length=200)
    role: Literal["physician", "pharmacist", "native-speaker"]
    reviewed_on: date
    decision: Literal["approved", "changes requested"]
    notes: str = Field(min_length=1, max_length=5000)


class ContentReviewWorkflow:
    def content_reviews(self) -> list[dict]:
        with self.connect() as db:
            return [self._decode(row["data"]) for row in db.execute("SELECT data FROM consultation_content_reviews ORDER BY seq")]

    def report_content_review(self, report: dict) -> None:
        from .timeutils import local_today
        parsed = ReviewReport.model_validate(report)
        if parsed.reviewed_on > local_today():
            raise ValueError("A review cannot be dated in the future.")
        target = next((item for item in review_pack(include_audio=parsed.scope in ("fixed_audio", "danger_audio"))["targets"] if item["scope"] == parsed.scope), None)
        if not target or target["content_hash"] != parsed.content_hash:
            raise ValueError("Review scope/hash differs from the current content. Obtain review of this version.")
        roles = {"rules": {"physician"}, "questions": {"physician", "native-speaker"}, "considerations": {"physician"},
                 "medicines": {"physician", "pharmacist"}, "regional_context": {"physician"},
                 "observation_wording": {"physician", "native-speaker"}, "photo_wording": {"physician", "native-speaker"}}
        roles["fixed_audio"] = {"native-speaker", "physician"}
        roles.update({"danger_screen": {"physician"}, "emergency_response": {"physician"}, "danger_audio": {"native-speaker", "physician"}})
        if parsed.role not in roles[parsed.scope]:
            raise ValueError("This review scope requires the relevant clinical/pharmacy/language role.")
        data = {**parsed.model_dump(mode="json"), "reported_only": True, "reviewer_identity_verified": False}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = [self._decode(row["data"]) for row in db.execute("SELECT data FROM consultation_content_reviews")]
            if data not in previous:
                db.execute("INSERT INTO consultation_content_reviews(data) VALUES(?)", (self._encode(data),))
