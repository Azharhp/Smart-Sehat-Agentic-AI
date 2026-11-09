"""Export the seeded question catalogue into editable, reviewable YAML."""
from smartsehat_workspace.consultation.clinical import ROOT
from smartsehat_workspace.consultation.questions import QUESTIONS
import yaml


def main():
    path = ROOT / "config/question_bank.yaml"
    if path.exists():
        print("Question bank already exists; preserve clinician/editor changes.")
        return
    path.write_text(yaml.safe_dump({"review_status": "pending", "version": "consultation-1", "questions": QUESTIONS}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"Exported {len(QUESTIONS)} bilingual questions.")


if __name__ == "__main__":
    main()
