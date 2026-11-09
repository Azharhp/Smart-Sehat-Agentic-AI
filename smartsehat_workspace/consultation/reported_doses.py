"""Worker-confirmed intake amounts, bound to exact reconciled medicine rows."""
import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .dosing import PrescriptionProduct, PrescriptionRegimen


def record_hash(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class ReportedDose(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    record_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    medicine: str = Field(min_length=1, max_length=120)
    dose_mg: float | None = Field(default=None, gt=0, strict=True)
    product: PrescriptionProduct
    regimen: PrescriptionRegimen
    unknown_reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def unknown_amount(self):
        if (self.dose_mg is None or self.regimen.doses_per_day is None or self.regimen.duration_days is None) and not self.unknown_reason:
            raise ValueError("Give a reason when the reported mg amount, daily count or duration cannot be established.")
        return self
