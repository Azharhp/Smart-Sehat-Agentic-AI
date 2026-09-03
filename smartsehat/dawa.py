"""Dawa Check — deterministic prescription / medicine-bag safety rules.

NEVER an LLM call — the rules run in Python. Vision (extract_node in
dawa_graph.py) can be an LLM; the actual RED / YELLOW / GREEN verdict
is decided here, in code, with reasons and citations.

Traffic-light semantics (LAW):
- GREEN  = "no flags found" (NEVER "good for the patient")
- YELLOW = one or more advisory flags — paramedic review
- RED    = one or more contraindications — do not dispense as-is

Day-3 patches applied in this version:
- PATCH 1a: the rifampicin-OCP DDI citation now points at a REAL chunk
  (NEML_PK_2023_tb_medicines — the Gazette rifampicin entry) instead of
  the invented "NEML_PK_2023_rifampicin_OCP_interaction" id.
- PATCH 1b: the artemether+lumefantrine pregnancy logic is now
  INDICATION-AWARE and medically correct. The WHO malaria treatment
  guideline (2022 update) recommends AL for falciparum malaria in ALL
  trimesters — untreated malaria in pregnancy is the greater risk. The
  NEML first-trimester caution is legacy EML language. Our corpus holds
  BOTH real documents; the flags cite the appropriate one and ask the
  paramedic to verify indication — never a blanket RED that would block
  a life-saving treatment.
- PATCH 2: citation gate — every flag citation is validated against
  corpus.get_chunk_map(); dangling citations are stripped (flag + reason
  remain). Same law as the reasoning agent's citation gate.
"""
from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


BRAND_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "brand_generic_map.json"


# ----------------------------------------------------------------------
# Brand / generic resolution
# ----------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_brand_map() -> list[dict]:
    with open(BRAND_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+]", "", (s or "").lower())


def resolve(name: str) -> dict:
    """Case-insensitive substring matching in BOTH directions so we tolerate
    slight misspellings, dosage suffixes, and roman-Urdu spellings."""
    q = _norm(name)
    if not q:
        return {"generic": None, "drug_class": "unrecognized",
                "pregnancy_category": None, "notes": None,
                "matched_brand": None}
    best: tuple[int, dict, str] | None = None  # (score, entry, matched_brand)
    for entry in _load_brand_map():
        candidates = [entry.get("generic") or ""] + (entry.get("brands") or [])
        for cand in candidates:
            c = _norm(cand)
            if not c:
                continue
            score = 0
            if q == c:
                score = 100
            elif c in q:
                score = 80 + len(c)
            elif q in c:
                score = 60 + len(q)
            if score and (best is None or score > best[0]):
                best = (score, entry, cand)
    if best is None:
        return {"generic": None, "drug_class": "unrecognized",
                "pregnancy_category": None, "notes": None,
                "matched_brand": None}
    _, entry, matched = best
    return {
        "generic": entry.get("generic"),
        "drug_class": entry.get("drug_class"),
        "pregnancy_category": entry.get("pregnancy_category"),
        "notes": entry.get("notes"),
        "matched_brand": matched,
    }


# ----------------------------------------------------------------------
# Strength / dose parsing
# ----------------------------------------------------------------------

_STRENGTH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|g|ml|mcg)", re.IGNORECASE)


def parse_strength_mg(text: str) -> float | None:
    """Best-effort dose parser — returns milligrams, else None."""
    if not text:
        return None
    m = _STRENGTH_RE.search(text)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "g":
        return value * 1000
    if unit == "mg":
        return value
    if unit == "mcg":
        return value / 1000
    return None  # ml — not a dose


# ----------------------------------------------------------------------
# Rules
# ----------------------------------------------------------------------

# Pregnancy citation map — chunk ids to cite for specific generics.
_PREG_CITATION = {
    "artemether+lumefantrine": "NEML_PK_2023_artemether_lumefantrine",
}

