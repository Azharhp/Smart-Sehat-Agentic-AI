"""Clinical corpus loader + keyword retrieval.

Day 2: no embeddings, no vector DB, no new deps. Plain keyword-overlap
retrieval over a small hand-curated corpus (`corpus/chunks.json`). Include
English and roman-Urdu variants so voice-derived symptoms match too.
"""
from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


CORPUS_PATH = Path(__file__).resolve().parent.parent / "corpus" / "chunks.json"


# Bilingual keyword expansion (English <-> roman-Urdu).
# Each entry: patient-side term -> corpus-side synonyms to also match on.
KEYWORD_ALIASES: dict[str, list[str]] = {
    "cough": ["cough", "khansi"],
    "khansi": ["cough", "khansi"],
    "fever": ["fever", "bukhar"],
    "bukhar": ["fever", "bukhar"],
    "night_sweats": ["night sweats", "raat pasina", "pasina"],
    "night sweats": ["night sweats", "raat pasina"],
    "weight_loss": ["weight loss", "wazan"],
    "weight loss": ["weight loss", "wazan"],
    "streak_hemoptysis": ["hemoptysis", "khoon", "balgham"],
    "hemoptysis": ["hemoptysis", "khoon"],
    "chest_pain": ["chest pain", "seene mein dard", "seene", "dard"],
    "chest pain": ["chest pain", "seene mein dard"],
    "breathlessness": ["breathlessness", "saans"],
    "pneumonia": ["pneumonia", "chest indrawing", "fast breathing"],
    "malaria": ["malaria", "falciparum", "vivax"],
    "tb": ["tuberculosis", "TB", "hemoptysis", "sputum"],
    "tuberculosis": ["tuberculosis", "TB"],
    "child": ["children", "child", "infants", "paediatric", "pediatric"],
    "pregnancy": ["pregnancy", "pregnant"],
    "cavitation": ["cavitation", "tuberculosis", "TB"],
    "opacity": ["pneumonia", "opacit"],
}


_TOKEN_RE = re.compile(r"[A-Za-z]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) > 2}


@lru_cache(maxsize=1)
def load_chunks() -> list[dict]:
    if not CORPUS_PATH.exists():
        return []
    with open(CORPUS_PATH, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    for c in chunks:
        c["_tokens"] = _tokens(c.get("text", ""))
    return chunks


def get_chunk_map() -> dict[str, dict]:
    return {c["id"]: {k: v for k, v in c.items() if k != "_tokens"} for c in load_chunks()}


def _expand_patient_terms(state: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for sym in (state.get("symptoms") or []):
        key = sym.lower().strip()
        for alias in KEYWORD_ALIASES.get(key, [key]):
            terms.update(_tokens(alias))
    for field in ("chief_complaint", "transcript_confirmed", "transcript"):
        val = state.get(field)
        if val:
            terms.update(_tokens(val))
    findings = state.get("imaging_findings") or {}
    if isinstance(findings, dict):
        for f in findings.get("findings") or []:
            for k in ("pattern", "location"):
                v = f.get(k) if isinstance(f, dict) else None
                if v:
                    terms.update(_tokens(v))
                    for alias in KEYWORD_ALIASES.get(v.lower(), []):
                        terms.update(_tokens(alias))
        interp = findings.get("interpretation")
        if interp:
            terms.update(_tokens(interp))
    tl = state.get("triage_level")
    if tl:
        terms.add(tl.lower())
    for c in (state.get("chronic_conditions") or []):
        terms.update(_tokens(c))
    return terms


def retrieve(state: dict[str, Any], top_k: int = 6) -> list[dict]:
    """Score chunks by keyword-overlap with the patient context."""
    chunks = load_chunks()
    if not chunks:
        return []
    query = _expand_patient_terms(state)
    if not query:
        # No patient context yet — return the first top_k chunks so the LLM
        # still sees something to ground on.
        return [{k: v for k, v in c.items() if k != "_tokens"} for c in chunks[:top_k]]
    scored: list[tuple[int, dict]] = []
    for c in chunks:
        overlap = len(query & c["_tokens"])
        if overlap > 0:
            scored.append((overlap, c))
    scored.sort(key=lambda t: t[0], reverse=True)
    # Small corpus — err toward including more.
    picked = [c for _, c in scored[:top_k]]
    if len(picked) < top_k:
        for c in chunks:
            if c not in picked:
                picked.append(c)
            if len(picked) >= top_k:
                break
    return [{k: v for k, v in c.items() if k != "_tokens"} for c in picked]
