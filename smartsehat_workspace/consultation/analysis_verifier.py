"""Verify constrained consideration structure against ledger/catalogue sources.

This proves supported rendering and current citations, not medical truth or
entailment of arbitrary clinical prose. The clinical catalogue awaits review.
"""
import re


def verify_analysis(analysis: dict, facts: list[dict]) -> dict:
    from .reasoning import catalogue
    specs = {item["id"]: item for item in catalogue()}
    confirmed = {fact["id"]: fact for fact in facts if fact.get("confirmed")}
    by_key = {fact["key"]: fact for fact in confirmed.values()}
    references = {row["id"] for row in analysis.get("references", [])}
    failures, seen = [], set()
    def fail(text, ids=()):
        failures.append({"sentence": text, "conflicting_fact_ids": list(ids), "message": "Consideration/source validation failed."})
    for index, item in enumerate(analysis["considerations"], 1):
        spec = specs.get(item.get("id"))
        if not spec or item["id"] in seen or item.get("rank") != index:
            fail("Unsupported, duplicated or incorrectly ranked consideration.")
            continue
        seen.add(item["id"])
        values = {key: fact["value"] for key, fact in by_key.items()}
        if not any(values.get(key) is True for key in spec["trigger_any"]):
            fail("Consideration is not eligible for the confirmed symptoms.")
        if item["title"] != spec["title"] or item["separating_test"] != spec["separating_test"]:
            fail("Consideration wording differs from the configured template.")
        cited = set()
        for group in ("evidence_for", "evidence_against"):
            for line in item.get(group, []):
                ids = re.findall(r"\[(F\d+)\]", line)
                cited.update(ids)
                allowed_keys = spec["against"] if group == "evidence_against" else spec["supporting"] + spec["measurements"]
                if any(fid in confirmed and confirmed[fid]["key"] not in allowed_keys for fid in ids):
                    fail("Evidence is outside this consideration's configured fields.", ids)
                if group == "evidence_against" and any(fid not in confirmed or confirmed[fid]["value"] is not False for fid in ids):
                    fail("Evidence against is not a confirmed negative.", ids)
        if cited != set(item.get("fact_ids", [])) or any(fid not in confirmed for fid in cited):
            fail("Consideration citations differ from its current rendered evidence.", cited)
        if any(key in by_key and by_key[key]["value"] is not None for key in item.get("missing", [])):
            fail("A known fact is incorrectly marked missing.")
        if set(item.get("missing", [])) != {key for key in spec["missing"] if values.get(key) is None}:
            fail("Consideration gaps differ from its configured missing information.")
        if not set(item.get("reference_ids", [])) <= references:
            fail("A consideration references an unavailable source.")
        if any(key in item for key in ("diagnosis", "dose", "dose_mg", "probability", "safe_to_ignore")):
            fail("Unapproved diagnostic, dose or probability output.")
    return {"status": "BLOCKED" if failures else "VERIFIED_STRUCTURE", "failures": failures,
            "clinical_interpretation": "Pending independent clinical review; no arbitrary-prose entailment claim."}
