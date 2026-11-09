"""Typed point-of-care results. Range checks are not clinical interpretation."""
from functools import lru_cache
import math
import yaml
from .clinical import ROOT


@lru_cache(maxsize=1)
def tests() -> dict:
    return yaml.safe_load((ROOT / "config/poc_tests.yaml").read_text(encoding="utf-8"))["tests"]


def validate(key: str, value, unit: str = "") -> dict:
    spec = tests().get(key)
    if not spec:
        raise ValueError("Unknown point-of-care test.")
    if spec["kind"] == "choice":
        if value not in spec["choices"]:
            raise ValueError("Choose positive, negative or invalid.")
        return {"result": value, "unit": "", "review_status": "pending"}
    if type(value) is bool or unit not in spec["units"]:
        raise ValueError("Choose a valid measurement unit and enter a number.")
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError("Enter a numeric test result.") from None
    limits = spec["units"][unit]
    if not math.isfinite(number) or not limits["min"] <= number <= limits["max"]:
        raise ValueError(f"Recheck the device and unit. Configured plausible range: {limits['min']}–{limits['max']} {unit}.")
    return {"value": number, "unit": unit, "review_status": "pending"}
