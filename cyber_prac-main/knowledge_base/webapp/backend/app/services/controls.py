"""Control aggregate operations: read detail, create, update, delete,
duplicate, move. A "control" to the UI is the control row + its questions +
evidence types + attributes, edited as one unit.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from fastapi import HTTPException

from ..db import dumps, parse_json_column
from ..schemas.models import (ControlCreate, ControlDetail, ControlUpdate,
                              Question)
from . import audit


# --- helpers -----------------------------------------------------------------
def _row_hash(control_row: sqlite3.Row, questions: list[dict],
              evidence: list[str]) -> str:
    """Stable hash of the editable state for soft optimistic concurrency."""
    basis = "|".join([
        control_row["statement"] or "",
        control_row["name"] or "",
        str(control_row["requires_evidence"]),
        control_row["attributes"] or "",
        dumps([{k: q[k] for k in ("text", "question_type", "weight",
                                    "help_text", "sort_order")} for q in questions]) or "",
        ",".join(sorted(evidence)),
    ])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _node_path(conn: sqlite3.Connection, node_id: int) -> list[str]:
    path: list[str] = []
    current = node_id
    guard = 0
    while current is not None and guard < 50:
        row = conn.execute(
            "SELECT code, parent_id FROM framework_nodes WHERE id = ?",
            (current,)).fetchone()
        if row is None:
            break
        path.append(row["code"])
        current = row["parent_id"]
        guard += 1
    return list(reversed(path))


def _fetch_questions(conn: sqlite3.Connection, control_pk: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM questions WHERE control_id = ? ORDER BY sort_order, id",
        (control_pk,)).fetchall()
    return [dict(r) for r in rows]


def _fetch_evidence(conn: sqlite3.Connection, control_pk: int) -> list[str]:
    rows = conn.execute(
        "SELECT evidence_type FROM control_evidence_types WHERE control_id = ? ORDER BY id",
        (control_pk,)).fetchall()
    return [r["evidence_type"] for r in rows]


# --- read --------------------------------------------------------------------
def get_detail(conn: sqlite3.Connection, control_pk: int) -> ControlDetail:
    row = conn.execute("SELECT * FROM controls WHERE id = ?", (control_pk,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Control {control_pk} not found")
    node = conn.execute(
        "SELECT code FROM framework_nodes WHERE id = ?", (row["node_id"],)).fetchone()
    q_rows = _fetch_questions(conn, control_pk)
    evidence = _fetch_evidence(conn, control_pk)
    return ControlDetail(
        id=row["id"],
        framework_id=row["framework_id"],
        node_id=row["node_id"],
        node_code=node["code"] if node else "",
        node_path=_node_path(conn, row["node_id"]),
        control_id=row["control_id"],
        name=row["name"],
        statement=row["statement"],
        requires_evidence=bool(row["requires_evidence"]),
        sort_order=row["sort_order"],
        attributes=parse_json_column(row["attributes"]),
        questions=[Question(
            id=q["id"], question_code=q["question_code"], text=q["text"],
            question_type=q["question_type"], choices=parse_json_column(q["choices"]),
            help_text=q["help_text"], weight=q["weight"],
            is_synthesized=bool(q["is_synthesized"]), sort_order=q["sort_order"],
        ) for q in q_rows],
        evidence_types=evidence,
        row_hash=_row_hash(row, q_rows, evidence),
    )


# --- write -------------------------------------------------------------------
def _write_questions(conn, control_pk: int, questions: list[Question]) -> None:
    conn.execute("DELETE FROM questions WHERE control_id = ?", (control_pk,))
    for i, q in enumerate(questions):
        conn.execute(
            """INSERT INTO questions
               (control_id, question_code, text, question_type, choices,
                help_text, weight, is_synthesized, sort_order)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (control_pk, q.question_code, q.text, q.question_type, dumps(q.choices),
             q.help_text, q.weight, int(q.is_synthesized),
             q.sort_order if q.sort_order else i))


def _write_evidence(conn, control_pk: int, evidence: list[str]) -> None:
    conn.execute("DELETE FROM control_evidence_types WHERE control_id = ?", (control_pk,))
    for etype in dict.fromkeys(e for e in evidence if e):
        conn.execute(
            "INSERT INTO control_evidence_types (control_id, evidence_type) VALUES (?,?)",
            (control_pk, etype))


