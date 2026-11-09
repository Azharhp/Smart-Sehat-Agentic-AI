"""Configuration-driven prototype rules. All thresholds await clinical review."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
import math
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
LEVELS = {"GREEN": 0, "AMBER": 1, "RED": 2}


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((ROOT / "config/consultation.yaml").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Age:
    value: float
    unit: str
    days: int
    display: str
    warning: str = ""
    date_of_birth: str | None = None
    as_of: str | None = None

    @classmethod
    def from_input(cls, value: float, unit: str) -> "Age":
        c = config()["age"]
        factors = {"days": 1, "weeks": 7, "months": c["days_per_month"], "years": c["days_per_year"]}
        if type(value) is bool or unit not in factors or not math.isfinite(value) or value < 0:
            raise ValueError("Enter a non-negative age and a valid unit.")
        days = round(value * factors[unit])
        months = int(value) if unit == "months" else round(days / c["days_per_month"])
        display = f"{value:g} {unit} = {months // 12} years {months % 12} months ({days} days)"
        warning = "Confirm the age and unit." if (unit == "months" and value > c["month_entry_warning_above"]) or (unit == "years" and (value < c["year_entry_warning_below"] or value > c["year_entry_warning_above"])) else ""
        return cls(value, unit, days, display, warning)

    @classmethod
    def from_dob(cls, dob: date, today: date | None = None) -> "Age":
        from .timeutils import local_today
        reference = today if today is not None else local_today()
        if type(dob) is not date or type(reference) is not date or dob > reference:
            raise ValueError("Use a valid birth date on or before the age reference date.")
        return replace(cls.from_input((reference - dob).days, "days"), date_of_birth=dob.isoformat(), as_of=reference.isoformat())

    @classmethod
    def from_record(cls, record: dict) -> "Age":
        if not isinstance(record, dict):
            raise ValueError("Age requires a value, unit and optional birth-date details.")
        value = record.get("value")
        if type(value) not in (int, float):
            raise ValueError("Age requires a numeric value and an explicit unit.")
        age = cls.from_input(value, record.get("unit"))
        if record.get("date_of_birth") is not None or record.get("as_of") is not None:
            try:
                calculated = cls.from_dob(date.fromisoformat(record["date_of_birth"]), date.fromisoformat(record["as_of"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Recorded birth date and age reference date must both be valid.") from exc
            if age.days != calculated.days:
                raise ValueError("Recorded age disagrees with the birth date and reference date.")
            return calculated
        return age

    @property
    def pathway(self) -> str:
        if self.days < config()["age"]["infant_days"]:
            return "young_infant"
        if self.days < config()["age"]["child_days"]:
            return "child"
        if self.days < round(config()["age"]["older_child_years"] * config()["age"]["days_per_year"]):
            return "older_child"
        return "older_adult" if self.days >= config()["thresholds"]["crb_age_days"] else "adult"

    def json(self) -> dict:
        return {**{key: value for key, value in asdict(self).items() if value is not None}, "pathway": self.pathway}


def danger_questions(age: Age, pregnant: bool) -> list[dict]:
    group = "child" if age.pathway in ("young_infant", "child", "older_child") else "adult"
    questions = list(config()["danger_signs"][group])
    if pregnant:
        questions += config()["danger_signs"]["maternal"]
    return list({q["key"]: q for q in questions}.values())


def respiratory_threshold(age: Age) -> int | None:
    thresholds = config()["thresholds"]
    if age.pathway == "young_infant":
        return thresholds["rr_infant"]
    if age.pathway == "child":
        return thresholds["rr_2_11_months"] if age.days < round(12 * config()["age"]["days_per_month"]) else thresholds["rr_12_59_months"]
    return thresholds["rr_adult"] if age.pathway in ("adult", "older_adult") else None


def validate_measurement(key: str, value: Any, age: Age, unit: str | None = None) -> dict:
    spec = config()["measurements"].get(key)
    if not spec:
        raise ValueError("Unknown measurement.")
    if type(value) is bool:
        return {"ok": False, "error": "A measurement must be numeric, not yes/no."}
    aliases = {"°C": "C", "°F": "F", "percent": "%", "bpm": "/min", "breaths/min": "/min", "breaths/minute": "/min"}
    unit = aliases.get(unit, unit)
    allowed_units = ("C", "F") if key == "temperature" else (spec["unit"],)
    if unit is not None and unit not in allowed_units:
        return {"ok": False, "error": f"Unit does not match {key}. Expected {' or '.join(allowed_units)}."}
    if value is None or str(value).strip() in ("", "0"):
        return {"ok": False, "missing": True, "error": "Not done: a reason is required."}
    try:
        number = float(value)
    except (ValueError, TypeError):
        return {"ok": False, "error": "Enter a number."}
    if key == "temperature" and unit == "F":
        number = (number - 32) * 5 / 9
    if not math.isfinite(number) or not spec["min"] <= number <= spec["max"]:
        return {"ok": False, "error": f"Expected {spec['min']}–{spec['max']} {spec['unit']}. Recheck the device and unit."}
    if key == "weight":
        for band in config()["weight_bands"]:
            if age.days < band["under_days"]:
                if number > band["max"]:
                    return {"ok": False, "error": "Weight is implausible for this age. Recheck age, unit and measurement."}
                break
    return {"ok": True, "value": round(number, 2), "unit": spec["unit"]}


def evaluate(facts: list[dict], age: Age, pregnant: bool = False, ai_level: str = "GREEN") -> dict:
    current = {f["key"]: f for f in facts if f.get("confirmed")}
    values = {k: f["value"] for k, f in current.items()}
    t = config()["thresholds"]
    fired: list[dict] = []
    level = "GREEN"

    def fire(rule: str, severity: str, text: str, keys: list[str], layer: str = "pathway") -> None:
        nonlocal level
        if LEVELS[severity] > LEVELS[level]:
            level = severity
        fired.append({"rule_id": rule, "level": severity, "layer": layer, "text": text,
                      "fact_ids": [current[k]["id"] for k in keys if k in current]})

    for q in danger_questions(age, pregnant):
        if values.get(q["key"]) is True:
            fire(q["id"], "RED", q["en"], [q["key"]], "danger")
    respiratory = any(values.get(k) is True for k in ("cough", "breathlessness", "wheeze"))
    rr, spo2 = values.get("resp_rate"), values.get("spo2")
    if spo2 is not None and spo2 < t["spo2_red"]:
        fire("SPO2-01", "RED", "Oxygen saturation below configured threshold", ["spo2"], "vitals")
    if age.pathway == "young_infant":
        if any(values.get(k) is True for k in ("cough", "fever", "breathlessness", "diarrhoea", "illness")):
            fire("ILL-INF-01", "AMBER", "Illness in a young infant", ["age", "cough", "fever", "illness"])
        if rr is not None and rr >= t["rr_infant"]:
            fire("RR-INF-01", "RED", "Fast breathing in a young infant", ["age", "resp_rate"])
        temp = values.get("temperature")
        if temp is not None and (temp >= t["infant_fever"] or temp < t["infant_low_temperature"]):
            fire("TEMP-INF-01", "RED", "Abnormal temperature in a young infant", ["age", "temperature"])
    elif age.pathway == "child":
        threshold = t["rr_2_11_months"] if age.days < round(12 * config()["age"]["days_per_month"]) else t["rr_12_59_months"]
        if rr is not None and rr >= threshold:
            fire("RR-CH-01" if threshold == t["rr_2_11_months"] else "RR-CH-02", "AMBER", "Fast breathing for age", ["age", "resp_rate"])
    if values.get("chest_indrawing") is True or values.get("stridor") is True:
        fire("CI-CH-01", t["child_indrawing_level"], "Chest indrawing or stridor when calm", ["chest_indrawing", "stridor"])
    if respiratory and age.pathway in ("adult", "older_adult"):
        score = sum([values.get("confusion") is True, rr is not None and rr >= t["rr_adult"],
                     (values.get("systolic") is not None and values["systolic"] < t["crb_systolic"]) or
                     (values.get("diastolic") is not None and values["diastolic"] <= t["crb_diastolic"]),
                     age.days >= t["crb_age_days"]])
        if score:
            fire("CRB-AD-01", "RED" if score >= t["crb_red_score"] else "AMBER", f"CRB-65 score {score}", ["age", "confusion", "resp_rate", "systolic", "diastolic"])
    if (values.get("cough_days") or 0) >= t["tb_cough_days"] and any(values.get(k) is True for k in ("weight_loss", "night_sweats", "haemoptysis", "tb_contact", "diabetes")):
        fire("TB-01", "AMBER", "TB screen: doctor review for sputum testing", ["cough_days", "weight_loss", "night_sweats", "haemoptysis", "tb_contact", "diabetes"])
    if (values.get("fever_days") or 0) > t["fever_days"] or any(values.get(k) is True for k in ("neck_stiffness", "rash", "bleeding", "persistent_vomiting", "abdominal_pain")):
        fire("FEV-01", "RED" if values.get("neck_stiffness") is True or values.get("bleeding") is True else "AMBER", "Fever pathway warning", ["fever_days", "neck_stiffness", "rash", "bleeding", "persistent_vomiting", "abdominal_pain"])
    if t["muac_min_age_days"] <= age.days < config()["age"]["child_days"] and values.get("muac") is not None and values["muac"] < t["muac_red_flag"]:
        fire("MUAC-01", "AMBER", "Low MUAC: nutrition review required", ["age", "muac"])
    if values.get("diarrhoea") is True and ((values.get("diarrhoea_days") or 0) >= t["diarrhoea_days"] or values.get("blood_in_stool") is True or values.get("dehydration") is True):
        fire("DIARRHOEA-01", "AMBER", "Diarrhoea warning: doctor review required", ["diarrhoea", "diarrhoea_days", "blood_in_stool", "dehydration"])
    if values.get("symptom_course") == "worsening" or values.get("symptom_severity") == "severe":
        fire("COURSE-01", "AMBER", "Patient reports worsening or severe symptoms; doctor review required", ["symptom_course", "symptom_severity"])
    malaria = values.get("malaria_rdt")
    if isinstance(malaria, dict) and malaria.get("result") == "positive":
        fire("POC-01", "AMBER", "Positive recorded malaria test: doctor interpretation required", ["malaria_rdt"])
    if values.get("followup_danger") is True:
        fire("FU-DANGER", "RED", "New danger sign at follow-up", ["followup_danger"], "followup")
    if values.get("followup_worse") is True:
        fire("FU-WORSE", "AMBER", "Worsening at follow-up: reassessment required", ["followup_worse"], "followup")
    required = [q["key"] for q in danger_questions(age, pregnant)]
    if respiratory:
        required += ["spo2", "resp_rate"]
        if age.pathway in ("young_infant", "child", "older_child"):
            required += ["weight"]
        if age.pathway in ("adult", "older_adult"):
            required += ["systolic", "diastolic"]
    missing = [k for k in required if values.get(k) is None]
    if missing:
        fire("MISSING-01", "AMBER", "Critical information unavailable: " + ", ".join(missing), missing, "missing")
    if ai_level in LEVELS and LEVELS[ai_level] > LEVELS[level]:
        fire("AI-CONCERN-01", ai_level, "AI concern raised urgency; doctor review required", [], "ai")
    return {"level": level, "fired": fired, "missing_critical": missing, "version": config()["version"], "review_status": "pending"}
