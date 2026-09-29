#!/usr/bin/env python3
"""Import every framework JSON in f_data/ into the CyberAI catalog.

Usage:
    python import_frameworks.py            # import new frameworks, skip existing
    python import_frameworks.py --force    # re-import (replace) existing versions
    python import_frameworks.py --verbose
"""
from __future__ import annotations

import argparse
import sys

from cyberai import database, settings
from cyberai.importer import FrameworkImporter


def main() -> int:
    parser = argparse.ArgumentParser(description="Import frameworks from f_data/.")
    parser.add_argument("--force", action="store_true",
                        help="replace frameworks that are already imported")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    log = settings.configure_logging(args.verbose)

    if not settings.DATABASE_PATH.exists():
        log.error("Database not found. Run: python create_database.py")
        return 1

    conn = database.connect()
    try:
        importer = FrameworkImporter(conn)
        results = importer.import_directory(force=args.force)
    finally:
        conn.close()

    # --- summary ------------------------------------------------------------
    print("\n" + "=" * 68)
    print(f"{'FRAMEWORK':<16}{'VER':<7}{'STATUS':<11}{'NODES':>6}{'CTRLS':>7}{'QUES':>7}")
    print("-" * 68)
    totals = {"nodes": 0, "controls": 0, "questions": 0}
    failed = 0
    for r in results:
        s = r.stats
        print(f"{r.code:<16}{r.version:<7}{r.status:<11}"
              f"{s.get('nodes', 0):>6}{s.get('controls', 0):>7}{s.get('questions', 0):>7}")
        for k in totals:
            totals[k] += s.get(k, 0)
        if r.status == "failed":
            failed += 1
    print("-" * 68)
    print(f"{'TOTAL':<34}{totals['nodes']:>6}{totals['controls']:>7}{totals['questions']:>7}")
    print("=" * 68)
    if failed:
        log.error("%d file(s) failed to import.", failed)
        return 1
    log.info("Import complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
