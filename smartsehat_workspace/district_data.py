"""Current district view across the legacy ledger and encrypted consultations."""
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from smartsehat_workspace import audit
from smartsehat_workspace.consultation.clinical import ROOT
from smartsehat_workspace.consultation.store import Store

CASE_COLUMNS = ["case_id", "source", "name", "created", "site", "urgency", "stage", "closed", "diagnosis", "outcome", "doctor_replied", "synthetic", "lat", "lon", "map_location", "illustrative"]
MAP_COLUMNS = ["Location", "Cases", "Consultations", "Legacy cases", "RED cases", "Open cases", "lat", "lon", "illustrative"]
PERIODS = {"All time": None, "Last 30 days": 30, "Last 7 days": 7}


def location_catalogue(path: Path | None = None) -> dict:
    data = json.loads((path or ROOT / "config/dashboard_locations.json").read_text(encoding="utf-8"))
    result = {}
    for entry in data["locations"]:
        lat, lon = float(entry["lat"]), float(entry["lon"])
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("A dashboard location has invalid coordinates.")
        key = entry["match"].strip().casefold()
        if key in result:
            raise ValueError("A dashboard location name is configured twice.")
        result[key] = {**entry, "lat": lat, "lon": lon}
    return result


def load_cases(legacy_path: str | None = None, service: Store | None = None, locations: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Do not cache: a newly saved case must appear on the next refresh."""
    rows = []
    with closing(audit._connect(legacy_path)) as db:
        legacy = db.execute("""SELECT c.*, p.name FROM cases c
            LEFT JOIN patients p ON p.patient_id=c.patient_id ORDER BY c.started_at DESC""").fetchall()
        synthetic_ids = {row[0] for row in db.execute("""SELECT DISTINCT case_id FROM events
            WHERE json_extract(payload_json, '$.synthetic') = 1""")}
        decisions = pd.read_sql_query("SELECT case_id, action FROM hitl_decisions", db)
        for row in legacy:
            closed = bool(row["completed_at"] or row["outcome"])
            rows.append({"case_id": row["case_id"], "source": "Legacy ledger", "name": row["name"] or row["patient_id"] or "Not recorded",
                "created": row["started_at"], "site": row["bhu_id"] or "Location not recorded",
                "urgency": "AMBER" if row["triage_level"] == "YELLOW" else row["triage_level"] or "UNKNOWN",
                "stage": "CLOSED" if closed else "IN PROGRESS", "closed": closed,
                "diagnosis": row["final_diagnosis"] or "", "outcome": row["outcome"] or "",
                "doctor_replied": False, "synthetic": row["case_id"] in synthetic_ids})
    for row in (service or Store()).dashboard_cases():
        rows.append({"case_id": row["case_id"], "source": "Consultation", "name": row["name"], "created": row["created"],
            "site": row["village"] or "Location not recorded", "urgency": row["urgency"], "stage": row["stage"], "closed": row["stage"] == "CLOSED",
            "diagnosis": row["diagnosis"], "outcome": row["outcome"], "doctor_replied": row["doctor_replied"], "synthetic": row["synthetic"]})
    catalogue = locations if locations is not None else location_catalogue()
    for row in rows:
        match = catalogue.get(row["site"].strip().casefold())
        if match and match.get("synthetic_only") and not row["synthetic"]:
            match = None
        row.update({"lat": match["lat"] if match else None, "lon": match["lon"] if match else None,
            "map_location": match["name"] if match else "Location not mapped", "illustrative": match.get("illustrative", False) if match else False})
    frame = pd.DataFrame(rows, columns=CASE_COLUMNS)
    frame["created"] = pd.to_datetime(frame["created"], errors="coerce", utc=True)
    frame = frame.sort_values("created", ascending=False, na_position="last").reset_index(drop=True)
    return frame, decisions


def filter_cases(frame: pd.DataFrame, period: str = "All time", source: str = "All sources", urgency: str = "All urgency levels", query: str = "", at: datetime | None = None) -> pd.DataFrame:
    result = frame.copy()
    days = PERIODS[period]
    if days is not None:
        end = at or datetime.now(timezone.utc)
        if end.tzinfo is None:
            raise ValueError("Dashboard time must include its timezone.")
        result = result[result["created"].between(end - timedelta(days=days), end)]
    if source != "All sources":
        result = result[result["source"] == source]
    if urgency != "All urgency levels":
        result = result[result["urgency"] == urgency]
    if query.strip() and not result.empty:
        text = result[["case_id", "name", "site", "diagnosis", "outcome"]].fillna("").agg(" ".join, axis=1)
        result = result[text.str.contains(query.strip(), case=False, regex=False)]
    return result.copy()


def map_counts(frame: pd.DataFrame) -> pd.DataFrame:
    located = frame.dropna(subset=["lat", "lon"])
    rows = []
    for (location, lat, lon, illustrative), group in located.groupby(["map_location", "lat", "lon", "illustrative"], sort=True):
        rows.append({"Location": location, "Cases": len(group), "Consultations": int((group["source"] == "Consultation").sum()),
            "Legacy cases": int((group["source"] == "Legacy ledger").sum()), "RED cases": int((group["urgency"] == "RED").sum()),
            "Open cases": int((~group["closed"].astype(bool)).sum()), "lat": lat, "lon": lon, "illustrative": illustrative})
    return pd.DataFrame(rows, columns=MAP_COLUMNS)
