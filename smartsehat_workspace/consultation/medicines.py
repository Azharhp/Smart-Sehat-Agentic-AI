"""Exact brand reconciliation; unknown strengths and doses remain unknown."""
from __future__ import annotations
from collections import defaultdict
from functools import lru_cache
import json
from .clinical import ROOT


@lru_cache(maxsize=1)
def resources() -> dict:
    return json.loads((ROOT / "config/consultation_resources.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def catalogue() -> dict[str, dict]:
    result = {}
    entries = json.loads((ROOT / "data/brand_generic_map.json").read_text(encoding="utf-8"))
    for entry in entries:
        for brand in entry.get("brands", []) + [entry["generic"]]:
            result[brand.casefold().strip()] = {"brand": brand, "generic": entry["generic"].split("+"), "class": entry["drug_class"]}
    for entry in resources()["extra_brands"]:
        result[entry["brand"].casefold()] = entry
    return result


def reconcile(names: list[str], facts: list[dict], pregnant: bool = False, age=None) -> dict:
    meds = [dict(catalogue().get(name.casefold().strip(), {"brand": name, "generic": [], "class": "unknown"})) for name in names if name.strip()]
    v = {f["key"]: f["value"] for f in facts if f.get("confirmed")}
    flags, ingredients = [], defaultdict(list)
    for med in meds:
        for ingredient in med["generic"]:
            ingredients[ingredient.strip()].append(med["brand"])
        if not med["generic"]:
            flags.append({"rule_id": "MED-UNKNOWN", "text": f"Unresolved medicine: {med['brand']}. Confirm with a pharmacist."})
        if any(term in med["class"].lower() for term in ("antibiotic", "macrolide", "tetracycline", "antimycobacterial", "steroid")):
            flags.append({"rule_id": "MED-HISTORY", "text": f"Antibiotic or steroid already taken: {med['brand']}. Inform doctor."})
    for ingredient, brands in ingredients.items():
        if len(brands) > 1:
            flags.append({"rule_id": "MED-DUP", "text": f"Duplicate {ingredient}: {', '.join(brands)}."})
    rules = resources()["medication_rules"]
    if v.get("allergy_penicillin") is True and any(i in ingredients for i in rules["penicillin_generics"]):
        flags.append({"rule_id": "MED-ALLERGY", "text": "Confirmed penicillin allergy conflicts with a recorded medicine. Doctor review required."})
    for allergy in v.get("allergy_records", []) or []:
        substance = allergy["substance"].casefold().strip()
        resolved_allergen = catalogue().get(substance)
        allergens = set(resolved_allergen["generic"]) if resolved_allergen else {substance}
        if allergens & set(ingredients):
            flags.append({"rule_id": "MED-ALLERGY", "text": f"Recorded allergy to {allergy['substance']} conflicts with a medicine ingredient. Reaction: {allergy.get('reaction', 'unknown')}. Doctor review required."})
    for interaction in rules["interactions"]:
        if all(i in ingredients for i in interaction["ingredients"]):
            flags.append({"rule_id": "MED-DDI", "text": interaction["message"]})
    if meds and (pregnant or v.get("breastfeeding") is True):
        flags.append({"rule_id": "MED-PREG", "text": "Pregnancy/breastfeeding: medicine review required by doctor/pharmacist."})
    for record in v.get("medicine_records", []) or []:
        missing = [key for key in ("strength", "amount", "duration", "adviser") if str(record.get(key, "unknown")).strip().casefold() in ("", "unknown")]
        if missing:
            flags.append({"rule_id": "MED-INCOMPLETE", "text": f"{record['name']}: unknown {', '.join(missing)}. Ask patient or inform doctor."})
    if meds and (v.get("allergy_penicillin") is None or str(v.get("allergies", "unknown")).strip().casefold() in ("unknown", "")):
        flags.append({"rule_id": "MED-ALLERGY-UNKNOWN", "text": "Allergy history is incomplete; no medicine should be assumed allergy-safe."})
    if meds:
        flags.append({"rule_id": "MED-DOSE-UNREVIEWED", "text": "Dose safety is not validated: formulation, strength and a clinician-reviewed dosing table are required."})
    dose_checks = []
    if age is not None:
        from .reported_doses import ReportedDose, record_hash
        from .dosing import check_amount
        from .clinical import validate_measurement
        current = {record_hash(row): row for row in v.get("medicine_records") or []}
        weight = v.get("weight")
        if weight is not None and not validate_measurement("weight", weight, age)["ok"]:
            weight = None
        for raw in v.get("reported_doses") or []:
            entry = ReportedDose.model_validate(raw)
            if entry.record_hash not in current:
                flags.append({"rule_id": "MED-DOSE-STALE", "text": "Reported amount no longer matches a current medicine row. Reconfirm it."})
                continue
            resolved = catalogue().get(entry.medicine.casefold().strip())
            checked = check_amount(resources()["dose_review"], resolved["generic"] if resolved else [], entry.dose_mg, weight, age, entry.product.model_dump(), entry.regimen.model_dump())
            dose_checks.append({"medicine": entry.medicine, "record_hash": entry.record_hash, "unknown_reason": entry.unknown_reason, **checked})
            flags += [{"rule_id": "HISTORY-" + flag["rule_id"], "text": entry.medicine + " reported intake: " + flag["text"]} for flag in checked["flags"]]
    return {"medicines": meds, "flags": flags, "reported_dose_checks": dose_checks, "review_status": "pending"}


def check_prescription(medicine: str, dose_mg: float | None, facts: list[dict], age, pregnant: bool, product: dict | None = None, regimen: dict | None = None) -> dict:
    """Check a doctor-authored choice. Never supplies a patient dose."""
    if not medicine.strip():
        return {"status": "NO_PRESCRIPTION", "flags": []}
    values = {f["key"]: f["value"] for f in facts if f.get("confirmed")}
    names = list(values.get("medicines") or []) + [medicine]
    checked = reconcile(names, facts, pregnant, age)
    flags = list(checked["flags"])
    resolved = catalogue().get(medicine.casefold().strip())
    if not resolved or not any(g in resources()["stock"] for g in resolved["generic"]):
        flags.append({"rule_id": "RX-STOCK", "text": "Selected medicine is absent from the synthetic stock list, or unresolved."})
    weight = values.get("weight")
    if weight is not None:
        from .clinical import validate_measurement
        if not validate_measurement("weight", weight, age)["ok"]:
            weight = None
    if age.pathway in ("young_infant", "child", "older_child") and not weight:
        flags.append({"rule_id": "RX-WEIGHT", "text": "No confirmed child weight: weight-based plausibility cannot be checked."})
    from .dosing import check_amount
    dose_check = check_amount(resources()["dose_review"], resolved["generic"] if resolved else [], dose_mg, weight, age, product, regimen)
    flags.extend(dose_check["flags"])
    alternatives = []
    if resolved:
        alternatives = [row["brand"] for row in catalogue().values() if row["brand"].casefold() != medicine.casefold().strip()
                        and set(row["generic"]) == set(resolved["generic"]) and any(g in resources()["stock"] for g in row["generic"])]
    return {"status": "REVIEW_REQUIRED" if flags else "NO_FLAGS_FOUND", "flags": flags, "dose_check": dose_check,
            "same_ingredient_stock_options": sorted(set(alternatives))[:10], "stock_source": "synthetic inventory; formulation equivalence requires doctor approval"}


def check_plan(medicine: str, dose_mg: float | None, schedule: list[dict], prescription: str, facts: list[dict], age, pregnant: bool, product: dict | None = None, regimen: dict | None = None) -> dict:
    """Check all doctor-entered medicines together, without inferring doses."""
    result = check_prescription(medicine, dose_mg, facts, age, pregnant, product, regimen)
    names = [row["medicine"].strip() for row in schedule if row.get("medicine", "").strip()]
    for name in dict.fromkeys(names):
        if name.casefold() != medicine.casefold().strip():
            result["flags"].extend(check_prescription(name, None, facts, age, pregnant)["flags"])
    if names:
        current = next((f["value"] for f in facts if f.get("confirmed") and f["key"] == "medicines"), []) or []
        all_names = list(dict.fromkeys([*current, *names, *([medicine] if medicine.strip() else [])]))
        result["flags"].extend(reconcile(all_names, facts, pregnant)["flags"])
        result["flags"].append({"rule_id": "RX-SCHEDULE-DOSE", "text": "Doctor must verify every schedule amount, formulation and timing. Free-text schedule doses cannot be approved automatically."})
    if prescription.strip() and not medicine.strip():
        result["flags"].append({"rule_id": "RX-UNSTRUCTURED", "text": "Free-text prescription cannot be automatically checked. Doctor must independently verify every medicine and amount."})
    result["flags"] = list({(flag["rule_id"], flag["text"]): flag for flag in result["flags"]}.values())
    if medicine.strip() or names or prescription.strip():
        result["status"] = "REVIEW_REQUIRED" if result["flags"] else "NO_FLAGS_FOUND"
    return result
