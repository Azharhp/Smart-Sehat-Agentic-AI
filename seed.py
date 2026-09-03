"""Seed the SmartSehat DB with baseline facility/user/patient."""
from __future__ import annotations
import json
import sqlite3

from smartsehat import audit


def _upsert(conn: sqlite3.Connection, sql: str, params: tuple) -> None:
    conn.execute(sql, params)


def seed(path: str | None = None) -> None:
    audit.init_db(path)
    conn = sqlite3.connect(path or audit.DB_PATH, check_same_thread=False)
    try:
        _upsert(conn,
                "INSERT OR REPLACE INTO facilities (bhu_id, name, district) VALUES (?, ?, ?)",
                ("BHU-0442", "Basic Health Unit 0442", "Rajanpur"))
        _upsert(conn,
                "INSERT OR REPLACE INTO users (user_id, name, role, bhu_id) VALUES (?, ?, ?, ?)",
                ("PM-2213", "Farid Khan", "paramedic", "BHU-0442"))
        _upsert(conn,
                "INSERT OR REPLACE INTO patients (patient_id, name, age, sex, chronic_conditions_json, allergies_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("PT-0001", "Rehmat Gul", 42, "M", json.dumps(["diabetes"]), json.dumps([])))
        conn.commit()
    finally:
        conn.close()
    audit.log_event(case_id="SYSTEM", event_type="LOGIN",
                    payload={"user_id": "PM-2213", "bhu_id": "BHU-0442"},
                    provider="system", path=path)


if __name__ == "__main__":
    seed()
    print("Seeded smartsehat.db")
