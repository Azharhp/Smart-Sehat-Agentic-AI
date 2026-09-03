"""SQLite append-only ledger for SmartSehat.

Bug-fixed version:
- update_case: `path` is now keyword-only (positional-arg footgun removed)
- get_hitl_decisions: new read helper (used by test_flow and, on Day 2+,
  by the Case Inspector timeline view)
- events table remains APPEND-ONLY — no updates or deletes anywhere
"""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.environ.get("SMARTSEHAT_DB", "smartsehat.db")


def _connect(path: str | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    role TEXT,
    bhu_id TEXT
);
CREATE TABLE IF NOT EXISTS facilities (
    bhu_id TEXT PRIMARY KEY,
    name TEXT,
    district TEXT
);
CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY,
    name TEXT,
    age INTEGER,
    sex TEXT,
    chronic_conditions_json TEXT,
    allergies_json TEXT
);
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    patient_id TEXT,
    paramedic_id TEXT,
    bhu_id TEXT,
    started_at TEXT,
    completed_at TEXT,
    triage_level TEXT,
    final_diagnosis TEXT,
    outcome TEXT,
    hitl_outcome TEXT
);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT,
    event_type TEXT,
    payload_json TEXT,
    provider_used TEXT,
    confidence REAL,
    timestamp TEXT
);
CREATE TABLE IF NOT EXISTS hitl_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT,
    gate_type TEXT,
    action TEXT,
    original_json TEXT,
    final_json TEXT,
    reason_given TEXT,
    ai_confidence REAL,
    timestamp TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: str | None = None) -> None:
    conn = _connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def log_event(case_id: str, event_type: str, payload: dict, provider: str | None = None,
              confidence: float | None = None, path: str | None = None) -> int:
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO events (case_id, event_type, payload_json, provider_used, confidence, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (case_id, event_type, json.dumps(payload, ensure_ascii=False), provider, confidence, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def log_hitl_decision(case_id: str, gate_type: str, action: str, original: dict,
                      final: dict | None = None, reason: str | None = None,
                      ai_confidence: float | None = None, path: str | None = None) -> int:
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO hitl_decisions (case_id, gate_type, action, original_json, final_json, reason_given, ai_confidence, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, gate_type, action, json.dumps(original, ensure_ascii=False),
             json.dumps(final, ensure_ascii=False) if final else None,
             reason, ai_confidence, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_case_timeline(case_id: str, path: str | None = None) -> list[dict]:
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE case_id = ? ORDER BY event_id ASC", (case_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_hitl_decisions(case_id: str, path: str | None = None) -> list[dict]:
    """Read helper for the HITL decision ledger (Case Inspector, tests)."""
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM hitl_decisions WHERE case_id = ? ORDER BY decision_id ASC", (case_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_case(case_id: str, patient_id: str, paramedic_id: str, bhu_id: str, path: str | None = None) -> None:
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO cases (case_id, patient_id, paramedic_id, bhu_id, started_at) VALUES (?, ?, ?, ?, ?)",
            (case_id, patient_id, paramedic_id, bhu_id, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def update_case(case_id: str, *, path: str | None = None, **fields) -> None:
    # FIX: `path` is keyword-only — a stray positional string can no longer
    # silently become a database path.
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [case_id]
    conn = _connect(path)
    try:
        conn.execute(f"UPDATE cases SET {cols} WHERE case_id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def get_case(case_id: str, path: str | None = None) -> dict | None:
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_patients(path: str | None = None) -> list[dict]:
    conn = _connect(path)
    try:
        rows = conn.execute("SELECT * FROM patients").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_cases_for_patient(patient_id: str, path: str | None = None) -> list[dict]:
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM cases WHERE patient_id = ? ORDER BY started_at DESC", (patient_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()