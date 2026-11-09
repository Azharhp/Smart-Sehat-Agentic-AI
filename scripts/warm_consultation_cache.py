"""Seed authored synthetic fixtures without provider calls or patient mutations."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from smartsehat_workspace.consultation.clinical import ROOT
from smartsehat_workspace.consultation import ai
from smartsehat_workspace.consultation.questions import QUESTIONS


def warm(cache_root: Path | None = None, *, manifest: dict | None = None, include_image: bool = True,
         overwrite: bool = False) -> list[Path]:
    samples = [
        ("khansi aur bukhar, no vomiting", [
            {"key": "cough", "value": True, "confidence": .7, "quote": "khansi"},
            {"key": "fever", "value": True, "confidence": .7, "quote": "bukhar"},
            {"key": "vomiting", "value": False, "confidence": .7, "quote": "no vomiting"}]),
        ("Cough for 4 days. Fever for 2 days. No vomiting.", [
            {"key": "cough", "value": True, "confidence": .7, "quote": "Cough for 4 days"},
            {"key": "cough_days", "value": 4, "confidence": .7, "quote": "Cough for 4 days"},
            {"key": "fever", "value": True, "confidence": .7, "quote": "Fever for 2 days"},
            {"key": "fever_days", "value": 2, "confidence": .7, "quote": "Fever for 2 days"},
            {"key": "vomiting", "value": False, "confidence": .7, "quote": "No vomiting"}]),
    ]
    prepared = []
    for transcript, facts in samples:
        for pathway in (None, "young_infant", "child", "older_child", "adult", "older_adult"):
            payload = {"transcript": transcript}
            if pathway:
                payload["age_pathway"] = pathway
            prepared.append(("extract", payload, ai.Extraction, {"facts": facts}))
    for question in QUESTIONS:
        payload = {key: question[key] for key in ("id", "asks_key", "text_ur")}
        prepared.append(("question-reword", payload, ai.RewordedQuestion, payload))
    if manifest is not None:
        from smartsehat_workspace.consultation.patient import Wording
        from smartsehat_workspace.consultation.reasoning import Selections
        schemas = {"extract": ai.Extraction, "question-reword": ai.RewordedQuestion,
                   "doctor-reword": Wording, "clinical-considerations-v2": Selections}
        if not isinstance(manifest, dict) or manifest.get("synthetic") is not True or not isinstance(manifest.get("entries"), list):
            raise ValueError("A fixture manifest must explicitly declare synthetic=true and entries.")
        for item in manifest["entries"]:
            if not isinstance(item, dict) or item.get("task") not in schemas or "input" not in item or "output" not in item:
                raise ValueError("Each fixture needs a supported task, exact input and schema-valid output.")
            schema = schemas[item["task"]]
            prepared.append((item["task"], item["input"], schema, schema.model_validate(item["output"]).model_dump()))
    # Validate the complete set before writing any fixture.
    for task, payload, schema, output in prepared:
        schema.model_validate(output)
        ai.json_cache_path(task, payload, schema, cache_root)
    paths = [ai.seed_json_cache(task, payload, schema, output, cache_root, overwrite) for task, payload, schema, output in prepared]
    image = ROOT / "assets/rx/demo_rx.jpg"
    if include_image and image.exists():
        path = ai.medicine_cache_path(image, cache_root)
        # Manually verified fictional image names; no intake/dose inferred.
        ai.seed_cache_file(path, ai.MedicineImage, {"names": ["paracetamol", "ibuprofen", "nimesulide", "amoxicillin", "omeprazole", "multivitamin"]}, overwrite)
        paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="Additional exact-input fixtures declared synthetic.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing validated cached results explicitly.")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
    paths = warm(manifest=manifest, overwrite=args.overwrite)
    print(f"Prepared {len(paths)} encrypted authored synthetic fixtures. Existing valid responses were preserved unless --overwrite was selected. No provider was called or patient case changed.")


if __name__ == "__main__":
    main()
