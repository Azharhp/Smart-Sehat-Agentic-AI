"""Additive SQLite migrations for identity, encounter ownership, and media."""
from __future__ import annotations
import sqlite3

CURRENT_SCHEMA_VERSION = 1


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def migrate(conn: sqlite3.Connection) -> None:
    """Apply idempotent, additive migrations without rewriting legacy rows."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    if 1 not in applied:
        _add_column(conn, "patients", "bhu_id TEXT")
        for definition in (
            "workflow_type TEXT", "actor_id TEXT", "status TEXT",
            "version INTEGER", "patient_context_version INTEGER",
            "patient_snapshot_json TEXT", "identity_kind TEXT", "updated_at TEXT",
        ):
            _add_column(conn, "cases", definition)
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_facilities (
            user_id TEXT NOT NULL,
            bhu_id TEXT NOT NULL,
            PRIMARY KEY (user_id, bhu_id)
        );
        CREATE TABLE IF NOT EXISTS patient_context_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            captured_by TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            UNIQUE(case_id, version)
        );
        CREATE TABLE IF NOT EXISTS media_artifacts (
            artifact_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            patient_id TEXT,
            identity_kind TEXT NOT NULL,
            uploader_actor_id TEXT NOT NULL,
            bhu_id TEXT NOT NULL,
            original_filename TEXT,
            detected_mime TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            storage_path TEXT NOT NULL UNIQUE,
            purpose TEXT NOT NULL,
            status TEXT NOT NULL,
            modality_confirmed INTEGER NOT NULL DEFAULT 0,
            confirmation_actor_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_case ON patient_context_snapshots(case_id, version);
        CREATE INDEX IF NOT EXISTS idx_media_case ON media_artifacts(case_id);
        """)
        # Preserve legacy ownership as explicit best-effort migration data.
        conn.execute("INSERT OR IGNORE INTO user_facilities(user_id, bhu_id) SELECT user_id, bhu_id FROM users WHERE bhu_id IS NOT NULL")
        conn.execute(
            "UPDATE patients SET bhu_id=(SELECT MIN(c.bhu_id) FROM cases c "
            "WHERE c.patient_id=patients.patient_id) WHERE bhu_id IS NULL AND "
            "1=(SELECT COUNT(DISTINCT c.bhu_id) FROM cases c "
            "WHERE c.patient_id=patients.patient_id AND c.bhu_id IS NOT NULL)"
        )
        conn.execute("UPDATE cases SET actor_id=paramedic_id WHERE actor_id IS NULL")
        conn.execute("UPDATE cases SET workflow_type=CASE WHEN case_id LIKE 'DAWA-%' THEN 'dawa' ELSE 'consultation' END WHERE workflow_type IS NULL")
        conn.execute("UPDATE cases SET identity_kind=CASE WHEN patient_id='RX-WALKIN' OR patient_id IS NULL THEN 'walk_in' ELSE 'registered' END WHERE identity_kind IS NULL")
        conn.execute("UPDATE cases SET status=CASE WHEN completed_at IS NULL THEN 'legacy_in_progress' ELSE 'completed' END WHERE status IS NULL")
        conn.execute("UPDATE cases SET version=1 WHERE version IS NULL")
        conn.execute("UPDATE cases SET patient_context_version=0 WHERE patient_context_version IS NULL")
        conn.execute("UPDATE cases SET updated_at=COALESCE(completed_at, started_at) WHERE updated_at IS NULL")
        conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
        conn.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
    conn.commit()
