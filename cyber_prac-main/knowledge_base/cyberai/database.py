"""SQLite connection management and schema application.

Kept deliberately thin so the same helpers work against PostgreSQL later
(swap `connect` for a psycopg pool; the SQL itself is portable).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import settings


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with production-friendly pragmas enabled."""
    path = db_path or settings.DATABASE_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Enforce referential integrity (off by default in SQLite).
    conn.execute("PRAGMA foreign_keys = ON;")
    # WAL: concurrent readers during writes — closest SQLite gets to prod.
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def apply_schema(conn: sqlite3.Connection, schema_path: Path | None = None) -> None:
    """Execute the schema DDL script within a single transaction."""
    path = schema_path or settings.SCHEMA_PATH
    sql = path.read_text(encoding="utf-8")
    with conn:                       # commit on success, rollback on error
        conn.executescript(sql)


def database_is_populated(conn: sqlite3.Connection) -> bool:
    """True if the catalog already contains at least one framework."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='frameworks'"
    ).fetchone()
    if row is None:
        return False
    return conn.execute("SELECT COUNT(*) FROM frameworks").fetchone()[0] > 0
