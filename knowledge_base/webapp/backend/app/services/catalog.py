"""Read-side services: frameworks, tree, control lists, search, stats."""
from __future__ import annotations

import sqlite3

from fastapi import HTTPException

from ..schemas.models import (ControlListItem, DashboardStats, Framework,
                             SearchHit, TreeNode)
from ..db import parse_json_column


# --- frameworks --------------------------------------------------------------
def list_frameworks(conn: sqlite3.Connection) -> list[Framework]:
    rows = conn.execute("""
        SELECT f.*,
          (SELECT COUNT(*) FROM framework_nodes n WHERE n.framework_id=f.id) node_count,
          (SELECT COUNT(*) FROM controls c WHERE c.framework_id=f.id) control_count,
          (SELECT COUNT(*) FROM questions q JOIN controls c ON c.id=q.control_id
             WHERE c.framework_id=f.id) question_count
        FROM frameworks f ORDER BY f.name, f.version
    """).fetchall()
    return [Framework(**dict(r)) for r in rows]


def get_framework(conn: sqlite3.Connection, framework_id: int) -> Framework:
    for fw in list_frameworks(conn):
        if fw.id == framework_id:
            return fw
    raise HTTPException(404, f"Framework {framework_id} not found")


# --- tree --------------------------------------------------------------------
def build_tree(conn: sqlite3.Connection, framework_id: int) -> list[TreeNode]:
    nodes = conn.execute(
        """SELECT id, parent_id, node_type, code, name FROM framework_nodes
           WHERE framework_id = ? ORDER BY sort_order, id""",
        (framework_id,)).fetchall()
    if not nodes:
        get_framework(conn, framework_id)  # raises 404 if framework absent
        return []

    direct = {r[0]: r[1] for r in conn.execute(
        "SELECT node_id, COUNT(*) FROM controls WHERE framework_id=? GROUP BY node_id",
        (framework_id,)).fetchall()}

    by_id: dict[int, TreeNode] = {}
    for r in nodes:
        by_id[r["id"]] = TreeNode(
            id=r["id"], parent_id=r["parent_id"], node_type=r["node_type"],
            code=r["code"], name=r["name"],
            control_count=direct.get(r["id"], 0))

    roots: list[TreeNode] = []
    for node in by_id.values():
        if node.parent_id and node.parent_id in by_id:
            by_id[node.parent_id].children.append(node)
        else:
            roots.append(node)

    def rollup(n: TreeNode) -> int:
        total = n.control_count + sum(rollup(c) for c in n.children)
        n.descendant_control_count = total
        return total

    for r in roots:
        rollup(r)
    return roots


# --- control lists -----------------------------------------------------------
def list_controls(conn: sqlite3.Connection, framework_id: int,
                  node_id: int | None = None, q: str | None = None,
                  question_type: str | None = None) -> list[ControlListItem]:
    sql = ["""
        SELECT c.id, c.control_id, c.name, c.statement, c.node_id, c.sort_order,
               c.requires_evidence, n.code node_code,
               (SELECT COUNT(*) FROM questions qq WHERE qq.control_id=c.id) question_count
        FROM controls c JOIN framework_nodes n ON n.id=c.node_id
        WHERE c.framework_id = ?"""]
    params: list = [framework_id]
    if node_id is not None:
        sql.append("AND c.node_id = ?"); params.append(node_id)
    if q:
        sql.append("AND (c.control_id LIKE ? OR c.statement LIKE ? OR c.name LIKE ?)")
        like = f"%{q}%"; params += [like, like, like]
    if question_type:
        sql.append("""AND EXISTS (SELECT 1 FROM questions qt
                        WHERE qt.control_id=c.id AND qt.question_type=?)""")
        params.append(question_type)
    sql.append("ORDER BY c.sort_order, c.id")
    rows = conn.execute("\n".join(sql), params).fetchall()
    return [ControlListItem(
        id=r["id"], control_id=r["control_id"], name=r["name"],
        statement=r["statement"], node_id=r["node_id"], node_code=r["node_code"],
        requires_evidence=bool(r["requires_evidence"]),
        question_count=r["question_count"], sort_order=r["sort_order"],
    ) for r in rows]


# --- search ------------------------------------------------------------------
def search(conn: sqlite3.Connection, term: str, limit: int = 50) -> list[SearchHit]:
    like = f"%{term}%"
    rows = conn.execute("""
        SELECT c.id control_pk, c.framework_id, f.code framework_code,
               c.control_id, c.name, c.statement, n.code node_code,
               CASE
                 WHEN c.control_id LIKE ? THEN 'control_id'
                 WHEN c.name LIKE ? THEN 'name'
                 WHEN c.statement LIKE ? THEN 'statement'
                 WHEN n.code LIKE ? THEN 'node'
                 ELSE 'question'
               END match_field
        FROM controls c
        JOIN frameworks f ON f.id=c.framework_id
        JOIN framework_nodes n ON n.id=c.node_id
        WHERE c.control_id LIKE ? OR c.name LIKE ? OR c.statement LIKE ?
           OR n.code LIKE ? OR n.name LIKE ?
           OR EXISTS (SELECT 1 FROM questions q WHERE q.control_id=c.id AND q.text LIKE ?)
        ORDER BY
          CASE WHEN c.control_id LIKE ? THEN 0 ELSE 1 END, f.code, c.sort_order
        LIMIT ?
    """, [like, like, like, like, like, like, like, like, like, like, like, limit]
    ).fetchall()
    return [SearchHit(**dict(r)) for r in rows]


# --- stats -------------------------------------------------------------------
def dashboard_stats(conn: sqlite3.Connection) -> DashboardStats:
    def scalar(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    frameworks = list_frameworks(conn)
    recent = conn.execute("""
        SELECT id, entity_type, entity_id, action, created_at
        FROM audit_logs ORDER BY id DESC LIMIT 10""").fetchall()

    return DashboardStats(
        frameworks=len(frameworks),
        nodes=scalar("SELECT COUNT(*) FROM framework_nodes"),
        controls=scalar("SELECT COUNT(*) FROM controls"),
        questions=scalar("SELECT COUNT(*) FROM questions"),
        assessments=scalar("SELECT COUNT(*) FROM assessments"),
        synthesized_questions=scalar("SELECT COUNT(*) FROM questions WHERE is_synthesized=1"),
        controls_without_questions=scalar(
            "SELECT COUNT(*) FROM controls c WHERE NOT EXISTS "
            "(SELECT 1 FROM questions q WHERE q.control_id=c.id)"),
        validation_error_frameworks=0,  # filled by caller if needed
        frameworks_detail=frameworks,
        recent_activity=[dict(r) for r in recent],
    )
