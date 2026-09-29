#!/usr/bin/env python3
"""Create the CyberAI SQLite database from schema.sql.

Usage:
    python create_database.py            # create if absent
    python create_database.py --force    # drop existing DB and recreate
    python create_database.py --verbose
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cyberai import database, settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the CyberAI database.")
    parser.add_argument("--force", action="store_true",
                        help="delete an existing database and recreate it")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    log = settings.configure_logging(args.verbose)
    db_path: Path = settings.DATABASE_PATH

    if db_path.exists():
        if not args.force:
            log.error("Database already exists at %s. Use --force to recreate.", db_path)
            return 1
        log.warning("Removing existing database at %s", db_path)
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()

    if not settings.SCHEMA_PATH.exists():
        log.error("schema.sql not found at %s", settings.SCHEMA_PATH)
        return 1

    log.info("Creating database at %s", db_path)
    conn = database.connect(db_path)
    try:
        database.apply_schema(conn)
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        log.info("Schema applied successfully: %d tables created.", tables)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
