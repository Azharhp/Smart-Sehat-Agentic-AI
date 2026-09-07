"""Deterministic triage rules — NEVER an LLM (safety law).

Rule tables cover THREE scripts: English, Urdu-script (Gemini/AIHubMix/
TokenHarbor STT output), and Devanagari (Qwen ASR output — bake-off caveat
#4). The safety floor must catch emergencies directly from the transcript,
even if every LLM step (structure extraction) fails.

Chest pain is decomposed into semantic atoms (chest AND pain AND ACS
feature) — real speech inserts intensity words ("بہت", "बहुत", "bohat")
inside fixed phrases, breaking literal substring matching.

Structured observations (Phase 3) are the preferred evidence source when
present; raw text remains the bounded fallback screening path. Accepted
history is consumed separately from raw text so a worker correction never
has to fight the rejected ASR wording (CLIN-008).

Bounded negation: an explicit negative marker makes its clause invisible to
raw-text matching, so "no blood in sputum" does not trigger the hemoptysis
rule while "blood in sputum" still does. Mixed positive/negative evidence
keeps high-sensitivity fallback — positive segments still match.

Truthful states: a no-match result is `NO_CONFIGURED_FLAG`, never "patient
safe". An empty/unassessed intake is `INSUFFICIENT_DATA`. Only the `RED`
demo/test force hook is honoured; nothing in a production payload can force
an emergency to GREEN (CLIN-010).
"""
from __future__ import annotations
import hashlib
import re
from typing import Any

RULE_VERSION = "2026-09-07.1"
SEVERITY = {"RED": "RED", "YELLOW": "YELLOW"}

# Negation markers (English, Roman Urdu, Urdu script, Devanagari). A clause
# containing one of these is dropped from the raw-text haystack. Unicode word
# boundaries keep "نہ" from matching inside ordinary words such as "پسینہ"
# (paseena / sweat), which must not turn the clause into a negation.
_NEGATION = re.compile(
    r"(?<![\w])(?:no|not|nahi|nhi|na)(?![\w])"
    r"|(?<![\w])نہیں(?![\w])"
    r"|(?<![\w])نہ(?![\w])"
    r"|(?<![\w])नहीं(?![\w])"
    r"|(?<![\w])नही(?![\w])"
)

# Clause separators: sentence punctuation plus common coordinating
# conjunctions, so "cough but no sweat" keeps its positive clause.
_CLAUSE_SPLIT = re.compile(
    r"[\.,;:!؟?۔…،]|"
    r"\b(?:and|but|aur|lekin|or)\b"
)

# Structured observation concepts that correspond to existing configured
# rules. Duration/age rules reuse the deterministic numeric values.
_FEVER_CONCEPTS = ("fever",)
_COUGH_CONCEPTS = ("cough",)


def _haystack(state: dict[str, Any]) -> str:
    """Lowercased searchable text from symptoms, transcripts and history."""
    parts: list[str] = []
    for s in state.get("symptoms") or []:
        parts.append(str(s).lower().replace("_", " "))
    for k in ("transcript_confirmed", "transcript", "chief_complaint"):
        v = state.get(k)
        if v:
            parts.append(str(v).lower())
    history = state.get("history")
    if isinstance(history, dict):
        # Only the accepted history governs post-confirmation triage. Raw /
        # corrected text must never re-introduce a rejected ASR wording back
        # into the decision (CLIN-008). Pre-confirmation raw screening reads
        # ``transcript`` above.
        v = history.get("accepted_text")
        if v:
            parts.append(str(v).lower())
    for obs in state.get("observations") or []:
        if not isinstance(obs, dict) or obs.get("verification_state") != "CONFIRMED":
            continue
        if obs.get("assertion") == "PRESENT":
            concept = str(obs.get("concept") or "").replace("_", " ")
            if concept:
                parts.append(concept)
    return " | ".join(parts)


