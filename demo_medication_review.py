"""Reproduce synthetic evidence receipts offline; never calls a model or database."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from smartsehat.medication_review import RULESET_VERSION, review_medicines

PATIENT = {"age": 30, "sex": "F", "weight_kg": 60, "allergies": [],
           "current_meds": [], "pregnant": False, "renal_impairment": False,
           "liver_disease": False, "context_confirmed": True}
PARACETAMOL = {"name": "paracetamol", "strength": "500 mg", "dose_mg": 500,
               "frequency_per_day": 3, "duration_days": 2, "route": "oral",
               "indication": "Synthetic demonstration only"}


def generate() -> dict:
    scenarios = [
        ("ordinary_adult_ceiling", [PARACETAMOL], PATIENT, "NO_FLAGS_IN_SUPPORTED_CHECKS", None),
        ("above_adult_ceiling", [{**PARACETAMOL, "dose_mg": 1500}], PATIENT,
         "HOLD", "paracetamol_adult_limit"),
        ("duplicate_current_brand", [PARACETAMOL], {**PATIENT, "current_meds": ["Panadol"]},
         "HOLD", "duplicate_ingredient"),
        ("beta_lactam_allergy", [{"name": "amoxicillin", "route": "oral"}],
         {**PATIENT, "allergies": [{"substance": "penicillin", "reaction": "anaphylaxis"}]},
         "HOLD", "allergy_match_review"),
        ("pregnancy_30_weeks", [{"name": "ibuprofen", "route": "oral"}],
         {**PATIENT, "pregnant": True, "gestational_weeks": 30}, "HOLD", "pregnancy_nsaid_30_weeks"),
        ("rifampicin_contraception", [{"name": "rifampicin"}],
         {**PATIENT, "current_meds": ["oral contraceptive pill"]},
         "REVIEW_REQUIRED", "ddi_rifampicin_contraceptive"),
        ("uncertain_ocr", [{"name": "Panadol?"}], PATIENT, "HOLD", "unrecognized_medicine"),
        ("unknown_history", [PARACETAMOL], {}, "REVIEW_REQUIRED", "context_not_assessed"),
    ]
    results = []
    for name, medicines, patient, expected_status, expected_flag in scenarios:
        report = review_medicines(deepcopy(medicines), deepcopy(patient))
        observed_flags = {flag["code"] for flag in report["flags"]}
        passed = report["status"] == expected_status and (expected_flag is None or expected_flag in observed_flags)
        results.append({"scenario": name, "synthetic": True, "medicines": medicines,
                        "patient_context": patient, "expected_status": expected_status,
                        "expected_flag": expected_flag, "passed": passed, "review": report})
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "ruleset_version": RULESET_VERSION,
            "claim": "Synthetic software examples; independent clinical validation is pending.",
            "passed": sum(row["passed"] for row in results), "total": len(results), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="audit-output/medication-demo-results.json")
    args = parser.parse_args()
    result = generate()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in result["results"]:
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['scenario']}: {row['review']['status']}")
    print(f"{result['passed']}/{result['total']} examples match expectations; rules {RULESET_VERSION}")
    print(path.resolve())
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
