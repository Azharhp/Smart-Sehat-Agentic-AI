"""Compatibility facade for the shared, limited medication review engine.

The full report carries missing-data coverage and workflow status. New callers
should use review_medicines; check_medicines retains the historical row-list API.
"""
from __future__ import annotations

import math
import re
from typing import Any

from .medication_review import resolve, review_medicines


def parse_strength_mg(text: str) -> float | None:
    """Convert an unambiguous single mass for display, never for administered dose.

    Concentrations (500 mg/5 ml), combination strengths, free text and volumes
    are deliberately unsupported. The rules never call this conversion.
    """
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mg|g|mcg)\s*", text, re.IGNORECASE)
    if match is None:
        return None
    value = float(match.group(1)) * {"mg": 1, "g": 1000, "mcg": 0.001}[match.group(2).lower()]
    return value if math.isfinite(value) and value > 0 else None


def check_medicines(extracted: list[Any], patient: dict[str, Any]) -> list[dict]:
    """Historical per-row view; colors never represent clinical approval."""
    return review_medicines(extracted, patient)["medicines"]
