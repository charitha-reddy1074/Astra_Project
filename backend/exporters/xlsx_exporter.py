"""xlsx_exporter.py – Save a generated questionnaire dict to a .xlsx file.

Produces a 4-sheet Market Assessment workbook:
  Sheet 1 – Part 1          : Pre-assessment capability checks (yes/no + detail)
  Sheet 2 – Part 2          : Structured interview questions per control
  Sheet 3 – Maturity Scoring: Per-sub-topic Mature/Partial/Critical Gap criteria
                              with blank Assessed Level / Notes columns so the
                              assessor can score each cybersecurity area
  Sheet 4 – Part 3          : Per-control evidence tracker (specific items per control)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter


# ── Palette ───────────────────────────────────────────────────────────────────
_NAVY       = "1F3864"
_BLUE       = "2E75B6"
_LIGHT_BLUE = "D6E4F0"
_ALT        = "F2F7FB"
_WHITE      = "FFFFFF"
_BORDER_CLR = "B8CCE4"

_thin = Side(style="thin", color=_BORDER_CLR)
_BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def _font(size=10, bold=False, color="000000"):
    return Font(name="Arial", size=size, bold=bold, color=color)


def _fill(hex_color):
    return PatternFill("solid", start_color=hex_color, fgColor=hex_color)


def _wrap(h="left", v="top"):
    return Alignment(horizontal=h, vertical=v, wrap_text=True)


def _col_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _freeze(ws, cell="A2"):
    ws.freeze_panes = cell


def _header_row(ws, row, headers, bg=_NAVY):
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=col, value=h)
        c.font = _font(10, bold=True, color=_WHITE)
        c.fill = _fill(bg)
        c.alignment = _wrap("center", "center")
        c.border = _BORDER
    ws.row_dimensions[row].height = 20


def _data_cell(ws, row, col, value, bg=_WHITE):
    c = ws.cell(row=row, column=col, value=value)
    c.font = _font()
    c.fill = _fill(bg)
    c.alignment = _wrap()
    c.border = _BORDER
    return c


def _section_label(ws, row, ncols, label, bg=_BLUE):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    c = ws.cell(row=row, column=1, value=label)
    c.font = _font(10, bold=True, color=_WHITE)
    c.fill = _fill(bg)
    c.alignment = _wrap()
    c.border = _BORDER
    ws.row_dimensions[row].height = 16


# ── Data extraction helpers ───────────────────────────────────────────────────

def _iter_sub_topics(q: Dict[str, Any]):
    """Yield (domain_name, sub_topic_dict) for every sub-topic in the questionnaire."""
    for group in q.get("question_groups") or []:
        domain_name = group.get("domain_name", "")
        for st in group.get("sub_topics") or []:
            yield domain_name, st


def _iter_part1(q: Dict[str, Any]):
    """Yield part1 question dicts from part1_checklist or sub_topics."""
    checklist = q.get("part1_checklist") or []
    if checklist:
        for item in checklist:
            yield item
        return
    # Fallback: pull part1_question from each sub-topic
    for _domain, st in _iter_sub_topics(q):
        p1 = st.get("part1_question")
        if p1:
            yield p1


def _iter_controls_with_questions(q: Dict[str, Any]):
    """
    Yield (domain_name, sub_topic_name, control_ids, questions_list)
    reading from the domains→categories→controls block when available,
    otherwise falling back to question_groups→sub_topics.
    """
    # Prefer the structured domains block (most complete)
    for domain in q.get("domains") or []:
        domain_name = domain.get("name", "")
        for cat in domain.get("categories") or []:
            for ctrl in cat.get("controls") or []:
                ctrl_id = ctrl.get("code", "")
                # Find the sub_topic name for this control by scanning sub_topics
                st_name = _find_sub_topic_for_control(q, ctrl_id) or cat.get("name", ctrl_id)
                questions = ctrl.get("questions") or []
                if questions:
                    yield domain_name, st_name, ctrl_id, questions
        return  # only process once

    # Fallback: use question_groups → sub_topics → questions
    for domain_name, st in _iter_sub_topics(q):
        st_name = st.get("sub_topic", "")
        ctrl_ids = st.get("control_ids") or []
        questions = st.get("questions") or []
        if questions:
            yield domain_name, st_name, ", ".join(ctrl_ids), questions


def _find_sub_topic_for_control(q: Dict[str, Any], ctrl_id: str) -> str:
    """Return the sub_topic name for a given control_id by scanning sub_topics."""
    for _domain, st in _iter_sub_topics(q):
        cids = st.get("control_ids") or []
        if isinstance(cids, str):
            cids = [c.strip() for c in cids.split(",")]
        if ctrl_id in cids:
            return st.get("sub_topic", "")
    return ""


def _iter_part3_evidence(q: Dict[str, Any]):
    """
    Yield (domain_name, sub_topic_name, control_id_or_group, evidence_list)
    from part3_tracker if available, otherwise from sub_topics.evidence_to_request.
    """
    tracker = q.get("part3_tracker") or []
    if tracker:
        for entry in tracker:
            domain = entry.get("domain_name", "")
            st_name = entry.get("sub_topic", "") or entry.get("sub_topic_name", "")
            ctrl = entry.get("control_id", "") or entry.get("control_ids", "")
            if isinstance(ctrl, list):
                ctrl = ", ".join(ctrl)
            evidence = entry.get("evidence_to_request") or entry.get("evidence_items") or []
            if isinstance(evidence, str):
                evidence = [e.strip() for e in evidence.split(",") if e.strip()]
            yield domain, st_name, ctrl, evidence
        return

    # Fallback: from sub_topics
    for domain_name, st in _iter_sub_topics(q):
        st_name = st.get("sub_topic", "")
        ctrl_ids = st.get("control_ids") or []
        if isinstance(ctrl_ids, list):
            ctrl_ids = ", ".join(ctrl_ids)
        evidence = st.get("evidence_to_request") or []
        if evidence:
            yield domain_name, st_name, ctrl_ids, evidence


# ── Sheet 1: Part 1 Pre-Assessment ───────────────────────────────────────────

def _write_part1(ws, q: Dict[str, Any]) -> None:
    ws.title = "Part 1 - Pre-Assessment"
    _col_widths(ws, [6, 30, 55, 22, 45])
    _freeze(ws, "A2")

    # Banner
    ws.merge_cells("A1:E1")
    c = ws.cell(row=1, column=1, value="Part 1 – Pre-Assessment Capability Check")
    c.font = _font(13, bold=True, color=_WHITE)
    c.fill = _fill(_NAVY)
    c.alignment = _wrap("center", "center")
    ws.row_dimensions[1].height = 28

    _header_row(ws, 2, ["#", "Domain / Sub-topic", "Question", "Response Options", "Detail Prompt"])

    row = 3
    num = 1
    seen = set()

    for item in _iter_part1(q):
        qid = item.get("question_id") or item.get("question_text", "")[:40]
        if qid in seen:
            continue
        seen.add(qid)

        bg = _ALT if row % 2 == 0 else _WHITE
        domain_label = item.get("sub_topic") or item.get("cluster") or item.get("domain_name", "")
        question_text = item.get("question_text") or item.get("text", "")
        resp_opts = item.get("response_options") or item.get("answer_format") or "Yes / No / Partial"
        if isinstance(resp_opts, list):
            resp_opts = " / ".join(resp_opts)
        detail = item.get("detail_prompt", "")

        _data_cell(ws, row, 1, num, bg)
        _data_cell(ws, row, 2, domain_label, bg)
        _data_cell(ws, row, 3, question_text, bg)
        _data_cell(ws, row, 4, resp_opts, bg)
        _data_cell(ws, row, 5, detail, bg)

        ws.row_dimensions[row].height = 40
        row += 1
        num += 1

    if row == 3:
        # No part1 questions found — write a placeholder
        _data_cell(ws, 3, 1, "—")
        ws.cell(row=3, column=2).value = "No pre-assessment questions found."


# ── Sheet 2: Part 2 Structured Interview Questions ───────────────────────────

def _write_part2(ws, q: Dict[str, Any]) -> None:
    ws.title = "Part 2 - Interview Questions"
    _col_widths(ws, [6, 25, 22, 14, 55, 18, 30, 8])
    _freeze(ws, "A2")

    ws.merge_cells("A1:H1")
    c = ws.cell(row=1, column=1, value="Part 2 – Structured Interview Questions")
    c.font = _font(13, bold=True, color=_WHITE)
    c.fill = _fill(_NAVY)
    c.alignment = _wrap("center", "center")
    ws.row_dimensions[1].height = 28

    _header_row(ws, 2, ["#", "Domain", "Sub-topic / Control", "Control ID", "Question", "Type", "Evidence Required", "Weight"])

    row = 3
    q_num = 1

    # Group by domain+sub_topic for section labels
    last_section = None

    for domain_name, st_name, ctrl_id, questions in _iter_controls_with_questions(q):
        section_key = (domain_name, st_name)
        if section_key != last_section:
            label = f"▶  {domain_name}  ·  {st_name}"
            _section_label(ws, row, 8, label)
            row += 1
            last_section = section_key

        for question in questions:
            bg = _ALT if row % 2 == 0 else _WHITE

            qtext = question.get("text") or question.get("question_text", "")
            qtype = (question.get("question_type") or "FREE_TEXT").upper()
            # evidence_required: true if the question has expected_evidence_types or evidenceRequired flag
            evid_types = question.get("expected_evidence_types") or []
            evid_req = question.get("evidenceRequired", bool(evid_types))
            evid_label = "✓ Required" if evid_req else ""
            weight = question.get("weight", "")

            _data_cell(ws, row, 1, q_num, bg)
            _data_cell(ws, row, 2, domain_name, bg)
            _data_cell(ws, row, 3, st_name, bg)
            _data_cell(ws, row, 4, ctrl_id, bg)
            _data_cell(ws, row, 5, qtext, bg)
            _data_cell(ws, row, 6, qtype, bg)
            _data_cell(ws, row, 7, evid_label, bg)
            _data_cell(ws, row, 8, weight, bg)

            ws.row_dimensions[row].height = 45
            row += 1
            q_num += 1

    if row == 3:
        _data_cell(ws, 3, 1, "—")
        ws.cell(row=3, column=2).value = "No interview questions found."


# ── Sheet: Maturity Scoring Guide ────────────────────────────────────────────

def _iter_maturity_criteria(q: Dict[str, Any]):
    """Yield (domain, sub_topic, control_ids, mature, partial, critical_gap).

    Prefers the top-level maturity_scoring_criteria list (verbatim for the
    derived/generated otherwise); falls back to per-group maturity guides.
    """
    criteria = q.get("maturity_scoring_criteria") or []
    if criteria:
        for row in criteria:
            cols = row.get("scoring_columns") or row.get("scoring") or {}
            # Support the cluster-file shape too (no scoring_columns wrapper)
            if not cols and ("mature" in row or "critical_gap" in row):
                cols = row
            ctrl = row.get("control_ids") or row.get("control_id") or ""
            if isinstance(ctrl, list):
                ctrl = ", ".join(ctrl)
            yield (
                row.get("domain_name") or row.get("cluster_name", ""),
                row.get("sub_topic", ""),
                ctrl,
                cols.get("mature", ""),
                cols.get("partial", ""),
                cols.get("critical_gap", ""),
            )
        return

    # Fallback: pull from question_groups → sub_topics / group maturity_guide
    for group in q.get("question_groups") or []:
        domain = group.get("domain_name", "")
        sub_topics = group.get("sub_topics") or []
        if sub_topics:
            for st in sub_topics:
                mg = st.get("maturity_guide") or {}
                ctrl = st.get("control_ids") or []
                if isinstance(ctrl, list):
                    ctrl = ", ".join(ctrl)
                yield (domain, st.get("sub_topic", ""), ctrl,
                       mg.get("mature", ""), mg.get("partial", ""), mg.get("critical_gap", ""))
        else:
            mg = group.get("maturity_guide") or {}
            if any(mg.values()):
                yield (domain, "", "", mg.get("mature", ""), mg.get("partial", ""), mg.get("critical_gap", ""))


def _write_maturity(ws, q: Dict[str, Any]) -> None:
    ws.title = "Maturity Scoring"
    _col_widths(ws, [22, 26, 14, 40, 40, 40, 16, 30])
    _freeze(ws, "A3")

    ws.merge_cells("A1:H1")
    c = ws.cell(row=1, column=1, value="Maturity Scoring Guide – Assess each sub-topic against the criteria")
    c.font = _font(13, bold=True, color=_WHITE)
    c.fill = _fill(_NAVY)
    c.alignment = _wrap("center", "center")
    ws.row_dimensions[1].height = 28

    _header_row(ws, 2, [
        "Domain", "Sub-topic", "Control(s)",
        "Mature", "Partial", "Critical Gap",
        "Assessed Level", "Notes / Justification",
    ])

    row = 3
    last_domain = None
    for domain, sub_topic, ctrl, mature, partial, gap in _iter_maturity_criteria(q):
        if domain != last_domain:
            _section_label(ws, row, 8, f"▶  {domain}")
            row += 1
            last_domain = domain

        bg = _ALT if row % 2 == 0 else _WHITE
        _data_cell(ws, row, 1, domain, bg)
        _data_cell(ws, row, 2, sub_topic, bg)
        _data_cell(ws, row, 3, ctrl, bg)
        _data_cell(ws, row, 4, mature, bg)
        _data_cell(ws, row, 5, partial, bg)
        _data_cell(ws, row, 6, gap, bg)
        _data_cell(ws, row, 7, "", bg)   # Assessed Level — Mature / Partial / Critical Gap
        _data_cell(ws, row, 8, "", bg)   # Notes / Justification (blank for assessor)
        ws.row_dimensions[row].height = 70
        row += 1

    if row == 3:
        _data_cell(ws, 3, 1, "—")
        ws.cell(row=3, column=2).value = "No maturity scoring criteria found."


# ── Sheet 3: Part 3 Evidence Tracker ─────────────────────────────────────────

def _write_part3(ws, q: Dict[str, Any]) -> None:
    ws.title = "Part 3 - Evidence Tracker"
    _col_widths(ws, [25, 30, 20, 50, 20, 30])
    _freeze(ws, "A2")

    ws.merge_cells("A1:F1")
    c = ws.cell(row=1, column=1, value="Part 3 – Per-Control Evidence Tracker")
    c.font = _font(13, bold=True, color=_WHITE)
    c.fill = _fill(_NAVY)
    c.alignment = _wrap("center", "center")
    ws.row_dimensions[1].height = 28

    _header_row(ws, 2, ["Domain", "Sub-topic", "Control(s)", "Evidence Required", "Status", "Notes / Reference"])

    row = 3
    last_domain = None

    for domain_name, st_name, ctrl_ids, evidence_list in _iter_part3_evidence(q):
        if domain_name != last_domain:
            _section_label(ws, row, 6, f"▶  {domain_name}")
            row += 1
            last_domain = domain_name

        for evid_item in evidence_list:
            bg = _ALT if row % 2 == 0 else _WHITE
            if isinstance(evid_item, dict):
                evid_text = evid_item.get("name") or evid_item.get("item") or str(evid_item)
            else:
                evid_text = str(evid_item)

            _data_cell(ws, row, 1, domain_name, bg)
            _data_cell(ws, row, 2, st_name, bg)
            _data_cell(ws, row, 3, ctrl_ids, bg)
            _data_cell(ws, row, 4, evid_text, bg)
            _data_cell(ws, row, 5, "", bg)   # Status (blank for auditor)
            _data_cell(ws, row, 6, "", bg)   # Notes  (blank for auditor)

            ws.row_dimensions[row].height = 35
            row += 1

    if row == 3:
        _data_cell(ws, 3, 1, "—")
        ws.cell(row=3, column=2).value = "No evidence items found."


# ── Public API ────────────────────────────────────────────────────────────────

def save_questionnaire_xlsx(questionnaire: Dict[str, Any], output_dir: Path) -> Path:
    """Build a 3-sheet Market Assessment Excel workbook and save it.

    Returns the Path of the written .xlsx file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    qid = questionnaire.get("questionnaire_id", "questionnaire")
    path = output_dir / f"questionnaire_{qid}.xlsx"

    wb = Workbook()
    _write_part1(wb.active, questionnaire)
    _write_part2(wb.create_sheet(), questionnaire)
    _write_maturity(wb.create_sheet(), questionnaire)
    _write_part3(wb.create_sheet(), questionnaire)

    wb.save(path)
    return path

