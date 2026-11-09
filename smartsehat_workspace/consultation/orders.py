"""Doctor-selected test contracts; no test is selected or interpreted by AI."""
from __future__ import annotations
from datetime import date
from functools import lru_cache
from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml
from .clinical import ROOT
from .timeutils import local_today


@lru_cache(maxsize=1)
def definitions() -> dict:
    return yaml.safe_load((ROOT / "config/test_orders.yaml").read_text(encoding="utf-8"))["tests"]


def selected(local: list[str], lab: list[str]) -> list[dict]:
    result = []
    for location, names in (("local", local), ("lab", lab)):
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError("Select tests from the supported doctor order list.")
        for name in dict.fromkeys(names):
            match = next(((key, spec) for key, spec in definitions().items() if name in (key, spec["label"]) and spec["location"] == location), None)
            if not match:
                raise ValueError("Unsupported test or test location. Use the doctor order selector.")
            key, spec = match
            result.append({"test_id": key, **spec})
    return result


class LabReport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    result: str = Field(min_length=1, max_length=12000)
    laboratory: str = Field(default="Not recorded", max_length=200)
    report_date: date | None = None

    @field_validator("report_date")
    @classmethod
    def not_in_future(cls, value):
        if value is not None and value > local_today():
            raise ValueError("The report date is in the future. Recheck it.")
        return value