def _strip_negated(hay: str) -> str:
    """Remove clauses carrying an explicit negation marker (bounded)."""
    if not hay or not _NEGATION.search(hay):
        return hay
    kept = []
    for clause in _CLAUSE_SPLIT.split(hay):
        clause = clause.strip()
        if not clause:
            continue
        if not _NEGATION.search(clause):
            kept.append(clause)
    return " | ".join(kept)


# Intentionally stem-like ASCII variants that must keep prefix-matching their
# inflected forms: "pregnan" matches pregnant/pregnancy, "bleed" matches
# bleeding/bleeds. Every other single ASCII word is matched at word boundaries
# so "benefits" never silently asserts "fits" (CLIN-007).
_WORD_STEMS = {"pregnan", "bleed"}


def _has_any(hay: str, variants: list[str]) -> bool:
    low = hay.lower()
    for variant in variants:
        v = variant.lower()
        if not v:
            continue
        # Boundaries apply only to single ASCII words that are not stems;
        # multi-word phrases and non-ASCII script terms keep substring match.
        if v.isascii() and v.isalnum() and v not in _WORD_STEMS:
            start = 0
            while True:
                idx = low.find(v, start)
                if idx < 0:
                    break
                before = idx - 1
                after = idx + len(v)
                if (before < 0 or not low[before].isalpha()) and \
                   (after >= len(low) or not low[after].isalpha()):
                    return True
                start = idx + 1
        elif v in low:
            return True
    return False


def _first_match(rules: list[tuple[str, list[list[str]]]], hay: str) -> tuple[str, list[str]] | None:
    """Return (rule_name, [matched_keyword_groups]) or None."""
    for name, and_groups in rules:
        if all(_has_any(hay, group) for group in and_groups):
            return name, [group[0] for group in and_groups]
    return None


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
         "blood in sputum", "khoon in sputum", "balgham mein khoon",
         "बलगम में खून", "بلغم میں خون"],
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


def _numeric_observation(observations: list[dict], concept: str) -> dict | None:
    """Return the latest CONFIRMED numeric observation for concept."""
    for obs in reversed(observations or []):
        if not isinstance(obs, dict) or obs.get("verification_state") != "CONFIRMED":
            continue
        if obs.get("concept") != concept or obs.get("assertion") != "PRESENT":
            continue
        value = obs.get("normalized_value")
        if value is None:
            value = obs.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return obs
    return None


def _has_present(observations: list[dict], concept: str) -> bool:
    return any(
        isinstance(obs, dict) and obs.get("verification_state") == "CONFIRMED"
        and obs.get("assertion") == "PRESENT" and obs.get("concept") == concept
        for obs in (observations or [])
    )


def _finding(name: str, severity: str, matched: list[str]) -> dict[str, Any]:
    return {
        "rule_id": name,
        "rule_version": RULE_VERSION,
        "severity": severity,
        "reason": f"Configured {severity} rule triggered: {name}.",
        "evidence": [{"matched_text": v} for v in matched],
    }