def create(conn: sqlite3.Connection, framework_id: int,
           payload: ControlCreate) -> ControlDetail:
    node = conn.execute(
        "SELECT id, framework_id FROM framework_nodes WHERE id = ?",
        (payload.node_id,)).fetchone()
    if node is None or node["framework_id"] != framework_id:
        raise HTTPException(400, "node_id does not belong to this framework")
    dup = conn.execute(
        "SELECT 1 FROM controls WHERE framework_id = ? AND control_id = ?",
        (framework_id, payload.control_id)).fetchone()
    if dup:
        raise HTTPException(409, f"control_id '{payload.control_id}' already exists")

    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order),-1) FROM controls WHERE framework_id = ?",
        (framework_id,)).fetchone()[0]
    cur = conn.execute(
        """INSERT INTO controls
           (framework_id, node_id, control_id, name, statement,
            requires_evidence, sort_order, attributes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (framework_id, payload.node_id, payload.control_id, payload.name,
         payload.statement, int(payload.requires_evidence), max_order + 1,
         dumps(payload.attributes)))
    pk = cur.lastrowid
    _write_questions(conn, pk, payload.questions)
    _write_evidence(conn, pk, payload.evidence_types)
    audit.log(conn, entity_type="control", entity_id=pk, action="create",
              after={"control_id": payload.control_id})
    return get_detail(conn, pk)


def update(conn: sqlite3.Connection, control_pk: int,
           payload: ControlUpdate) -> ControlDetail:
    before = get_detail(conn, control_pk)
    if payload.row_hash and payload.row_hash != before.row_hash:
        raise HTTPException(
            409, "This control changed since you opened it. Reload to see the latest.")

    fields, values = [], []
    if payload.name is not None:
        fields.append("name = ?"); values.append(payload.name)
    if payload.statement is not None:
        fields.append("statement = ?"); values.append(payload.statement)
    if payload.requires_evidence is not None:
        fields.append("requires_evidence = ?"); values.append(int(payload.requires_evidence))
    if payload.attributes is not None:
        fields.append("attributes = ?"); values.append(dumps(payload.attributes))
    if fields:
        values.append(control_pk)
        conn.execute(f"UPDATE controls SET {', '.join(fields)} WHERE id = ?", values)
    if payload.questions is not None:
        _write_questions(conn, control_pk, payload.questions)
    if payload.evidence_types is not None:
        _write_evidence(conn, control_pk, payload.evidence_types)

    after = get_detail(conn, control_pk)
    audit.log(conn, entity_type="control", entity_id=control_pk, action="update",
              before=before.model_dump(include={"statement", "name",
                                                 "requires_evidence", "attributes"}),
              after=after.model_dump(include={"statement", "name",
                                              "requires_evidence", "attributes"}))
    return after


def delete(conn: sqlite3.Connection, control_pk: int) -> None:
    before = get_detail(conn, control_pk)
    conn.execute("DELETE FROM controls WHERE id = ?", (control_pk,))  # cascades
    audit.log(conn, entity_type="control", entity_id=control_pk, action="delete",
              before={"control_id": before.control_id})


def duplicate(conn: sqlite3.Connection, control_pk: int) -> ControlDetail:
    src = get_detail(conn, control_pk)
    # find a free control_id: append -COPY, -COPY2, ...
    base = f"{src.control_id}-COPY"
    new_id, n = base, 1
    while conn.execute("SELECT 1 FROM controls WHERE framework_id=? AND control_id=?",
                       (src.framework_id, new_id)).fetchone():
        n += 1
        new_id = f"{base}{n}"
    payload = ControlCreate(
        node_id=src.node_id, control_id=new_id,
        name=src.name, statement=src.statement,
        requires_evidence=src.requires_evidence, attributes=src.attributes,
        questions=[Question(**{**q.model_dump(), "id": None}) for q in src.questions],
        evidence_types=src.evidence_types,
    )
    return create(conn, src.framework_id, payload)


def move(conn: sqlite3.Connection, control_pk: int, target_node_id: int) -> ControlDetail:
    control = conn.execute(
        "SELECT framework_id FROM controls WHERE id = ?", (control_pk,)).fetchone()
    if control is None:
        raise HTTPException(404, "Control not found")
    node = conn.execute(
        "SELECT framework_id FROM framework_nodes WHERE id = ?", (target_node_id,)).fetchone()
    if node is None:
        raise HTTPException(404, "Target node not found")
    if node["framework_id"] != control["framework_id"]:
        raise HTTPException(400, "Cannot move a control across frameworks")
    conn.execute("UPDATE controls SET node_id = ? WHERE id = ?",
                 (target_node_id, control_pk))
    audit.log(conn, entity_type="control", entity_id=control_pk, action="move",
              after={"node_id": target_node_id})
    return get_detail(conn, control_pk)


def history(conn: sqlite3.Connection, control_pk: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, action, changes, user_id, created_at FROM audit_logs
           WHERE entity_type='control' AND entity_id=? ORDER BY id DESC LIMIT 100""",
        (control_pk,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["changes"] = parse_json_column(d["changes"])
        out.append(d)
    return out
