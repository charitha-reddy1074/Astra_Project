#!/usr/bin/env python3
"""Verify catalog integrity and report import statistics.

Checks:
  * counts: frameworks / nodes / controls / questions / evidence types
  * per-framework breakdown
  * duplicate control IDs within a framework (should be zero)
  * orphaned foreign keys (PRAGMA foreign_key_check)
  * controls with no questions
  * nodes with a dangling parent

Exit code is non-zero if any integrity problem is found.
"""
from __future__ import annotations

import sys

from cyberai import database, settings


def main() -> int:
    log = settings.configure_logging(False)
    if not settings.DATABASE_PATH.exists():
        log.error("Database not found. Run: python create_database.py")
        return 1

    conn = database.connect()
    problems = 0
    try:
        q = conn.execute

        # --- headline counts ------------------------------------------------
        counts = {
            "frameworks":      q("SELECT COUNT(*) FROM frameworks").fetchone()[0],
            "framework_nodes": q("SELECT COUNT(*) FROM framework_nodes").fetchone()[0],
            "controls":        q("SELECT COUNT(*) FROM controls").fetchone()[0],
            "questions":       q("SELECT COUNT(*) FROM questions").fetchone()[0],
            "evidence_types":  q("SELECT COUNT(*) FROM control_evidence_types").fetchone()[0],
            "maturity_levels": q("SELECT COUNT(*) FROM maturity_levels").fetchone()[0],
        }
        print("\n=== CATALOG COUNTS ===")
        for k, v in counts.items():
            print(f"  {k:<18}{v:>6}")

        # --- per-framework breakdown ---------------------------------------
        print("\n=== PER-FRAMEWORK ===")
        rows = q("""
            SELECT f.code, f.version,
                   (SELECT COUNT(*) FROM framework_nodes n WHERE n.framework_id=f.id) AS nodes,
                   (SELECT COUNT(*) FROM controls c WHERE c.framework_id=f.id) AS controls,
                   (SELECT COUNT(*) FROM questions qu
                      JOIN controls c ON c.id=qu.control_id
                      WHERE c.framework_id=f.id) AS questions
            FROM frameworks f ORDER BY f.code
        """).fetchall()
        print(f"  {'CODE':<16}{'VER':<7}{'NODES':>6}{'CTRLS':>7}{'QUES':>7}")
        for r in rows:
            print(f"  {r['code']:<16}{r['version']:<7}"
                  f"{r['nodes']:>6}{r['controls']:>7}{r['questions']:>7}")

        # --- duplicate control ids within a framework -----------------------
        dups = q("""
            SELECT framework_id, control_id, COUNT(*) c
            FROM controls GROUP BY framework_id, control_id HAVING c > 1
        """).fetchall()
        print("\n=== INTEGRITY CHECKS ===")
        if dups:
            problems += len(dups)
            print(f"  [FAIL] {len(dups)} duplicate control_id(s) within a framework")
            for d in dups[:10]:
                print(f"         framework_id={d['framework_id']} control_id={d['control_id']} x{d['c']}")
        else:
            print("  [OK]   no duplicate control IDs within any framework")

        # --- orphaned foreign keys -----------------------------------------
        fk_issues = q("PRAGMA foreign_key_check").fetchall()
        if fk_issues:
            problems += len(fk_issues)
            print(f"  [FAIL] {len(fk_issues)} orphaned foreign key row(s)")
            for i in fk_issues[:10]:
                print(f"         table={i[0]} rowid={i[1]} -> {i[2]}")
        else:
            print("  [OK]   no orphaned foreign keys")

        # --- controls with no questions ------------------------------------
        no_q = q("""
            SELECT COUNT(*) FROM controls c
            WHERE NOT EXISTS (SELECT 1 FROM questions qu WHERE qu.control_id=c.id)
        """).fetchone()[0]
        if no_q:
            problems += no_q
            print(f"  [WARN] {no_q} control(s) have no questions")
        else:
            print("  [OK]   every control has at least one question")

        # --- dangling node parents (should be impossible with FK on) --------
        dangling = q("""
            SELECT COUNT(*) FROM framework_nodes n
            WHERE n.parent_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM framework_nodes p WHERE p.id=n.parent_id)
        """).fetchone()[0]
        if dangling:
            problems += dangling
            print(f"  [FAIL] {dangling} node(s) with a dangling parent")
        else:
            print("  [OK]   node hierarchy is consistent")

    finally:
        conn.close()

    print("\n" + ("VERIFICATION PASSED" if problems == 0
                  else f"VERIFICATION FAILED — {problems} problem(s)"))
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
