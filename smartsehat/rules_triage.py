"""Deterministic triage rules — NEVER an LLM (safety law).

Rule tables cover THREE scripts: English, Urdu-script (Gemini STT output),
and Devanagari (Qwen ASR output — bake-off caveat #4). The safety floor
must catch emergencies directly from the raw transcript, even if every
LLM step (structure extraction) fails.

Chest pain is decomposed into semantic atoms (chest AND pain AND ACS
feature) — real speech inserts intensity words ("بہت", "बहुत", "bohat")
inside fixed phrases, breaking literal substring matching.

Rules ordered by severity; first RED trigger wins, else first YELLOW,
else GREEN. The `force_triage` state hook still overrides (test / demo).
"""
from __future__ import annotations
from typing import Any


def _haystack(state: dict[str, Any]) -> str:
    """Lowercased searchable text from symptoms + transcript."""
    parts: list[str] = []
    for s in state.get("symptoms") or []:
        parts.append(str(s).lower().replace("_", " "))
    for k in ("transcript_confirmed", "transcript", "chief_complaint"):
        v = state.get(k)
        if v:
            parts.append(str(v).lower())
    return " | ".join(parts)


def _has_any(hay: str, variants: list[str]) -> bool:
    return any(v.lower() in hay for v in variants)


# RED rules — any-of triggers immediate emergency routing.
RED_RULES: list[tuple[str, list[list[str]]]] = [
    # rule name, list of AND-groups; each AND-group is a list of OR variants.
    ("chest_pain_with_ACS_features", [
        ["chest", "seene", "सीने", "سینے"],
        ["pain", "dard", "दर्द", "درد"],
        ["radiation", "left arm", "jaw", "sweating", "pasina", "paseena",
         "diaphoresis", "पसीना", "पसीना", "پسینہ", "बाजू", "बाजू", "بازو"],
    ]),
    ("severe_breathlessness", [
        ["severe breathlessness", "cannot breathe", "gasping", "saans nahi",
         "severe_breathlessness", "respiratory distress", "सांस नहीं", "साँस नहीं", "سانس نہیں"],
    ]),
    ("altered_consciousness_or_fits", [
        ["unconscious", "unresponsive", "fits", "seizure", "convulsion",
         "behoshi", "बेहोश", "बेहोशी", "दौरे", "تشنج", "بےہوش", "بیہوش", "دورے"],
    ]),
    ("pregnancy_with_bleeding", [
        ["pregnan", "hamila", "hamal", "गर्भवती", "गर्भ", "حاملہ", "حمل"],
        ["bleed", "khoon", "haemorrhage", "hemorrhage", "खून", "خون"],
    ]),
    ("infant_lt_2mo_with_fever", [
        ["neonat", "newborn", "less than 2 months", "<2 months", "infant_lt_2mo"],
        ["fever", "bukhar", "बुखार", "बुख़ार", "بخار"],
    ]),
]


# YELLOW rules — same-day referral / continue pathway.
YELLOW_RULES: list[tuple[str, list[list[str]]]] = [
    ("hemoptysis_streak", [
        ["streak hemoptysis", "streak_hemoptysis", "hemoptysis",
         "balgham mein khoon", "khoon in sputum", "बलगम में खून", "بلغم میں خون"],
    ]),
    ("fever_gt_3days", [
        ["fever > 3", "fever more than 3", "3 din se bukhar", "fever_gt_3days",
         "fever for 3 days", "fever for more than 3 days",
         "तीन दिन से बुखार", "تین دن سے بخار"],
    ]),
    ("cough_gt_14days", [
        ["cough > 14", "cough more than 14", "chronic cough", "chronic_cough",
         "cough for 2 weeks", "2 hafta khansi", "cough_gt_14days",
         "दो हफ्तों से खांसी", "दो हफ़्ते से खांस", "دو ہفتوں سے کھانسی"],
    ]),
    ("pregnancy_with_fever", [
        ["pregnan", "hamila", "hamal", "गर्भवती", "गर्भ", "حاملہ", "حمل"],
        ["fever", "bukhar", "बुखार", "बुख़ार", "بخار"],
    ]),
    ("weight_loss_and_night_sweats", [
        ["weight loss", "wazan", "वजन", "وزن"],
        ["night sweats", "night_sweats", "raat pasina", "raat ko paseena",
         "रात को पसीना", "रात में पसीना", "رات کو پسینہ"],
    ]),
]


def _first_match(rules: list[tuple[str, list[list[str]]]], hay: str) -> tuple[str, list[str]] | None:
    """Return (level_specific_rule_name, [triggered_keyword_groups]) or None."""
    for name, and_groups in rules:
        if all(_has_any(hay, group) for group in and_groups):
            return name, [group[0] for group in and_groups]
    return None


def evaluate(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic triage decision. Returns:
       {"level": "RED"|"YELLOW"|"GREEN", "triggered_rules": [...], "instruction": str}
    """
    # Force override (test / demo hook).
    forced = state.get("force_triage")
    if forced in ("RED", "YELLOW", "GREEN"):
        return {
            "level": forced,
            "triggered_rules": [f"force_triage:{forced}"],
            "instruction": _instruction_for(forced),
        }

    hay = _haystack(state)

    red = _first_match(RED_RULES, hay)
    if red:
        return {
            "level": "RED",
            "triggered_rules": [red[0], *red[1]],
            "instruction": _instruction_for("RED"),
        }
    yellow = _first_match(YELLOW_RULES, hay)
    if yellow:
        return {
            "level": "YELLOW",
            "triggered_rules": [yellow[0], *yellow[1]],
            "instruction": _instruction_for("YELLOW"),
        }
    return {
        "level": "GREEN",
        "triggered_rules": [],
        "instruction": _instruction_for("GREEN"),
    }


def _instruction_for(level: str) -> str:
    if level == "RED":
        return "RED: transport immediately, initiate emergency protocol"
    if level == "YELLOW":
        return "YELLOW: same-day RHU referral; continue pathway"
    return "GREEN: routine care, standard counselling"