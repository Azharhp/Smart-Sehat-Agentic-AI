"""Audit prepared danger audio and real stored review/observation reports.

This never invents a reviewer, runs a field trial, or asserts clinical approval.
"""
import argparse
import io
import json
from pathlib import Path
import wave

from smartsehat_workspace.consultation.clinical import ROOT
from smartsehat_workspace.consultation.danger_content import audio_manifest, content_hash, review_archive
from smartsehat_workspace.consultation.content_review import review_pack
from smartsehat_workspace.consultation.store import Store


def audit(service: Store) -> dict:
    rows, files = audio_manifest()
    audio = {language: {"prepared": sum(row["language"] == language and row["available"] for row in rows),
                       "total_unique": sum(row["language"] == language for row in rows)} for language in ("English", "Urdu")}
    invalid = []
    for row in rows:
        if not row["available"]:
            invalid.append(row["text_hash"])
            continue
        # Existing fixed voices are WAVs. Other human audio formats are handled
        # by the app, but require an external listening/format review here.
        if row["file"].endswith(".wav"):
            try:
                with wave.open(io.BytesIO(files[row["file"]]), "rb") as clip:
                    if not clip.getnframes() or clip.getnchannels() != 1:
                        invalid.append(row["text_hash"])
            except (wave.Error, EOFError):
                invalid.append(row["text_hash"])
    scopes = {target["scope"]: target["content_hash"] for target in review_pack(include_audio=True)["targets"]
              if target["scope"] in ("danger_screen", "emergency_response", "danger_audio")}
    reports = [row for row in service.content_reviews() if scopes.get(row["scope"]) == row["content_hash"]]
    current_hash = content_hash()
    observations = []
    with service.connect() as db:
        for row in db.execute("SELECT data FROM consultation_events WHERE type='DANGER_OBSERVATION_REPORTED'"):
            data = service._decode(row["data"])
            if data.get("content_hash") == current_hash:
                observations.append(data)
    return {"format": "smartsehat-danger-readiness-1", "content_hash": current_hash,
            "audio": audio, "unavailable_or_invalid_audio": invalid, "prepared_audio_complete": not invalid,
            "clinical_and_pronunciation_review": {"matching_reported_reviews": len(reports), "reviewer_identity_verified": False,
                "status": "Reported reviews require external verification" if reports else "Pending real physician/native-speaker review"},
            "usability": {"matching_reported_observations": len(observations),
                "reported_under_30_seconds": sum(row.get("reported_under_30_seconds", False) for row in observations),
                "independent_validation_established": False,
                "status": "Reported observations require external verification" if observations else "Pending actual separately observed target-device trial"},
            "overall_status": "Prepared software/audio; external clinical and usability validation pending"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-package", action="store_true", help="Write the public fixed-content review ZIP and status JSON under docs/.")
    args = parser.parse_args()
    result = audit(Store())
    if args.write_package:
        (ROOT / "docs/danger-screen-review.zip").write_bytes(review_archive())
        (ROOT / "docs/danger-screen-status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
