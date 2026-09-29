"""Framework importer.

Reads every JSON file in f_data/, resolves the right adapter, and writes the
normalized IR into the catalog tables inside a single transaction per file.
Idempotent: a framework (code, version) already present is skipped unless
`force=True`, in which case it is deleted (cascade) and re-imported.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import settings
from .adapters import adapter_for
from .ir import FrameworkIR

log = logging.getLogger("cyberai")


@dataclass
class ImportResult:
    code: str
    version: str
    source_file: str
    status: str                       # 'imported' | 'skipped' | 'replaced' | 'failed'
    stats: dict = field(default_factory=dict)
    error: str | None = None


def _dumps(value) -> str | None:
    """JSON-encode a dict/list for a TEXT column; None stays None."""
    if value in (None, {}, []):
        return None
    return json.dumps(value, ensure_ascii=False)


class FrameworkImporter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- public API ----------------------------------------------------------
    def import_directory(self, data_dir: Path | None = None,
                         force: bool = False) -> list[ImportResult]:
        data_dir = data_dir or settings.DATA_DIR
        files = sorted(data_dir.glob("*.json"))
        if not files:
            log.warning("No JSON files found in %s", data_dir)
            return []

        log.info("Discovered %d framework file(s) in %s", len(files), data_dir)
        results: list[ImportResult] = []
        for path in files:
            results.append(self.import_file(path, force=force))
        return results

    def import_file(self, path: Path, force: bool = False) -> ImportResult:
        try:
            fw = adapter_for(path).parse()
        except Exception as exc:  # parsing failure is isolated to this file
            log.error("Failed to parse %s: %s", path.name, exc)
            return ImportResult(code="?", version="?", source_file=path.name,
                                status="failed", error=str(exc))

        existing = self._existing_framework_id(fw.code, fw.version)
        if existing and not force:
            log.info("SKIP  %-14s v%-5s (already imported)", fw.code, fw.version)
            return ImportResult(fw.code, fw.version, path.name, "skipped")

        try:
            with self.conn:                       # atomic: commit or rollback
                if existing:
                    self.conn.execute("DELETE FROM frameworks WHERE id = ?", (existing,))
                self._write_framework(fw)
            status = "replaced" if existing else "imported"
            stats = fw.stats()
            log.info("%-8s %-14s v%-5s  nodes=%-3d controls=%-3d questions=%-3d",
                     status.upper(), fw.code, fw.version,
                     stats["nodes"], stats["controls"], stats["questions"])
            return ImportResult(fw.code, fw.version, path.name, status, stats)
        except Exception as exc:                  # transaction already rolled back
            log.error("Import of %s failed and was rolled back: %s", path.name, exc)
            return ImportResult(fw.code, fw.version, path.name, "failed", error=str(exc))

    # -- internals -----------------------------------------------------------
    def _existing_framework_id(self, code: str, version: str) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM frameworks WHERE code = ? AND version = ?",
            (code, version)).fetchone()
        return row["id"] if row else None

    def _write_framework(self, fw: FrameworkIR) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO frameworks
               (code, name, version, description, source_format, source_file,
                external_uuid, scoring_scale, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (fw.code, fw.name, fw.version, fw.description, fw.source_format,
             fw.source_file, fw.external_uuid, _dumps(fw.scoring_scale),
             fw.ingested_at))
        framework_id = cur.lastrowid

        # maturity levels
        for ml in fw.maturity_levels:
            cur.execute(
                """INSERT INTO maturity_levels
                   (framework_id, level, code, name, definition, attributes)
                   VALUES (?,?,?,?,?,?)""",
                (framework_id, ml.level, ml.code, ml.name, ml.definition,
                 _dumps(ml.attributes)))

        # nodes — two passes so parent ids are known before children reference them.
        # Insert roots first, then remaining, resolving parent_code -> id.
        code_to_id: dict[str, int] = {}
        pending = list(fw.nodes)
        # Stable multi-pass to honour arbitrary depth without recursion.
        while pending:
            progressed = False
            still: list = []
            for node in pending:
                if node.parent_code is None or node.parent_code in code_to_id:
                    parent_id = code_to_id.get(node.parent_code) if node.parent_code else None
                    cur.execute(
                        """INSERT INTO framework_nodes
                           (framework_id, parent_id, node_type, code, name,
                            description, criteria_statement, sort_order, attributes)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (framework_id, parent_id, node.node_type, node.code, node.name,
                         node.description, node.criteria_statement, node.sort_order,
                         _dumps(node.attributes)))
                    code_to_id[node.code] = cur.lastrowid
                    progressed = True
                else:
                    still.append(node)
            if not progressed:
                raise ValueError(
                    f"Unresolvable node parents in {fw.code}: "
                    f"{[n.code for n in still]}")
            pending = still

        # controls + questions + evidence types
        for control in fw.controls:
            node_id = code_to_id.get(control.node_code)
            if node_id is None:
                raise ValueError(
                    f"Control {control.control_id} references unknown node "
                    f"'{control.node_code}' in {fw.code}")
            cur.execute(
                """INSERT INTO controls
                   (framework_id, node_id, control_id, name, statement,
                    requires_evidence, sort_order, attributes)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (framework_id, node_id, control.control_id, control.name,
                 control.statement, int(control.requires_evidence),
                 control.sort_order, _dumps(control.attributes)))
            control_pk = cur.lastrowid

            for q in control.questions:
                cur.execute(
                    """INSERT INTO questions
                       (control_id, question_code, text, question_type, choices,
                        help_text, weight, is_synthesized, sort_order)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (control_pk, q.question_code, q.text, q.question_type,
                     _dumps(q.choices), q.help_text, q.weight,
                     int(q.is_synthesized), q.sort_order))

            for etype in dict.fromkeys(control.evidence_types):   # dedupe, keep order
                cur.execute(
                    """INSERT INTO control_evidence_types (control_id, evidence_type)
                       VALUES (?,?)""",
                    (control_pk, etype))
