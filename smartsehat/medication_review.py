"""Finite, source-backed medication review shared by both clinical workflows.

This is deterministic decision support, not a prescribing engine or clinical
validation. Missing information remains unknown. Product identity is matched
exactly; label strength never becomes an administered dose. See the evidence
register for clinical sources, application policy, and validation still needed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any


RULESET_VERSION = "2026-09-06.1"
NO_MEDICINES = "No medicines proposed"
SOURCES = {
    "NHS_PARACETAMOL_ADULT": {
        "title": "NHS: Paracetamol for adults",
        "url": "https://www.nhs.uk/medicines/paracetamol-for-adults/",
        "section": "How to take paracetamol and how much to take (dose); Who can and cannot take paracetamol",
    },
    "FDA_ACETAMINOPHEN_DUPLICATES": {
        "title": "FDA: Acetaminophen",
        "url": "https://www.fda.gov/drugs/safe-use-over-counter-pain-relievers-and-fever-reducers/acetaminophen",
        "section": "Safe Use of Acetaminophen",
    },
    "FDA_NSAID_PREGNANCY": {
        "title": "FDA: NSAIDs at 20 weeks or later in pregnancy",
        "url": "https://www.fda.gov/drugs/drug-safety-and-availability/fda-recommends-avoiding-use-nsaids-pregnancy-20-weeks-or-later-because-they-can-result-low-amniotic",
        "section": "Additional Information for Health Care Professionals; 81 mg aspirin and ophthalmic exceptions",
    },
    "DAILYMED_AMOXICILLIN_ALLERGY": {
        "title": "DailyMed: Amoxicillin capsule, Rising Health",
        "url": "https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=d6a33b28-f520-4a4c-b553-0ebd00558345",
        "section": "4 Contraindications; 5.1 Anaphylactic Reactions",
    },
    "DAILYMED_RIFAMPIN_CONTRACEPTIVES": {
        "title": "DailyMed: Rifampin capsule, Lupin, version 26",
        "url": "https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=b56004f0-f23d-47f6-80c7-39884fccbccc&version=26",
        "section": "Precautions / Drug Interactions / Table 1: Systemic Hormonal Contraceptives; Information for Patients",
    },
    "DAILYMED_WARFARIN_BLEEDING": {
        "title": "DailyMed: Warfarin sodium, Amneal, version 7",
        "url": "https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=51e98fb6-ba76-497e-95d8-fe895ef0b7ed&version=7",
        "section": "7.3 Drugs that Increase Bleeding Risk, Table 3",
    },
    "DAILYMED_TMP_SMX_ACEI": {
        "title": "DailyMed: Bactrim / Bactrim DS, Sun Pharmaceutical",
        "url": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=f59d0c04-9c66-4d53-a0e1-cb55570deb62",
        "section": "Precautions / Drug Interactions: Angiotensin Converting Enzyme Inhibitors; Hyperkalemia",
    },
    "WHO_AWARE": {
        "title": "WHO: The AWaRe antibiotic book (2022), with subsequent guideline updates",
        "url": "https://www.who.int/publications/i/item/9789240062382",
        "section": "Overview; Improving the use of antibiotics with the AWaRe book; subsequent guidance listed on the publication page",
    },
    "NIH_PK_TYPHOID_2024": {
        "title": "NIH Pakistan: Typhoid advisory, 22 May 2024",
        "url": "https://www.nih.org.pk/wp-content/uploads/2024/06/Weekly%20Report-22-2024.pdf#page=19",
        "section": "PDF page 19 of 20, F.1-22/Advisory/CDC/2024, Treatment",
    },
    "NICE_MEDICINES_RECONCILIATION": {
        "title": "NICE NG5: Medicines optimisation",
        "url": "https://www.nice.org.uk/guidance/ng5/chapter/Recommendations",
        "section": "1.2.3 information about medicines; 1.3 Medicines reconciliation; 1.4 Medication review",
    },
    "SMARTSEHAT_RULE_SCOPE": {
        "title": "SmartSehat medication review scope and application policy",
        "url": "docs/MEDICATION_EVIDENCE.md",
        "section": "Rule scope and decision semantics; implementation scope",
    },
}

# Deliberately small demonstration vocabulary. Local brand formulations still
# require label confirmation. Never import the legacy pregnancy-category map or
# match a product by a substring. Rimactazid is intentionally not mapped to a
# single ingredient. Adding a product requires explicit ingredient reconciliation.
_PRODUCTS = [
    ("paracetamol", "analgesic-antipyretic", ["acetaminophen", "Panadol", "Calpol"]),
    ("paracetamol+caffeine", "analgesic-antipyretic", ["Panadol Extra", "acetaminophen+caffeine"]),
    ("ibuprofen", "NSAID", ["Brufen"]),
    ("aspirin", "NSAID-antiplatelet", ["Disprin", "acetylsalicylic acid"]),
    ("naproxen", "NSAID", []),
    ("diclofenac", "NSAID", []),
    ("celecoxib", "NSAID", []),
    ("nimesulide", "NSAID", ["Nise", "Nimulid"]),
    ("amoxicillin", "antibiotic", ["Amoxil"]),
    ("amoxicillin+clavulanic acid", "antibiotic", ["amoxicillin-clavulanate", "amoxicillin+clavulanate", "co-amoxiclav", "Augmentin"]),
    ("metronidazole", "antibiotic-antiprotozoal", ["Flagyl"]),
    ("azithromycin", "antibiotic-macrolide", ["Zithromax", "Azomax"]),
    ("doxycycline", "antibiotic-tetracycline", ["Vibramycin", "Doxy-1"]),
    ("ciprofloxacin", "antibiotic-fluoroquinolone", ["Ciproxin", "Ciprofox"]),
    ("sulfamethoxazole+trimethoprim", "antibiotic", ["trimethoprim+sulfamethoxazole", "co-trimoxazole", "TMP-SMX", "Bactrim", "Bactrim DS", "Septran", "Septrin"]),
    ("rifampicin", "antibiotic-antimycobacterial", ["rifampin", "Rifadin"]),
    ("warfarin", "anticoagulant", []),
    ("lisinopril", "ACE inhibitor", []),
    ("ramipril", "ACE inhibitor", []),
    ("enalapril", "ACE inhibitor", []),
    ("captopril", "ACE inhibitor", []),
    ("perindopril", "ACE inhibitor", []),
    ("systemic hormonal contraceptive", "systemic hormonal contraceptive", ["oral contraceptive", "oral contraceptive pill", "combined oral contraceptive", "combined pill", "progestogen-only pill", "progestin-only pill"]),
    ("ethinylestradiol+levonorgestrel", "systemic hormonal contraceptive", ["ethinyl estradiol+levonorgestrel", "levonorgestrel+ethinylestradiol"]),
    ("omeprazole", "PPI", ["Risek"]),
    ("metformin", "antidiabetic", ["Glucophage"]),
    ("artemether+lumefantrine", "antimalarial", ["Coartem"]),
]
_NUMBER_FIELDS = ("dose_mg", "frequency_per_day", "duration_days")
_INPUT_FIELDS = ("id", "name", "strength", *_NUMBER_FIELDS, "route", "indication", "_input_errors")


def _norm(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s*([+-])\s*", r"\1", " ".join(value.casefold().split()))


def resolve(name: str) -> dict:
    """Resolve an exact supported name; uncertainty and dose suffixes stay unknown."""
    query = _norm(name)
    if query and "?" not in query:
        for generic, drug_class, aliases in _PRODUCTS:
            for alias in [generic, *aliases]:
                if query == _norm(alias):
                    return {"generic": generic, "ingredients": generic.split("+"),
                            "drug_class": drug_class, "matched_brand": alias}
    return {"generic": None, "ingredients": [], "drug_class": "unrecognized", "matched_brand": None}


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _number(value: Any, *, zero: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or result < 0 or (not zero and result == 0):
        return None
    return result


def normalize_medicines(value: Any, prefix: str = "m") -> tuple[list[dict], list[str]]:
    """Normalize explicit records without dose inference or dropping invalid rows.

    Errors are retained on rows so a second pass cannot erase their provenance.
    An explicitly supplied [] is different from a missing/malformed medicine list.
    """
    if not isinstance(value, list):
        return [], ["Medicine list must be an explicit list (use [] only when no medicines are proposed)."]
    rows, errors, used = [], [], set()
    for index, item in enumerate(value, 1):
        record = {"name": item} if isinstance(item, str) else item
        row_errors: list[str] = []
        if not isinstance(record, dict):
            record = {}
            row_errors.append("Medicine entry must be a name or structured object.")
        prior = record.get("_input_errors")
        if isinstance(prior, list):
            row_errors.extend(str(error) for error in prior if error)
        identifier = record.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", identifier) or identifier in used:
            identifier = f"{prefix}{index}"
            while identifier in used:
                identifier += "_"
        used.add(identifier)
        row = {"id": identifier, "name": _text(record.get("name")) or ""}
        if not row["name"]:
            row_errors.append("Medicine name is missing or malformed.")
        for field in ("strength", "route", "indication"):
            raw = record.get(field)
            row[field] = _text(raw)
            if raw is not None and not isinstance(raw, str):
                row_errors.append(f"{field} must be text or unknown.")
        for field in _NUMBER_FIELDS:
            raw = record.get(field)
            row[field] = _number(raw)
            if raw is not None and raw != "" and row[field] is None:
                row_errors.append(f"{field} must be a finite positive number or unknown.")
        identity = resolve(row["name"])
        row.update(generic=identity["generic"], ingredients=identity["ingredients"],
                   **{"class": identity["drug_class"]}, matched_brand=identity["matched_brand"], flags=[])
        if row_errors:
            row["_input_errors"] = list(dict.fromkeys(row_errors))
            errors.extend(f"{identifier}: {error}" for error in row["_input_errors"])
        rows.append(row)
    return rows, errors


def draft_to_medicines(text: Any) -> list[dict]:
    """Keep legacy free text unresolved; a human must structure regimen details."""
    if isinstance(text, str) and text.strip() == NO_MEDICINES:
        return []
    if not isinstance(text, str) or not text.strip():
        return [{"name": "", "_input_errors": ["No explicit medication plan was supplied."]}]
    return [{"name": line.strip()} for line in text.splitlines() if line.strip()]


def format_medicines(medicines: list[dict]) -> str:
    if not medicines:
        return NO_MEDICINES
    lines = []
    for medicine in medicines:
        parts = [str(medicine.get("name") or "Unconfirmed medicine")]
        if medicine.get("strength"):
            parts.append(f"label strength: {medicine['strength']}")
        for field, label in (("dose_mg", "mg/dose"), ("frequency_per_day", "administrations/day"), ("duration_days", "days")):
            number = _number(medicine.get(field))
            parts.append(f"{number:g} {label}" if number is not None else f"{label}: unknown")
        for field in ("route", "indication"):
            parts.append(f"{field}: {medicine.get(field) or 'unknown'}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def review_medicines(medicines: Any, patient: Any, allowlist: list[str] | None = None) -> dict:
    """Return immutable-source findings and the exact reviewed input fingerprint.

    HOLD is an application workflow blocker requiring correction/reconciliation,
    not an instruction to discontinue existing therapy. Acknowledge/review notes
    never remove findings. None allowlist disables formulary matching; [] means
    the configured formulary contains no approved proposed medicines.
    """
    proposed, input_errors = normalize_medicines(medicines)
    context = deepcopy(patient) if isinstance(patient, dict) else {}
    if not isinstance(patient, dict):
        input_errors.append("Patient context must be a structured object.")
    current_raw = context.get("current_meds")
    current, current_errors = normalize_medicines(current_raw, "c") if current_raw is not None else ([], [])
    used = {row["id"] for row in proposed}
    for index, row in enumerate(current, 1):
        if row["id"] in used:
            row["id"] = f"c{index}"
            while row["id"] in used:
                row["id"] += "_"
        used.add(row["id"])
    all_rows = [*proposed, *current]
    all_ids = [row["id"] for row in all_rows]
    flags: list[dict] = []
    not_assessed: list[str] = []

    def add(code: str, reason: str, ids: list[str], source: str = "SMARTSEHAT_RULE_SCOPE", *, block: bool = False) -> None:
        identifiers = sorted(set(ids))
        signature = _fingerprint([code, identifiers, reason])[:20]
        if any(flag["id"] == signature for flag in flags):
            return
        evidence = {"id": source, **SOURCES[source]}
        flags.append({"id": signature, "code": code, "reason": reason,
                      "severity": "RED" if block else "YELLOW", "blocking": block,
                      "medicine_ids": identifiers, "citation": source,
                      "evidence": evidence, "ruleset_version": RULESET_VERSION})

    def unassessed(message: str, ids: list[str], code: str = "context_not_assessed", source: str = "SMARTSEHAT_RULE_SCOPE") -> None:
        if message not in not_assessed:
            not_assessed.append(message)
        add(code, message, ids, source)

    if input_errors or current_errors:
        errors = [*input_errors, *current_errors]
        add("invalid_input", " ".join(errors), all_ids, block=True)
        not_assessed.extend(errors)
    # Preserve the distinction between missing and explicitly absent history.
    if all_rows:
        if context.get("context_confirmed") is not True:
            unassessed("Patient context has not been confirmed for this encounter.", all_ids)
        for field, label in (("allergies", "Allergy history"), ("current_meds", "Current medicine list")):
            if context.get(field) is None:
                unassessed(f"{label} is unknown; reconciliation is incomplete.", all_ids, source="NICE_MEDICINES_RECONCILIATION")
        for field, label in (("pregnant", "Pregnancy status"), ("renal_impairment", "Renal impairment history"), ("liver_disease", "Liver disease history")):
            if context.get(field) is not True and context.get(field) is not False:
                unassessed(f"{label} is unknown or malformed.", all_ids)
        if _number(context.get("age"), zero=True) is None:
            unassessed("Age is unknown or malformed; age-dependent review is incomplete.", all_ids)
        if context.get("renal_impairment") is True or context.get("liver_disease") is True:
            unassessed("Organ impairment requires an individualized medicine and dose review; adjustments are outside this rule pack.", all_ids)

    # Identity and formulary are workflow policies, not pharmacological claims.
    allowed = None
    if allowlist is not None:
        if not isinstance(allowlist, list) or not all(isinstance(name, str) for name in allowlist):
            add("formulary_invalid", "Configured formulary is malformed; verify configuration.", [r["id"] for r in proposed], block=True)
            allowed = set()
        else:
            allowed = {resolve(name)["generic"] for name in allowlist} - {None}
    for row in all_rows:
        ids = [row["id"]]
        if row["generic"] is None:
            add("unrecognized_medicine", f"{row['name'] or 'Unnamed item'}: identity/ingredients are unresolved; confirm the product label and use a supported exact name.", ids, block=True)
            not_assessed.append(f"{row['name'] or row['id']}: no ingredient-specific checks possible.")
        elif row in proposed and allowed is not None and row["generic"] not in allowed:
            add("outside_formulary", f"{row['name']}: not present in the configured consultation formulary; obtain an appropriate prescribing review.", ids, block=True)

    # Ingredient overlap catches a single product and its combination product.
    by_ingredient: dict[str, list[str]] = {}
    for row in all_rows:
        for ingredient in set(row["ingredients"]):
            by_ingredient.setdefault(ingredient, []).append(row["id"])
    for ingredient, ids in by_ingredient.items():
        if len(ids) > 1:
            source = "FDA_ACETAMINOPHEN_DUPLICATES" if ingredient == "paracetamol" else "NICE_MEDICINES_RECONCILIATION"
            add("duplicate_ingredient", f"Ingredient {ingredient} appears in multiple current/proposed entries. Reconcile whether these are concurrent, replacements or duplicate records; do not automatically stop an existing medicine.", ids, source, block=True)

    # All supported pair rules are symmetric and include current-current pairs.
    for index, first in enumerate(all_rows):
        for second in all_rows[index + 1:]:
            pair = (first, second)
            ids = [first["id"], second["id"]]
            has = lambda ingredient: any(ingredient in row["ingredients"] for row in pair)
            if has("rifampicin") and any(row["class"] == "systemic hormonal contraceptive" for row in pair):
                add("ddi_rifampicin_contraceptive", "Rifampicin can reduce systemic hormonal contraceptive exposure. Arrange clinician/pharmacist counseling and a documented contraceptive plan, including the label's nonhormonal advice; do not automatically stop essential rifampicin.", ids, "DAILYMED_RIFAMPIN_CONTRACEPTIVES")
            if has("warfarin") and any("NSAID" in row["class"] for row in pair):
                add("ddi_warfarin_nsaid", "Warfarin with aspirin/NSAIDs increases bleeding risk. Review the indication, bleeding risk and monitoring plan; this is not a universal contraindication.", ids, "DAILYMED_WARFARIN_BLEEDING")
            if has("trimethoprim") and any(row["class"] == "ACE inhibitor" for row in pair):
                add("ddi_tmp_smx_acei", "The TMP-SMX label advises avoiding concurrent ACE-inhibitor use because of hyperkalemia risk. Hold this proposed regimen for clinician/pharmacist review of potassium, renal function and alternatives; do not invent laboratory results or stop existing therapy automatically.", ids, "DAILYMED_TMP_SMX_ACEI", block=True)

    allergies = context.get("allergies")
    if allergies is not None and not isinstance(allergies, list):
        add("invalid_allergy_history", "Allergies must be a structured list or unknown; the submitted history cannot be interpreted.", all_ids, block=True)
        allergies = []
    for allergy in allergies or []:
        record = {"substance": allergy} if isinstance(allergy, str) else allergy
        if not isinstance(record, dict) or not _text(record.get("substance")):
            add("invalid_allergy_history", "An allergy entry has no valid substance; clarify it before approving medicines.", all_ids, block=True)
            continue
        substance = _norm(record["substance"])
        known = resolve(record["substance"])["ingredients"]
        beta_lactam = bool(re.search(r"\b(penicillin|penicillins|beta-lactam|beta lactam|cephalosporin|cephalosporins|amoxicillin)\b", substance))
        for row in all_rows:
            ingredients = set(row["ingredients"])
            possible_amox = "amoxicillin" in ingredients and beta_lactam
            exact_match = bool(ingredients.intersection(known)) or any(re.search(r"\b" + re.escape(ingredient) + r"\b", substance) for ingredient in ingredients)
            if not (possible_amox or exact_match):
                continue
            intolerance = _norm(record.get("type")) == "intolerance"
            source = "DAILYMED_AMOXICILLIN_ALLERGY" if "amoxicillin" in ingredients else "NICE_MEDICINES_RECONCILIATION"
            if intolerance:
                add("recorded_intolerance_review", f"{row['name']}: recorded intolerance to {record['substance']}; confirm the reaction and suitability. Intolerance is not automatically a serious allergy.", [row["id"]], source)
            else:
                add("allergy_match_review", f"{row['name']}: possible match to recorded allergy {record['substance']}. Clarify reaction and severity before approval. Serious beta-lactam hypersensitivity is an amoxicillin contraindication; an unspecified history does not establish that diagnosis." if "amoxicillin" in ingredients else f"{row['name']}: possible match to recorded allergy {record['substance']}; clarify the reaction and obtain clinical review before approval.", [row["id"]], source, block=True)

    dose_assessed = []
    for row in all_rows:
        ids, name = [row["id"]], row["name"] or row["id"]
        ingredients = set(row["ingredients"])
        if not ingredients:
            continue
        if "antibiotic" in row["class"]:
            add("antibiotic_indication_review", f"{name}: confirm indication, applicable current guideline, dose, route and duration with an authorized prescriber. Recorded indication: {row.get('indication') or 'unknown'}. This rule does not establish appropriate antibiotic treatment.", ids, "WHO_AWARE")
        if "azithromycin" in ingredients:
            add("azithromycin_stewardship", "NIH Pakistan's 2024 typhoid advisory calls for restricted azithromycin use, including XDR typhoid and other serious conditions under clinical review. A diagnosis keyword does not verify the exception; confirm evidence, indication, prescriber and current local guidance.", ids, "NIH_PK_TYPHOID_2024")

        if ingredients == {"paracetamol"} and _norm(row.get("route")) in {"oral", "po", "by mouth"}:
            age, weight = _number(context.get("age"), zero=True), _number(context.get("weight_kg"))
            if age is None or age < 18:
                unassessed(f"{name}: adult paracetamol dose rule does not cover unknown age or patients younger than 18; no pediatric dose calculated.", ids, "paracetamol_population_review", "NHS_PARACETAMOL_ADULT")
            else:
                dose, frequency = row["dose_mg"], row["frequency_per_day"]
                # Ordinary adult ceilings can flag excess even when individual
                # suitability remains unknown; they must not clear low-weight or
                # organ-risk patients for these doses.
                if dose is None or frequency is None:
                    unassessed(f"{name}: explicit administered mg/dose and administrations/day are required; label strength is not a dose.", ids, "dose_not_assessed", "NHS_PARACETAMOL_ADULT")
                else:
                    dose_assessed.append(row["id"])
                    if dose > 1000 or dose * frequency > 4000 or frequency > 4:
                        add("paracetamol_adult_limit", f"{name}: entered {dose:g} mg/dose × {frequency:g}/day ({dose * frequency:g} mg/day) exceeds an ordinary adult limit (1000 mg/dose, 4000 mg/day or four administrations/day). Requires correction or clinical review.", ids, "NHS_PARACETAMOL_ADULT", block=True)
                    if frequency != int(frequency):
                        unassessed(f"{name}: fractional administrations/day needs schedule clarification; an average cannot verify a 24-hour maximum.", ids, "dose_schedule_review", "NHS_PARACETAMOL_ADULT")
                    if dose < 500:
                        unassessed(f"{name}: dose is below the usual adult tablet range; indication and effectiveness are not validated by this ceiling check.", ids, "dose_individual_review", "NHS_PARACETAMOL_ADULT")
                if weight is None or weight < 50:
                    unassessed(f"{name}: weight is unknown or below 50 kg; an individualized dose may be needed and is not calculated.", ids, "paracetamol_weight_review", "NHS_PARACETAMOL_ADULT")
                if row["duration_days"] is None or row["duration_days"] > 3:
                    unassessed(f"{name}: duration is unknown or exceeds three days; confirm the clinician-directed plan.", ids, "paracetamol_duration_review", "NHS_PARACETAMOL_ADULT")
                not_assessed.append(f"{name}: actual timing (at least four hours between doses), product instructions, alcohol-related risk and individualized suitability require human verification; frequency alone does not verify spacing.")
        else:
            unassessed(f"{name}: dose, frequency and duration are outside the implemented adult oral single-ingredient paracetamol rule (including combinations or unsupported routes).", ids, "dose_not_assessed")

        if context.get("pregnant") is True:
            if "NSAID" in row["class"]:
                weeks = _number(context.get("gestational_weeks"), zero=True)
                route = _norm(row.get("route"))
                systemic = route in {"oral", "po", "by mouth", "iv", "intravenous", "im", "intramuscular", "rectal"}
                if not systemic:
                    unassessed(f"{name}: route is unknown or outside systemic NSAID pregnancy checks; ophthalmic preparations are excluded from this FDA warning and other routes need product-specific review.", ids, "pregnancy_route_review", "FDA_NSAID_PREGNANCY")
                elif ingredients == {"aspirin"} and row["dose_mg"] == 81 and row["frequency_per_day"] == 1 and context.get("aspirin_prescribed") is True:
                    add("pregnancy_aspirin_81mg_review", "Reported clinician-directed aspirin 81 mg daily falls within the FDA communication's stated exception; verify the actual prescription, indication and ongoing review. This does not certify pregnancy safety.", ids, "FDA_NSAID_PREGNANCY")
                elif weeks is None or weeks > 45:
                    unassessed(f"{name}: gestational age is unknown or implausible; clarify timing before any pregnancy assessment.", ids, "pregnancy_timing_review", "FDA_NSAID_PREGNANCY")
                elif weeks >= 30:
                    add("pregnancy_nsaid_30_weeks", f"{name}: FDA advises avoiding systemic NSAIDs from 30 weeks because of fetal renal and ductus arteriosus risks; resolve with a clinician before approval.", ids, "FDA_NSAID_PREGNANCY", block=True)
                elif weeks >= 20:
                    add("pregnancy_nsaid_20_weeks", f"{name}: between 20 and 30 weeks, systemic NSAIDs require clinician justification, lowest effective dose and shortest duration; consider amniotic-fluid monitoring if beyond 48 hours.", ids, "FDA_NSAID_PREGNANCY")
                else:
                    unassessed(f"{name}: gestation is below 20 weeks; this timing-specific rule does not establish safety in early pregnancy.", ids, "pregnancy_early_review", "FDA_NSAID_PREGNANCY")
            else:
                unassessed(f"{name}: pregnancy suitability for this medicine is outside this rule pack; obtain indication-specific clinical review. No pregnancy letter category is used.", ids, "pregnancy_not_assessed")

    for row in all_rows:
        row["flags"] = [deepcopy(flag) for flag in flags if row["id"] in flag["medicine_ids"]]
        row["light"] = "RED" if any(flag["blocking"] for flag in row["flags"]) else "YELLOW" if row["flags"] else "GREEN"
    blocked = any(flag["blocking"] for flag in flags)
    status = "HOLD" if blocked else "REVIEW_REQUIRED" if flags else "NO_MEDICINES_PROPOSED" if not proposed else "NO_FLAGS_IN_SUPPORTED_CHECKS"
    fingerprint = _fingerprint({
        "ruleset_version": RULESET_VERSION,
        "medicines": [{field: row.get(field) for field in _INPUT_FIELDS} for row in proposed],
        "patient": context, "allowlist": allowlist, "input_errors": input_errors,
    })
    return {
        "ruleset_version": RULESET_VERSION, "input_hash": fingerprint,
        "status": status, "approval_blocked": blocked,
        "medicines": proposed, "current_medicines": current, "flags": flags,
        "not_assessed": list(dict.fromkeys(not_assessed)),
        "coverage": {
            "identity": "Exact matches to the finite demonstration vocabulary; local product labels still require confirmation.",
            "ingredient_reconciliation": "Current and proposed supported ingredients; overlap requires reconciliation.",
            "interactions": "Only rifampicin/systemic hormonal contraception, warfarin/NSAIDs and TMP-SMX/selected ACE inhibitors.",
            "dose": f"Ordinary adult oral single-ingredient paracetamol ceilings evaluated for {len(dose_assessed)} entries; no dose recommendation or full regimen verification.",
            "allergy": "Possible recorded ingredient matches and amoxicillin/beta-lactam history; not a complete cross-reactivity assessment.",
            "pregnancy": "Selected systemic NSAID timing and reported prescribed aspirin 81 mg daily exception; other cases unassessed.",
            "formulary": "Not applied" if allowlist is None else "Configured consultation list only; inclusion is not clinical approval.",
        },
        "limitations": [
            "Source-backed prototype; independent clinical validation and local formulary sign-off are pending.",
            "No flags means only no findings in implemented checks; it does not mean safe, approved, all interactions checked or ready to dispense.",
            "This pack does not assess diagnosis, comprehensive contraindications, pediatric dosing, organ-dose adjustments, laboratory values, breastfeeding or every medicine/formulation.",
            "A HOLD is a workflow resolution requirement; existing medicines must not be stopped automatically. Review notes preserve original findings.",
            "Brand identity and actual administration schedule require confirmation against the product and patient history.",
        ],
    }


def approval_error(report: Any, acknowledged: Any, reason: Any) -> str | None:
    """Backend decision gate; UI controls are not trusted to enforce it."""
    if not isinstance(report, dict) or not report.get("input_hash") or report.get("ruleset_version") != RULESET_VERSION:
        return "A current medication review is required before approval."
    if report.get("approval_blocked") or report.get("status") == "HOLD":
        return "Medication review is on HOLD. Correct/reconcile the medicines or context, or refer for clinical review; acknowledgment cannot clear a blocker."
    if report.get("status") == "NO_MEDICINES_PROPOSED":
        return None
    if report.get("status") not in {"REVIEW_REQUIRED", "NO_FLAGS_IN_SUPPORTED_CHECKS"}:
        return "Medication review has an unrecognized status; rerun it."
    if acknowledged is not True:
        return "Acknowledge the current medication findings and unassessed areas before approval."
    if report["status"] == "REVIEW_REQUIRED" and (not isinstance(reason, str) or len(reason.strip()) < 10):
        return "Record a meaningful review and follow-up/referral plan (at least 10 characters)."
    return None