def _input_hash(state: dict[str, Any], hay: str) -> str:
    """Deterministic fingerprint over the actual inputs used for triage.

    ``history``/``observations``/``demographics``/``symptoms`` are the
    canonical evidence; anything else (draft fields, force hooks) is excluded
    so the hash only changes when assessed evidence changes.
    """
    canonical = _canonical_text(hay)
    for key in ("symptoms", "observations", "history", "demographics"):
        value = state.get(key)
        if value is not None:
            canonical += "|" + _canonical_text(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{k}={_canonical_text(v)}" for k, v in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return " ".join(_canonical_text(v) for v in value)
    if value is None:
        return ""
    return str(value)


def _structured_forced(state: dict[str, Any]) -> dict[str, Any] | None:
    """Structured age/duration rules reusing existing configured rule IDs.

    Only rules already present in RED_RULES/YELLOW_RULES are evaluated here;
    no new medical thresholds are invented (CLIN-006 fix within scope).
    """
    observations = state.get("observations") or []

    for obs in observations:
        if not isinstance(obs, dict) or obs.get("verification_state") != "CONFIRMED":
            continue
        if obs.get("concept") == "age" and obs.get("assertion") == "PRESENT":
            age_value = obs.get("normalized_value")
            if age_value is None:
                age_value = obs.get("value")
            age_unit = (obs.get("normalized_unit") or obs.get("unit") or "years").lower()
            if age_unit in {"months", "month"} and isinstance(age_value, (int, float)) and age_value < 2 and _has_present(observations, "fever"):
                return _red("infant_lt_2mo_with_fever",
                            [{"observation_id": obs.get("observation_id"),
                              "concept": "age", "value": age_value, "unit": age_unit, "assertion": "PRESENT"},
                             {"concept": "fever", "assertion": "PRESENT"}])

    # Duration thresholds reuse the existing configured YELLOW rule IDs.
    fever_days = _normalized_days(_numeric_observation(observations, "fever_duration"))
    if fever_days is not None and fever_days >= 3:
        return _yellow("fever_gt_3days",
                       [{"concept": "fever_duration", "value": fever_days, "unit": "days",
                         "assertion": "PRESENT"}], "fever_duration")
    cough_days = _normalized_days(_numeric_observation(observations, "cough_duration"))
    if cough_days is not None and cough_days > 14:
        return _yellow("cough_gt_14days",
                       [{"concept": "cough_duration", "value": cough_days, "unit": "days",
                         "assertion": "PRESENT"}], "cough_duration")
    return None


def _normalized_days(obs: dict | None) -> float | None:
    """Days value from a normalized duration observation, None if unknown."""
    if obs is None:
        return None
    value = obs.get("normalized_value")
    unit = (obs.get("normalized_unit") or obs.get("unit") or "").lower()
    if not (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return None
    if unit == "weeks":
        return float(value) * 7
    if unit in ("days", "day"):
        return float(value)
    return None


def _red(name: str, evidence: list[dict]) -> dict[str, Any]:
    return {
        "level": "RED", "status": "RED",
        "triggered_rules": [name, *[e.get("concept", "") for e in evidence]],
        "rule_name": name, "rule_version": RULE_VERSION,
        "findings": [{"rule_id": name, "rule_version": RULE_VERSION, "severity": "RED",
                      "reason": f"Configured RED rule triggered: {name}.",
                      "evidence": evidence}],
        "instruction": _instruction_for("RED"),
    }


def _yellow(name: str, evidence: list[dict], reason_hint: str | None = None,
            severity: str = "YELLOW") -> dict[str, Any]:
    return {
        "level": severity, "status": severity,
        "triggered_rules": [name, *[e.get("concept", "") for e in evidence]],
        "rule_name": name, "rule_version": RULE_VERSION,
        "findings": [{"rule_id": name, "rule_version": RULE_VERSION, "severity": severity,
                      "reason": f"Configured {severity} rule triggered: {name}."
                                + (f" Confirmed {reason_hint} value provided." if reason_hint else ""),
                      "evidence": evidence}],
        "instruction": _instruction_for(severity),
    }


def evaluate(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic triage decision.

    Returns a dict with BOTH the legacy contract (``level``,
    ``triggered_rules``, ``instruction`` — used by test_flow and
    test_emergency_protocols) and the Phase 3 contract (``status``,
    ``findings``, ``rule_version``, ``input_hash``, ``missing_information``,
    ``unresolved_evidence``, ``limitations``).
    """
    # Force override: only RED is honoured. A production payload that tries
    # to force YELLOW or GREEN is ignored so an emergency can never be
    # silently downgraded (CLIN-010 / A10).
    forced = state.get("force_triage")
    if forced == "RED":
        return {
            "level": "RED", "status": "RED",
            "triggered_rules": ["force_triage:RED"],
            "findings": [{"rule_id": "force_triage:RED", "rule_version": RULE_VERSION,
                          "severity": "RED", "reason": "Demo/test force hook forced RED.",
                          "evidence": []}],
            "unresolved_evidence": [], "limitations": [],
            "missing_information": _missing_information(state),
            "rule_version": RULE_VERSION,
            "input_hash": _input_hash(state, ""),
            "instruction": _instruction_for("RED"),
        }

    raw_hay = _haystack(state)
    hay = _strip_negated(raw_hay)

    # Structured age/duration rules run before the raw-text fallback so a
    # confirmed structured observation can never be downgraded by text.
    structured = _structured_forced(state)
    if structured:
        return {**structured, "rule_version": RULE_VERSION,
                "unresolved_evidence": [], "limitations": [],
                "missing_information": _missing_information(state),
                "input_hash": _input_hash(state, raw_hay)}

    if not hay:
        # No non-negated text matched the negative-reflection scan. When the
        # original raw text provides *nothing* assessable we say so directly;
        # a fully-negated complaint is still treated as insufficient data for
        # rules that require a positive symptom (never "no rule matched").
        return {
            "level": "INSUFFICIENT_DATA", "status": "INSUFFICIENT_DATA",
            "triggered_rules": [],
            "findings": [], "unresolved_evidence": [], "limitations": [],
            "missing_information": _missing_information(state),
            "rule_version": RULE_VERSION, "input_hash": _input_hash(state, raw_hay),
            "instruction": "INSUFFICIENT DATA — no confirmed positive history or structured observations are available to assess.",
        }
        return {
            "level": "INSUFFICIENT_DATA", "status": "INSUFFICIENT_DATA",
            "triggered_rules": [],
            "findings": [], "unresolved_evidence": [], "limitations": [],
            "missing_information": _missing_information(state),
            "rule_version": RULE_VERSION, "input_hash": _input_hash(state, raw_hay),
            "instruction": "INSUFFICIENT DATA — no confirmed history or structured observations are available to assess.",
        }

    red = _first_match(RED_RULES, hay)
    if red:
        name, matched = red
        return {
            "level": "RED", "status": "RED",
            "triggered_rules": [name, *matched],
            "findings": [_finding(name, "RED", matched)],
            "unresolved_evidence": [], "limitations": [],
            "missing_information": _missing_information(state),
            "rule_version": RULE_VERSION, "input_hash": _input_hash(state, raw_hay),
            "instruction": _instruction_for("RED"),
        }
    yellow = _first_match(YELLOW_RULES, hay)
    if yellow:
        name, matched = yellow
        return {
            "level": "YELLOW", "status": "YELLOW",
            "triggered_rules": [name, *matched],
            "findings": [_finding(name, "YELLOW", matched)],
            "unresolved_evidence": [], "limitations": [],
            "missing_information": _missing_information(state),
            "rule_version": RULE_VERSION, "input_hash": _input_hash(state, raw_hay),
            "instruction": _instruction_for("YELLOW"),
        }
    # No configured rule matched within the assessed scope. This is NOT a
    # claim that the patient is safe.
    return {
        "level": "GREEN", "status": "NO_CONFIGURED_FLAG",
        "triggered_rules": [],
        "findings": [],
        "unresolved_evidence": [], "limitations": [],
        "missing_information": _missing_information(state),
        "rule_version": RULE_VERSION, "input_hash": _input_hash(state, raw_hay),
        "instruction": "NO CONFIGURED FLAG — no rule matched within the assessed scope; this does not establish that the patient is safe.",
    }


def _missing_information(state: dict[str, Any]) -> list[str]:
    """Return a truthful list of fields that were never assessed/known."""
    missing: list[str] = []
    if not (state.get("history") or state.get("transcript_confirmed") or state.get("transcript")):
        missing.append("confirmed patient history")
    if not state.get("observations"):
        missing.append("structured observations")
    else:
        present = {obs.get("concept") for obs in state.get("observations") or []
                   if isinstance(obs, dict) and obs.get("verification_state") == "CONFIRMED"}
        if not present:
            missing.append("structured observations")
    return missing


def _instruction_for(level: str) -> str:
    if level == "RED":
        return "RED: transport immediately, initiate emergency protocol"
    if level == "YELLOW":
        return "YELLOW: same-day RHU referral; continue pathway"
    return "GREEN: routine care, standard counselling"