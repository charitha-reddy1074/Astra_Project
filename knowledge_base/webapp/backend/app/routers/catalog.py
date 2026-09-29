"""Read/catalog routes: frameworks, tree, control lists, search, stats,
validation, export."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from ..db import get_db, parse_json_column
from ..schemas.models import (ControlListItem, DashboardStats, Framework,
                             SearchHit, TreeNode, ValidationReport)
from ..services import catalog, validation

router = APIRouter()


@router.get("/frameworks", response_model=list[Framework])
def list_frameworks(db: sqlite3.Connection = Depends(get_db)):
    return catalog.list_frameworks(db)


@router.get("/frameworks/{framework_id}", response_model=Framework)
def get_framework(framework_id: int, db: sqlite3.Connection = Depends(get_db)):
    return catalog.get_framework(db, framework_id)


@router.get("/frameworks/{framework_id}/tree", response_model=list[TreeNode])
def get_tree(framework_id: int, db: sqlite3.Connection = Depends(get_db)):
    return catalog.build_tree(db, framework_id)


@router.get("/frameworks/{framework_id}/controls", response_model=list[ControlListItem])
def list_controls(framework_id: int,
                  node_id: int | None = None,
                  q: str | None = None,
                  question_type: str | None = None,
                  db: sqlite3.Connection = Depends(get_db)):
    return catalog.list_controls(db, framework_id, node_id, q, question_type)


@router.get("/frameworks/{framework_id}/validate", response_model=ValidationReport)
def validate(framework_id: int, db: sqlite3.Connection = Depends(get_db)):
    return validation.validate_framework(db, framework_id)


@router.get("/frameworks/{framework_id}/export")
def export_framework(framework_id: int, db: sqlite3.Connection = Depends(get_db)):
    """Reconstruct a nested JSON export from the relational catalog."""
    fw = catalog.get_framework(db, framework_id)
    tree = catalog.build_tree(db, framework_id)
    controls = catalog.list_controls(db, framework_id)
    ctrl_by_node: dict[int, list] = {}
    for c in controls:
        detail = db.execute(
            "SELECT statement, name, requires_evidence, attributes FROM controls WHERE id=?",
            (c.id,)).fetchone()
        qs = db.execute(
            "SELECT question_code, text, question_type, weight FROM questions "
            "WHERE control_id=? ORDER BY sort_order", (c.id,)).fetchall()
        ctrl_by_node.setdefault(c.node_id, []).append({
            "control_id": c.control_id,
            "control_statement": detail["statement"],
            "name": detail["name"],
            "requires_evidence": bool(detail["requires_evidence"]),
            "attributes": parse_json_column(detail["attributes"]),
            "questions": [dict(q) for q in qs],
        })

    def render(node: TreeNode) -> dict:
        return {
            "node_id": node.code, "node_name": node.name, "node_type": node.node_type,
            "controls": ctrl_by_node.get(node.id, []),
            "children": [render(ch) for ch in node.children],
        }

    return {
        "framework_id": fw.external_uuid, "code": fw.code, "name": fw.name,
        "version": fw.version, "domains": [render(r) for r in tree],
    }


@router.get("/search", response_model=list[SearchHit])
def search(q: str = Query(min_length=1), limit: int = 50,
           db: sqlite3.Connection = Depends(get_db)):
    return catalog.search(db, q, limit)


@router.get("/stats", response_model=DashboardStats)
def stats(db: sqlite3.Connection = Depends(get_db)):
    result = catalog.dashboard_stats(db)
    # enrich with validation error count across frameworks
    bad = 0
    for fw in result.frameworks_detail:
        if validation.validate_framework(db, fw.id).error_count > 0:
            bad += 1
    result.validation_error_frameworks = bad
    return result
