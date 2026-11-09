"""Score an independently prepared speech dataset without making provider calls.

Usage: python -m scripts.consultation_speech_evaluate --template path.json
       python -m scripts.consultation_speech_evaluate dataset.json --output results.json
Use --record only to save the reported data to this installation's encrypted DB.
"""
import argparse
import json
from pathlib import Path

from smartsehat_workspace.consultation.speech_evaluation import evaluate_dataset, evaluation_template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, nargs="?")
    parser.add_argument("--template", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    if args.template:
        args.template.write_text(json.dumps(evaluation_template(), ensure_ascii=False, indent=2), encoding="utf-8")
        print("Empty evaluation template written; no measurements or language validation recorded.")
        return
    if args.dataset is None:
        parser.error("Provide a dataset or --template path.")
    if args.dataset.stat().st_size > 8 * 1024 * 1024:
        parser.error("Dataset exceeds 8 MB.")
    data = json.loads(args.dataset.read_text(encoding="utf-8-sig"))
    result = evaluate_dataset(data)
    if args.record:
        from smartsehat_workspace.consultation.store import Store
        Store().report_speech_evaluation(data)
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # Keep transcripts and speaker identifiers out of console logs.
    print(json.dumps({"dataset_sha256": result["dataset_sha256"], "groups": result["groups"], "reported_only": True, "independent_validation": "pending"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
