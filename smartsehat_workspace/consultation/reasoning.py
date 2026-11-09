"""Doctor-facing considerations grounded in a constrained clinical catalogue.

Rank describes evidence coverage, not disease probability or a diagnosis.
The model may rank/choose only eligible catalogue entries and current fact IDs.
"""
from __future__ import annotations
from functools import lru_cache
import yaml
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal
from .clinical import ROOT, config
from .narrative import sentence
from .ai import call_json


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consideration_id: str
    fact_ids: list[str]


class Selections(BaseModel):
    model_config = ConfigDict(extra="forbid")
    considerations: list[Selection] = Field(max_length=8)
    concern: Literal["GREEN", "AMBER", "RED"] = "GREEN"
    concern_fact_ids: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def catalogue() -> list[dict]:
    return yaml.safe_load((ROOT / "config/considerations.yaml").read_text(encoding="utf-8"))["items"]


def reason(facts: list[dict], urgency: dict, live: bool = False) -> dict:
    from smartsehat_workspace import corpus
    confirmed = [f for f in facts if f.get("confirmed")]
    by_key = {f["key"]: f for f in confirmed}
    values = {k: f["value"] for k, f in by_key.items()}
    eligible = []
    for item in catalogue():
        if not any(values.get(k) is True for k in item["trigger_any"]):
            continue
        if item.get("minimum_duration_key") and (values.get(item["minimum_duration_key"]) or 0) < config()["thresholds"][item["duration_threshold_config"]]:
            continue
        eligible.append(item)
    if not eligible:
        return {"considerations": [], "references": [], "mode": "General collect-and-send; no AI clinical reasoning",
                "claims": [], "missing": urgency["missing_critical"], "suggested_measurements": urgency["missing_critical"], "ai_concern": "GREEN"}
    fallback = {"considerations": [{"consideration_id": item["id"], "fact_ids": [by_key[k]["id"] for k in item["supporting"] + item["measurements"] if k in by_key]} for item in eligible]}
    output, mode = call_json("clinical-considerations-v2", {"confirmed_facts": confirmed,
                            "eligible_catalogue": eligible, "urgency": urgency}, Selections, fallback, live)
    chosen = {item["id"]: item for item in eligible}
    proposed_ids = {item["consideration_id"] for item in output["considerations"]}
    output["considerations"] += [item for item in fallback["considerations"] if item["consideration_id"] not in proposed_ids]
    ids = {f["id"]: f for f in confirmed}
    invalid = []
    items = []
    used = set()
    for selection in output["considerations"]:
        code = selection["consideration_id"]
        if code not in chosen:
            invalid.append(f"Unsupported clinical consideration: {code}")
            continue
        if code in used:
            continue
        used.add(code)
        spec = chosen[code]
        invalid += [f"Unknown or unconfirmed citation [{fid}]" for fid in selection["fact_ids"] if fid not in ids]
        positive = [by_key[k] for k in spec["supporting"] if values.get(k) is True]
        negative = [by_key[k] for k in spec["against"] if values.get(k) is False]
        measured = [by_key[k] for k in spec["measurements"] if k in by_key and values.get(k) is not None]
        missing = [k for k in spec["missing"] if values.get(k) is None]
        references = corpus.retrieve({"symptoms": spec["reference_terms"]}, top_k=2)
        attached_ids = list(dict.fromkeys([f["id"] for f in positive + measured + negative]))
        items.append({"id": code, "title": spec["title"], "fact_ids": attached_ids,
            "evidence_for": [sentence(f) for f in positive + measured], "evidence_against": [sentence(f) for f in negative],
            "missing": missing, "separating_test": spec["separating_test"], "reference_ids": [r["id"] for r in references],
            "coverage": len(positive) + len(measured), "priority": spec["priority"], "review_status": "pending"})
    items.sort(key=lambda item: (-item["coverage"], -item["priority"], item["id"]))
    for rank, item in enumerate(items, 1):
        item["rank"] = rank
    references = {ref["id"]: ref for item in eligible for ref in corpus.retrieve({"symptoms": item["reference_terms"]}, top_k=2)}
    claims = [line for item in items for line in item["evidence_for"] + item["evidence_against"]]
    # Unsupported model proposals cannot disappear into an apparently verified packet.
    claims += invalid
    concern = output.get("concern", "GREEN")
    concern_ids = output.get("concern_fact_ids", [])
    if concern != "GREEN" and (not concern_ids or any(fid not in ids for fid in concern_ids)):
        claims.append("AI concern did not cite current confirmed evidence")
        concern = "GREEN"
    return {"considerations": items, "references": list(references.values()), "claims": claims,
            "mode": mode, "missing": list(dict.fromkeys(urgency["missing_critical"] + [k for item in items for k in item["missing"]])),
            "suggested_measurements": [k for k in dict.fromkeys(urgency["missing_critical"] + [k for item in items for k in item["missing"]]) if k in config()["measurements"]],
            "ai_concern": concern, "concern_fact_ids": concern_ids,
            "ranking_basis": "Evidence coverage only; no diagnostic probability"}
