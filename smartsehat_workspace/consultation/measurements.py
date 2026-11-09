"""Typed doctor-task measurements, retaining entry units and collection method."""
from __future__ import annotations
import math
from .clinical import Age, config, validate_measurement


def task_measurement(key: str, answer, age: Age) -> dict:
    raw, unit, method, duration = answer, None, "manual-device-entry", None
    if isinstance(answer, dict):
        if set(answer) - {"value", "unit", "method", "duration_seconds"}:
            raise ValueError("Return a measurement value, unit and supported collection method.")
        raw, unit = answer.get("value"), answer.get("unit")
        method, duration = answer.get("method", method), answer.get("duration_seconds")
    if method not in ("manual-device-entry", "full-minute-tap-counter"):
        raise ValueError("Choose manual entry or a completed full-minute breath count.")
    if method == "full-minute-tap-counter":
        if key != "resp_rate" or type(duration) not in (int, float) or duration != 60:
            raise ValueError("A breath-counter result requires a completed 60-second respiratory count.")
        try:
            count = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("A breath count must be a whole number.") from exc
        if type(raw) is bool or not math.isfinite(count) or not count.is_integer():
            raise ValueError("A breath count must be a whole number.")
    elif duration is not None:
        raise ValueError("A manual measurement cannot claim a timed-counter duration.")
    checked = validate_measurement(key, raw, age, unit)
    if not checked["ok"]:
        return checked
    recorded = {"value": checked["value"], "unit": checked["unit"], "entered_value": raw,
                "entered_unit": unit if unit is not None else config()["measurements"][key]["unit"], "method": method}
    if duration is not None:
        recorded["duration_seconds"] = duration
    return {**checked, "recorded": recorded,
            "quote": "Worker confirmed a completed 60-second tap count." if method == "full-minute-tap-counter" else "Worker confirmed a manually entered measurement."}


def completed_breath_count(result, scope: str) -> bool:
    return isinstance(result, dict) and result.get("case_id") == scope and result.get("complete") is True and result.get("duration_seconds") == 60 and type(result.get("count")) is int
