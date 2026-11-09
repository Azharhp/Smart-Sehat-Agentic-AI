"""Match doctor-entered amounts to reviewed product-specific entries.

No clinical ranges are supplied here. The configured table starts empty/pending,
and this helper never calculates or suggests a patient dose.
"""
from datetime import date
import math
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .timeutils import local_today

Formulation = Literal["unknown", "tablet", "capsule", "syrup", "injection", "inhaler", "cream", "other"]
Route = Literal["unknown", "oral", "inhaled", "topical", "intramuscular", "intravenous", "other"]


class PrescriptionProduct(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    formulation: Formulation = "unknown"
    strength: str = Field(default="", max_length=120)
    route: Route = "unknown"


class PrescriptionRegimen(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    doses_per_day: float | None = Field(default=None, gt=0, strict=True)
    duration_days: float | None = Field(default=None, gt=0, strict=True)
    indication: str = Field(default="", max_length=500)
    as_needed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def whole_daily_maximum(self):
        if self.doses_per_day is not None and not self.doses_per_day.is_integer():
            raise ValueError("Enter a whole maximum dose count in any 24 hours, not an average across days.")
        return self


class ReviewedDoseEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=100)
    generic: str = Field(min_length=1, max_length=120)
    formulation: Formulation
    strength: str = Field(min_length=1, max_length=120)
    route: Route
    min_age_days: int = Field(ge=0, strict=True)
    max_age_days: int = Field(ge=0, strict=True)
    min_mg_per_kg_per_dose: float = Field(ge=0, strict=True)
    max_mg_per_kg_per_dose: float = Field(gt=0, strict=True)
    max_mg_per_dose: float | None = Field(default=None, gt=0, strict=True)
    max_mg_per_day: float | None = Field(default=None, gt=0, strict=True)
    max_mg_per_kg_per_day: float | None = Field(default=None, gt=0, strict=True)
    min_doses_per_day: float | None = Field(default=None, gt=0, strict=True)
    max_doses_per_day: float | None = Field(default=None, gt=0, strict=True)
    min_duration_days: float | None = Field(default=None, gt=0, strict=True)
    max_duration_days: float | None = Field(default=None, gt=0, strict=True)
    indication: str | None = Field(default=None, min_length=1, max_length=500)
    reviewed_by: str = Field(min_length=1, max_length=200)
    reviewed_on: date
    source: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def consistent(self):
        if self.formulation == "unknown" or self.route == "unknown" or self.strength.casefold() == "unknown":
            raise ValueError("A reviewed entry requires a specific product and route.")
        if self.max_age_days < self.min_age_days or self.max_mg_per_kg_per_dose < self.min_mg_per_kg_per_dose:
            raise ValueError("Reviewed age and plausibility bounds must be ordered.")
        if self.reviewed_on > local_today():
            raise ValueError("A clinical review date cannot be in the future.")
        for lower, upper in ((self.min_doses_per_day, self.max_doses_per_day), (self.min_duration_days, self.max_duration_days)):
            if (lower is None) != (upper is None) or lower is not None and lower > upper:
                raise ValueError("Reviewed frequency/duration require both ordered bounds.")
        return self


def _key(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def check_amount(table: dict, ingredients: list[str], dose_mg, weight, age, product: dict | None = None, regimen: dict | None = None) -> dict:
    product = PrescriptionProduct.model_validate(product or {})
    regimen = PrescriptionRegimen.model_validate(regimen or {})
    if dose_mg is not None and (type(dose_mg) not in (int, float) or not math.isfinite(dose_mg) or dose_mg <= 0):
        raise ValueError("Doctor-entered amount must be a finite positive number in mg per dose.")
    if weight is not None and (type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0):
        raise ValueError("A confirmed positive finite weight in kg is required.")
    result = {"status": "UNAVAILABLE", "regimen_status": "UNAVAILABLE", "product": product.model_dump(), "regimen": regimen.model_dump(), "checks": {}, "flags": [],
              "scope": "Only explicitly configured reviewed product/age/indication limits are checked. No dose is suggested; unconfigured limits, organ function and full written/schedule instructions require independent doctor verification."}

    def flag(rule, text):
        result["flags"].append({"rule_id": rule, "text": text})

    if table.get("status") != "reviewed" or not table.get("entries"):
        flag("RX-DOSE-PENDING", "Dose plausibility unavailable: table awaits clinical review. Doctor must verify formulation, amount and schedule.")
        return result
    try:
        entries = [ReviewedDoseEntry.model_validate(row) for row in table["entries"]]
        if len({row.id for row in entries}) != len(entries):
            raise ValueError("Repeated reviewed rule ID")
    except (ValueError, TypeError):
        flag("RX-DOSE-CONFIG", "Reviewed dosing entries are incomplete or invalid. Automatic plausibility checking is unavailable.")
        return result
    if product.formulation == "unknown" or product.route == "unknown" or _key(product.strength) in ("", "unknown"):
        result["status"] = "INPUT_REQUIRED"
        flag("RX-PRODUCT", "Record the doctor-selected formulation, printed strength and route before matching a reviewed dosing entry.")
        return result
    # An ingredient inside a combination must not borrow a single-drug band.
    product_matches = [row for row in entries if len(ingredients) == 1 and _key(row.generic) == _key(ingredients[0])
               and row.formulation == product.formulation and row.route == product.route
               and _key(row.strength) == _key(product.strength) and row.min_age_days <= age.days <= row.max_age_days]
    if not regimen.indication and any(row.indication for row in product_matches):
        result["status"] = "INPUT_REQUIRED"
        flag("RX-INDICATION", "Record the doctor's indication before matching this indication-specific reviewed entry.")
        return result
    matches = [row for row in product_matches if row.indication is None or _key(row.indication) == _key(regimen.indication)]
    if len(matches) != 1:
        result["status"] = "NO_MATCH"
        flag("RX-DOSE-NO-MATCH", "No unique reviewed entry matches this ingredient, formulation, printed strength, route, age and configured indication. Doctor verification is required.")
        return result
    row = matches[0]
    result["entry"] = {"id": row.id, "reviewed_by": row.reviewed_by, "reviewed_on": row.reviewed_on.isoformat(), "source": row.source}
    if not weight or dose_mg is None:
        result["status"] = "INPUT_REQUIRED"
        result["checks"]["per_dose"] = "INPUT_REQUIRED"
        flag("RX-DOSE-INPUT", "A confirmed valid weight and doctor-entered mg amount are required for this per-dose plausibility entry.")
    elif not row.min_mg_per_kg_per_dose <= dose_mg / weight <= row.max_mg_per_kg_per_dose:
        result["status"] = "OUTSIDE_REVIEWED_BAND"
        result["checks"]["per_dose"] = "OUTSIDE"
        flag("RX-DOSE-RANGE", "Doctor-entered amount falls outside the matched reviewed per-dose band. Recheck the product and amount.")
    else:
        result["status"] = "WITHIN_REVIEWED_BAND"
        result["checks"]["per_dose"] = "WITHIN"

    def compare(name, value, lower, upper, rule, message):
        if lower is None and upper is None:
            result["checks"][name] = "NOT_CONFIGURED"
        elif value is None:
            result["checks"][name] = "INPUT_REQUIRED"
            flag("RX-REGIMEN-INPUT", "Doctor-entered amount, daily dose count and duration are required for the configured regimen limits.")
        elif lower is not None and value < lower or upper is not None and value > upper:
            result["checks"][name] = "OUTSIDE"
            flag(rule, message)
        else:
            result["checks"][name] = "WITHIN"

    compare("absolute_per_dose", dose_mg, None, row.max_mg_per_dose, "RX-ABSOLUTE-DOSE", "Doctor-entered amount exceeds the reviewed absolute per-dose maximum.")
    compare("frequency", regimen.doses_per_day, row.min_doses_per_day, row.max_doses_per_day, "RX-FREQUENCY", "Doctor-entered daily dose count is outside the reviewed frequency band.")
    compare("duration", regimen.duration_days, row.min_duration_days, row.max_duration_days, "RX-DURATION", "Doctor-entered course length is outside the reviewed duration band.")
    result["checks"]["indication"] = "WITHIN" if row.indication is not None else "NOT_CONFIGURED"
    caps = [cap for cap in (row.max_mg_per_day, row.max_mg_per_kg_per_day * weight if row.max_mg_per_kg_per_day is not None and weight is not None else None) if cap is not None]
    daily = dose_mg * regimen.doses_per_day if dose_mg is not None and regimen.doses_per_day is not None else None
    compare("daily_maximum", daily, None, min(caps) if caps else None, "RX-DAILY-MAX", "Doctor-entered amount multiplied by the daily dose count exceeds a reviewed daily maximum.")
    if row.max_mg_per_kg_per_day is not None and weight is None and result["checks"]["daily_maximum"] != "OUTSIDE":
        result["checks"]["daily_maximum"] = "INPUT_REQUIRED"
        flag("RX-REGIMEN-INPUT", "A confirmed valid weight is required for the configured daily weight-based limit.")
    checks = set(result["checks"].values())
    result["regimen_status"] = "OUTSIDE_REVIEWED_LIMITS" if "OUTSIDE" in checks else "INPUT_REQUIRED" if "INPUT_REQUIRED" in checks else "INCOMPLETE_REVIEWED_CHECKS" if "NOT_CONFIGURED" in checks else "WITHIN_CONFIGURED_LIMITS"
    missing_limits = [name for name, status in result["checks"].items() if status == "NOT_CONFIGURED"]
    if missing_limits:
        flag("RX-REGIMEN-SCOPE", "No reviewed limit configured for: " + ", ".join(missing_limits) + ". Doctor verification is required.")
    return result