# DDI pairs (deterministic, real interactions). Each entry:
#   (a_generic, b_generic_or_current_med_keyword, light, code, reason, citation)
_DDI_RULES: list[tuple[str, str, str, str, str, str | None]] = [
    ("rifampicin", "oral contraceptive", "RED",
     "ddi_rifampicin_ocp",
     "Rifampicin induces CYP3A4 and reduces plasma levels of hormonal contraceptives; contraceptive failure risk.",
     # PATCH 1a: real chunk id (Gazette rifampicin entry).
     "NEML_PK_2023_tb_medicines"),
    ("aspirin", "warfarin", "RED",
     "ddi_aspirin_warfarin",
     "Aspirin + warfarin markedly increases bleeding risk (additive antiplatelet + anticoagulant).",
     None),
    ("ibuprofen", "lisinopril", "YELLOW",
     "ddi_nsaid_acei",
     "NSAID + ACE inhibitor: renal function risk in dehydrated / elderly patients.",
     None),
    ("ibuprofen", "ramipril", "YELLOW",
     "ddi_nsaid_acei",
     "NSAID + ACE inhibitor: renal function risk in dehydrated / elderly patients.",
     None),
    ("metronidazole", "alcohol", "YELLOW",
     "ddi_metronidazole_alcohol",
     "Counselling note: avoid alcohol during metronidazole and 48 h after (disulfiram-like reaction).",
     None),
]


def _current_meds_text(patient: dict[str, Any]) -> str:
    """Flatten patient.current_meds into a lowercased searchable blob."""
    cm = patient.get("current_meds") or []
    if isinstance(cm, str):
        return cm.lower()
    return " | ".join(str(x).lower() for x in cm)


def _entry_generics(entries: list[dict]) -> list[str]:
    return [(e.get("generic") or "").lower() for e in entries]


def _apply_ddi_rules(entries: list[dict], patient: dict[str, Any]) -> dict[int, list[dict]]:
    """Return {entry_index: [flag_dict, ...]} of DDI flags."""
    flags: dict[int, list[dict]] = {}
    generics = _entry_generics(entries)
    context = _current_meds_text(patient)

    def _add(i: int, flag: dict) -> None:
        flags.setdefault(i, []).append(flag)

    for a_gen, b_gen, light, code, reason, citation in _DDI_RULES:
        a_gen_l = a_gen.lower()
        b_gen_l = b_gen.lower()
        a_idx = next((i for i, g in enumerate(generics) if g == a_gen_l), None)
        b_idx = next((i for i, g in enumerate(generics) if g == b_gen_l), None)
        b_in_context = b_gen_l in context

        if a_idx is not None and (b_idx is not None or b_in_context):
            flag = {"code": code, "reason": reason, "citation": citation,
                    "severity": light}
            _add(a_idx, flag)
            if b_idx is not None:
                _add(b_idx, flag)

    # Azithromycin stewardship — YELLOW unless indication mentions XDR typhoid.
    for i, g in enumerate(generics):
        if g == "azithromycin":
            ind = (patient.get("indication") or "").lower()
            if "xdr" not in ind and "typhoid" not in ind:
                _add(i, {
                    "code": "azithromycin_stewardship",
                    "reason": "NIH advisory: reserve azithromycin for lab-confirmed XDR typhoid; overuse drives resistance.",
                    "citation": "NIH_XDR_typhoid_2024_stewardship",
                    "severity": "YELLOW",
                })
    return flags


def _dose_flag(entry: dict, extraction_text: str) -> dict | None:
    generic = (entry.get("generic") or "").lower()
    mg = parse_strength_mg(extraction_text)
    if mg is None:
        return None
    if generic in ("amoxicillin", "amoxicillin-clavulanate") and mg > 1000:
        return {"code": "dose_check",
                "reason": f"Per-dose amoxicillin {mg:.0f} mg exceeds usual adult 500-1000 mg range.",
                "citation": None, "severity": "YELLOW"}
    if generic in ("paracetamol", "paracetamol+caffeine") and mg > 1000:
        return {"code": "dose_check",
                "reason": f"Per-dose paracetamol {mg:.0f} mg exceeds usual 500-1000 mg adult dose.",
                "citation": None, "severity": "YELLOW"}
    return None


