"""Confirmed illness location/date and reviewed district/month context.

The shipped catalogue is empty/pending. Nothing here infers a district from a
village, invents local disease rates, changes urgency or assigns a diagnosis.
"""
from datetime import date
import hashlib
import json
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .clinical import ROOT
from .timeutils import local_today


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class FeverContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    country: str = Field(default="", max_length=120)
    province: str = Field(default="", max_length=120)
    district: str = Field(default="", max_length=120)
    observed_on: date
    unknown_reason: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=1500)

    @model_validator(mode="after")
    def confirmed_context(self):
        if self.observed_on > local_today():
            raise ValueError("The illness context date cannot be in the future.")
        if (not self.country or not self.province or not self.district) and not self.unknown_reason:
            raise ValueError("Record country/province/district or explain why the illness location is unknown.")
        return self


class ContextEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100)
    country: str = Field(min_length=1, max_length=120)
    province: str = Field(min_length=1, max_length=120)
    district: str = Field(min_length=1, max_length=120)
    months: list[Annotated[int, Field(strict=True, ge=1, le=12)]] = Field(min_length=1, max_length=12)
    valid_from: date
    valid_through: date
    condition: Literal["malaria", "dengue", "typhoid"]
    context: str = Field(min_length=1, max_length=1500)
    reviewed_by: str = Field(min_length=1, max_length=200)
    reviewed_on: date
    source: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def reviewed_context(self):
        if any(type(month) is not int or not 1 <= month <= 12 for month in self.months) or len(set(self.months)) != len(self.months):
            raise ValueError("Context months must be unique integers from 1 to 12.")
        if self.valid_from > self.valid_through or self.reviewed_on > local_today():
            raise ValueError("Context validity dates or review date are invalid.")
        return self


def catalogue_snapshot() -> tuple[dict, str]:
    try:
        raw = (ROOT / "config/fever_context.json").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        table = json.loads(raw)
        return (table if isinstance(table, dict) else {"review_status": "invalid"}), digest
    except (OSError, ValueError) as exc:
        return {"review_status": "invalid"}, "unavailable:" + type(exc).__name__


def catalogue_hash() -> str:
    return catalogue_snapshot()[1]


def context_for(facts: list[dict], *, snapshot: tuple[dict, str] | None = None) -> dict:
    table, digest = snapshot if snapshot is not None else catalogue_snapshot()
    confirmed = {f["key"]: f for f in facts if f.get("confirmed")}
    fever, location = confirmed.get("fever"), confirmed.get("fever_context")
    result = {"status": "NOT_APPLICABLE", "catalogue_hash": digest, "version": table.get("version", "unavailable"),
              "fact_ids": [f["id"] for f in (fever, location) if f], "location": None, "entries": [],
              "scope": "Reviewed local context for doctor interpretation; no diagnostic probability or automatic urgency change."}
    if not fever or fever["value"] is not True:
        return result
    if not location or location["value"] is None:
        return {**result, "status": "INPUT_REQUIRED"}
    try:
        context = FeverContext.model_validate(location["value"])
    except ValueError:
        return {**result, "status": "INPUT_INVALID"}
    result["location"] = context.model_dump(mode="json")
    if not context.country or not context.province or not context.district:
        return {**result, "status": "LOCATION_UNKNOWN"}
    if table.get("review_status") != "reviewed" or not table.get("entries"):
        return {**result, "status": "CONFIG_INVALID" if table.get("review_status") == "invalid" else "UNAVAILABLE_PENDING_REVIEW"}
    try:
        if not isinstance(table.get("version"), str) or not table["version"].strip():
            raise ValueError("Reviewed context requires a catalogue version.")
        entries = [ContextEntry.model_validate(row) for row in table["entries"]]
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("Context entry IDs must be unique.")
    except (ValueError, TypeError):
        return {**result, "status": "CONFIG_INVALID"}
    matches = [entry for entry in entries if _key(entry.country) == _key(context.country) and _key(entry.province) == _key(context.province)
               and _key(entry.district) == _key(context.district) and context.observed_on.month in entry.months
               and entry.valid_from <= context.observed_on <= entry.valid_through]
    if not matches:
        return {**result, "status": "NO_REVIEWED_MATCH"}
    if len({entry.condition for entry in matches}) != len(matches):
        return {**result, "status": "AMBIGUOUS_REVIEWED_CONTEXT"}
    return {**result, "status": "REVIEWED_CONTEXT", "entries": [entry.model_dump(mode="json") for entry in matches]}
