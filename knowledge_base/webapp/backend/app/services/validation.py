"""Framework validation rules. Pure read-only checks over one framework."""
from __future__ import annotations

import sqlite3

from ..schemas.models import ValidationIssue, ValidationReport


def validate_framework(conn: sqlite3.Connection, framework_id: int) -> ValidationReport:
    issues: list[ValidationIssue] = []

    # Duplicate control IDs within the framework.
    for r in conn.execute(
        """SELECT control_id, COUNT(*) c FROM controls WHERE framework_id=?
           GROUP BY control_id HAVING c > 1""", (framework_id,)):
        issues.append(ValidationIssue(
            severity="error", rule="duplicate_control",
            message=f"Control ID '{r['control_id']}' appears {r['c']} times",
            entity_type="control", entity_ref=r["control_id"]))

    # Controls with no questions.
    for r in conn.execute(
        """SELECT id, control_id FROM controls c WHERE framework_id=?
           AND NOT EXISTS (SELECT 1 FROM questions q WHERE q.control_id=c.id)""",
        (framework_id,)):
        issues.append(ValidationIssue(
            severity="warning", rule="missing_questions",
            message=f"Control '{r['control_id']}' has no questions",
            entity_type="control", entity_id=r["id"], entity_ref=r["control_id"]))

    # Empty statements.
    for r in conn.execute(
        """SELECT id, control_id FROM controls
           WHERE framework_id=? AND (statement IS NULL OR TRIM(statement)='')""",
        (framework_id,)):
        issues.append(ValidationIssue(
            severity="error", rule="empty_statement",
            message=f"Control '{r['control_id']}' has an empty statement",
            entity_type="control", entity_id=r["id"], entity_ref=r["control_id"]))

    # Broken hierarchy: node parent points outside this framework or missing.
    for r in conn.execute(
        """SELECT n.id, n.code FROM framework_nodes n WHERE n.framework_id=?
           AND n.parent_id IS NOT NULL AND NOT EXISTS
           (SELECT 1 FROM framework_nodes p WHERE p.id=n.parent_id
              AND p.framework_id=n.framework_id)""", (framework_id,)):
        issues.append(ValidationIssue(
            severity="error", rule="broken_hierarchy",
            message=f"Node '{r['code']}' has a missing or foreign parent",
            entity_type="node", entity_id=r["id"], entity_ref=r["code"]))

    # Orphan controls: node_id missing.
    for r in conn.execute(
        """SELECT c.id, c.control_id FROM controls c WHERE c.framework_id=?
           AND NOT EXISTS (SELECT 1 FROM framework_nodes n WHERE n.id=c.node_id)""",
        (framework_id,)):
        issues.append(ValidationIssue(
            severity="error", rule="orphan_control",
            message=f"Control '{r['control_id']}' references a missing node",
            entity_type="control", entity_id=r["id"], entity_ref=r["control_id"]))

    # Unused nodes: leaf node with no controls and no children.
    for r in conn.execute(
        """SELECT n.id, n.code FROM framework_nodes n WHERE n.framework_id=?
           AND NOT EXISTS (SELECT 1 FROM controls c WHERE c.node_id=n.id)
           AND NOT EXISTS (SELECT 1 FROM framework_nodes ch WHERE ch.parent_id=n.id)""",
        (framework_id,)):
        issues.append(ValidationIssue(
            severity="warning", rule="unused_node",
            message=f"Node '{r['code']}' is empty (no controls, no children)",
            entity_type="node", entity_id=r["id"], entity_ref=r["code"]))

    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    return ValidationReport(
        framework_id=framework_id, ok=errors == 0,
        error_count=errors, warning_count=warnings, issues=issues)
