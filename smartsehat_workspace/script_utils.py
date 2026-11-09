"""Script normalization utilities.

Qwen3-ASR (our STT fallback) transcribes Urdu speech in Devanagari
script. When the user selected Urdu, we transliterate the Devanagari
output back to Urdu script so the health worker always reads the
transcript in the expected script. The transcript confirmation gate
lets the human correct any letter — this is a starting draft.
"""
from __future__ import annotations
import re


def contains_devanagari(text: str) -> bool:
    """True if any character is in the Devanagari Unicode block."""
    return any("\u0900" <= ch <= "\u097F" for ch in (text or ""))


# Phonetic mapping: Devanagari -> Urdu script.
# NOTE: entries map to Urdu characters; anything not in this dict
# passes through unchanged (Latin, Urdu script, punctuation, spaces).
DEVA_TO_URDU = {
    "अ": "ا", "आ": "آ", "इ": "ا", "ई": "ی", "उ": "ا", "ऊ": "و",
    "ए": "े", "ऐ": "े", "ओ": "و", "औ": "او", "ऑ": "آ",

    "ा": "ا", "ि": "", "ी": "ی", "ु": "", "ू": "و",
    "े": "े", "ै": "े", "ो": "و", "ौ": "او", "ॉ": "ا", "ृ": "र",

    "क": "ک", "ख": "کھ", "ग": "گ", "घ": "گھ", "ङ": "نگ",
    "च": "च", "छ": "چھ", "ज": "ج", "झ": "جھ", "ञ": "ن",
    "ट": "ٹ", "ठ": "टھ", "ड": "ڈ", "ढ": "ڈھ", "ण": "ن",
    "त": "त", "थ": "थ", "द": "د", "ध": "دھ", "न": "न",
    "प": "प", "फ": "फ", "ब": "ب", "भ": "بھ", "म": "م",
    "य": "ی", "र": "र", "ल": "ل", "व": "و", "श": "श",
    "स": "स", "ह": "ह",

    "ज़": "ز", "फ़": "ف", "ख़": "خ", "क़": "क़",
    "ड़": "ड़", "ढ़": "ढ़", "ट़": "ट़", "य़": "य़",

    "ं": "ं", "ँ": "ँ", "ः": "ः", "्": "", "़": "",
    "ऽ": "-", "।": ".",

    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
    "५": "5", "६": "6", "७": "7", "८": "8", "९": "9",
}


def devanagari_to_urdu(text: str) -> str:
    """Phonetic transliteration: Devanagari -> Urdu script.

    Characters without a mapping pass through unchanged (Latin,
    existing Urdu script, punctuation, and spaces are untouched).
    """
    if not text:
        return text
    return re.sub(r"\s+", " ", "".join(
        DEVA_TO_URDU.get(ch, ch) for ch in text)).strip()