def _combine_light(flags: list[dict]) -> str:
    if any(f.get("severity") == "RED" for f in flags):
        return "RED"
    if any(f.get("severity") == "YELLOW" for f in flags):
        return "YELLOW"
    return "GREEN"


# ----------------------------------------------------------------------
# Public entrypoint
# ----------------------------------------------------------------------

def check_medicines(extracted: list[Any], patient: dict[str, Any]) -> list[dict]:
    """Deterministic safety checks over an extracted medicine list.

    `extracted` is either a list of strings ("Panadol 500mg") or list of
    {"name": str, "strength": str|None} dicts. `patient` carries flags such
    as {"pregnant": bool, "age": int, "current_meds": [...], "indication": str}.
    Returns per-medicine dicts with light, generic, class, and flags.
    """
    # Normalise input into (raw_name, extraction_text, resolved_entry)
    normalised: list[tuple[str, str, dict]] = []
    for item in extracted or []:
        if isinstance(item, dict):
            name = item.get("name") or ""
            strength = item.get("strength") or ""
            raw_text = f"{name} {strength}".strip()
        else:
            name = str(item)
            raw_text = name
        entry = resolve(name)
        normalised.append((name, raw_text, entry))

    # Duplicate-class / duplicate-generic detection
    class_counts: dict[str, list[int]] = {}
    generic_counts: dict[str, list[int]] = {}
    for i, (_, _, e) in enumerate(normalised):
        cls = (e.get("drug_class") or "").lower()
        gen = (e.get("generic") or "").lower()
        if cls and cls != "unrecognized":
            class_counts.setdefault(cls, []).append(i)
        if gen:
            generic_counts.setdefault(gen, []).append(i)

    dup_class_idx: set[int] = set()
    dup_class_pairs: dict[int, str] = {}
    dup_class_has_dx_peer: set[int] = set()
    for cls, idxs in class_counts.items():
        if len(idxs) > 1:
            # Pregnancy escalation: if ANY peer in this class is category D/X
            # and the patient is pregnant, every member's duplicate_class flag
            # is RED — either drug could end up dispensed and reach the fetus.
            peer_dx = any(
                (normalised[j][2].get("pregnancy_category") or "").upper() in ("D", "X")
                for j in idxs
            )
            for i in idxs:
                dup_class_idx.add(i)
                dup_class_pairs[i] = cls
                if peer_dx:
                    dup_class_has_dx_peer.add(i)
    dup_gen_idx: set[int] = set()
    for gen, idxs in generic_counts.items():
        if len(idxs) > 1:
            for i in idxs:
                dup_gen_idx.add(i)

    # DDI flags (indexed)
    ddi_flags = _apply_ddi_rules([e for _, _, e in normalised], patient)

    pregnant = bool(patient.get("pregnant"))

    out: list[dict] = []
    for i, (name, raw, entry) in enumerate(normalised):
        flags: list[dict] = []

        # a) Unrecognized
        if entry.get("drug_class") == "unrecognized":
            flags.append({
                "code": "unrecognized_review",
                "reason": f"Medicine '{name}' could not be resolved to a known generic — paramedic review.",
                "citation": None,
                "severity": "YELLOW",
            })

        # b) Duplicate generic (same drug, two brands) -> RED
        if i in dup_gen_idx:
            flags.append({
                "code": "duplicate_generic",
                "reason": f"Duplicate generic '{entry.get('generic')}' — same drug appears under two brands.",
                "citation": None,
                "severity": "RED",
            })
        # b) Duplicate class (two NSAIDs, two antibiotics ...) -> YELLOW,
        #    escalated to RED for a pregnant patient when any class-peer is D/X.
        elif i in dup_class_idx:
            escalate = pregnant and i in dup_class_has_dx_peer
            flags.append({
                "code": "duplicate_class",
                "reason": (f"Duplicate {dup_class_pairs[i]} class — two agents from the same class."
                           + (" One peer is pregnancy category D/X."
                              if escalate else "")),
                "citation": None,
                "severity": "RED" if escalate else "YELLOW",
            })

        # c) Pregnancy checks
        if pregnant:
            gen_l = (entry.get("generic") or "").lower()
            cat = (entry.get("pregnancy_category") or "").upper()
            ind = (patient.get("indication") or "").lower()

            # PATCH 1b: INDICATION-AWARE artemether-lumefantrine handling.
            # The WHO malaria treatment guideline (2022 update, on the
            # strength of a 2021 Lancet Infectious Diseases meta-analysis)
            # recommends AL for falciparum malaria in ALL trimesters —
            # untreated malaria in pregnancy is the greater risk to mother
            # AND fetus. The NEML first-trimester caution is legacy EML
            # language. Our corpus holds BOTH real documents: the flags cite
            # the appropriate one and ask the paramedic to verify the
            # indication. NEVER a blanket RED that would block a
            # life-saving treatment.
            if gen_l == "artemether+lumefantrine":
                if "malaria" in ind or "falciparum" in ind:
                    flags.append({
                        "code": "pregnancy_indication_ok",
                        "reason": ("Antimalarial with documented malaria indication — "
                                   "WHO 2023 recommends artemether-lumefantrine in all "
                                   "trimesters; untreated malaria is the greater risk. "
                                   "The NEML carries a first-trimester caution (legacy "
                                   "EML text)."),
                        "citation": "WHO_MAL_pregnancy_AL",
                        "severity": "YELLOW",
                    })
                else:
                    flags.append({
                        "code": "pregnancy_indication_review",
                        "reason": ("Antimalarial in a pregnant patient with no malaria "
                                   "indication documented — verify indication and "
                                   "trimester before dispensing. WHO 2023 permits AL "
                                   "in all trimesters for falciparum malaria; the NEML "
                                   "lists a first-trimester caution."),
                        "citation": "NEML_PK_2023_artemether_lumefantrine",
                        "severity": "YELLOW",
                    })
            elif cat in ("D", "X"):
                flags.append({
                    "code": "contraindicated_pregnancy",
                    "reason": (f"Pregnancy category {cat}: fetal risk — do NOT dispense as-is."),
                    "citation": _PREG_CITATION.get(gen_l),
                    "severity": "RED",
                })
            elif cat == "C" and entry.get("notes"):
                # Other category-C drugs with drug-specific notes
                # (e.g. nimesulide hepatotoxicity). Bare "C" alone is not a flag.
                flags.append({
                    "code": "pregnancy_caution",
                    "reason": f"Pregnancy category C — {entry.get('notes')}.",
                    "citation": _PREG_CITATION.get(gen_l),
                    "severity": "YELLOW",
                })

        # d) DDIs (added by _apply_ddi_rules)
        for f in ddi_flags.get(i, []):
            flags.append(f)

        # e) Dose sanity
        dose = _dose_flag(entry, raw)
        if dose:
            flags.append(dose)

        # f) Antibiotic review — GENERAL advisory when antibiotics show up
        cls = (entry.get("drug_class") or "").lower()
        if "antibiotic" in cls and not any(f["code"] == "azithromycin_stewardship"
                                           for f in flags):
            # Only add if there isn't already a stronger flag on this med
            if not any(f["severity"] == "RED" for f in flags):
                flags.append({
                    "code": "antibiotic_review",
                    "reason": "Antibiotic stewardship: confirm indication and shortest effective course.",
                    "citation": None,
                    "severity": "YELLOW",
                })

        out.append({
            "name": name,
            "generic": entry.get("generic"),
            "class": entry.get("drug_class"),
            "matched_brand": entry.get("matched_brand"),
            "pregnancy_category": entry.get("pregnancy_category"),
            "light": _combine_light(flags),
            "flags": flags,
        })

    # PATCH 2 — citation gate: every flag citation must resolve to a real
    # corpus chunk (same law as the reasoning agent's gate). Dangling
    # citations are stripped; the flag and its reason remain, and the
    # paramedic still sees them.
    from . import corpus
    valid_ids = set(corpus.get_chunk_map().keys())
    for row in out:
        for f in row["flags"]:
            if f.get("citation") is not None and f["citation"] not in valid_ids:
                f["citation"] = None
    return out