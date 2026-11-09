"""Fail-closed verification of value-exact rendered ledger sentences.

Free model prose cannot be proved consistent by substring/negation heuristics.
It is blocked. English/Urdu deterministic fact sentences and canonical audit
sentences are accepted; interpretation uses separately labelled templates.
"""
from __future__ import annotations
import json
import re


def fact_sentence(fact: dict) -> str:
    value = json.dumps(fact["value"], ensure_ascii=False, sort_keys=True)
    missing = f" (not done: {fact['reason_missing']})" if fact["value"] is None and fact.get("reason_missing") else ""
    return f"{fact['key']} = {value}" + (f" {fact['unit']}" if fact.get("unit") else "") + missing + f" [{fact['id']}]"


def verify(sentences: list[str], facts: list[dict]) -> dict:
    current = {f["id"]: f for f in facts if f.get("confirmed")}
    failures = []
    for sentence in sentences:
        ids = re.findall(r"\[(F\d+)\]", sentence)
        message = ""
        if not ids:
            message = "Uncited clinical claim."
        elif any(i not in current for i in ids):
            message = "Citation is unconfirmed, superseded, or unknown."
        else:
            from .narrative import sentence as render_sentence
            allowed = set()
            if len(ids) == 1:
                fact = current[ids[0]]
                allowed = {fact_sentence(fact), render_sentence(fact, "English"), render_sentence(fact, "Urdu")}
            if sentence not in allowed:
                message = "Claim does not match its confirmed fact, value, negation and unit. Free clinical claims require review."
        if message:
            failures.append({"sentence": sentence, "conflicting_fact_ids": ids, "message": message})
    return {"status": "BLOCKED" if failures else "VERIFIED", "failures": failures}
