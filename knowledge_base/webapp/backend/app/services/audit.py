"""Audit logging — every catalog mutation is recorded in audit_logs."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..db import dumps
from ..settings import CURRENT_USER_ID


def _resolve_user_id(conn: sqlite3.Connection) -> int | None:
    """Only attribute to a real user; otherwise NULL (FK-safe) so audit logging
    never blocks a legitimate edit when auth is still stubbed."""
    row = conn.execute("SELECT id FROM users WHERE id = ?", (CURRENT_USER_ID,)).fetchone()
    return row["id"] if row else None


def log(conn: sqlite3.Connection, *, entity_type: str, entity_id: int,
        action: str, before: Any = None, after: Any = None) -> None:
    conn.execute(
        """INSERT INTO audit_logs
           (organization_id, user_id, entity_type, entity_id, action, changes, created_at)
           VALUES (NULL, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
        (_resolve_user_id(conn), entity_type, entity_id, action,
         dumps({"before": before, "after": after})),
    )
