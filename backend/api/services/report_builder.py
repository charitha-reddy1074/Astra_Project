"""Build the cyber_prac report for an assessment using the ROOT backend logic.

This module only EXTRACTS the questionnaire/answers from the API's DB rows; the
actual scoring (verdicts, Mature/Partial/Critical-Gap rollup, risk/maturity
bands) and report assembly come straight from the root backend:
  - backend.services.answer_questionnaire._deterministic_fallback_score
  - backend.exporters.report_generator.build_report_data
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from backend.services.answer_questionnaire import _deterministic_fallback_score
from backend.exporters.report_generator import build_report_data
from backend.api.config import settings

_PREVIEW_PER_FILE = 1500   # chars of evidence text per file
_PREVIEW_TOTAL = 4000      # chars of evidence text per question


def _read_preview(file_path: str) -> str:
    """Read the .preview.txt sidecar for an evidence file, if present."""
    try:
        with open(file_path + ".preview.txt", encoding="utf-8") as fh:
            return fh.read()[:_PREVIEW_PER_FILE]
    except OSError:
        return ""


def _collect_request_evidence(assessment) -> tuple[dict, dict]:
    """Preview texts of owner-provided (or accepted) document-request files,
    grouped by control code and by domain code."""
    ev_dir = os.path.join(settings.EVIDENCE_FOLDER, assessment.id)
    by_control: dict[str, list[str]] = {}
    by_domain: dict[str, list[str]] = {}
    for dr in (getattr(assessment, "document_requests", None) or []):
        if dr.status not in ("provided", "accepted"):
            continue
        for stored_name in (dr.provided_files or []):
            text = _read_preview(os.path.join(ev_dir, stored_name))
            if not text.strip():
                continue
            # Evidence requested for a specific control vouches only for that
            # control; a domain-level request (no control) applies domain-wide.
            if dr.control_code:
                by_control.setdefault(dr.control_code, []).append(text)
            elif dr.domain_code:
                by_domain.setdefault(dr.domain_code, []).append(text)
    return by_control, by_domain


def _question_evidence(resp, control_code: str, domain_code: str,
                       by_control: dict, by_domain: dict) -> str:
    """Evidence text accompanying one answer: files attached to the response
    itself first, else documents the owner provided for this control/domain."""
    parts: list[str] = []
    for ev in (getattr(resp, "evidence", None) or []) if resp else []:
        text = _read_preview(ev.file_path)
        if text.strip():
            parts.append(text)
    if not parts and control_code:
        parts = list(by_control.get(control_code, []))
    if not parts and domain_code:
        parts = list(by_domain.get(domain_code, []))
    return "\n".join(parts)[:_PREVIEW_TOTAL]


def build_questionnaire_and_answers(assessment, question_map: Dict[str, Dict[str, Any]]):
    """Extract (questionnaire, answers) dicts from a loaded Assessment ORM object,
    in the exact shape the root scorer / report assembler expect.

    This is the single source of truth for BOTH report rendering and assessment
    scoring, so the two can never diverge.
    """
    responses = {r.question_id: r for r in (assessment.responses or [])}

    q_ids: List[str] = []
    if assessment.questionnaire and assessment.questionnaire.question_ids:
        q_ids = list(assessment.questionnaire.question_ids)
    if not q_ids:
        q_ids = list(responses.keys())
    seen, ordered_qids = set(), []
    for qid in q_ids:
        if qid not in seen:
            seen.add(qid)
            ordered_qids.append(qid)

    # When the assessment scopes to a domain subset, ``question_map`` only
    # contains the in-scope questions, so any qid missing from it (a stale
    # questionnaire entry or a response prefilled for an out-of-scope control)
    # is excluded — scoring then reflects the selected domains only.
    scoped = bool(getattr(assessment, "selected_domains", None))

    questions_list: List[Dict[str, Any]] = []
    answers_list: List[Dict[str, Any]] = []
    framework_name = None
    ev_by_control, ev_by_domain = _collect_request_evidence(assessment)

    for qid in ordered_qids:
        meta = question_map.get(qid)
        if meta is None:
            if scoped:
                continue
            meta = {}
        resp = responses.get(qid)
        framework_name = framework_name or meta.get("framework_name")
        control_code = meta.get("control_code") or (resp.control_id if resp else "") or ""
        q_entry = {
            "question_id": qid,
            "question_text": meta.get("text") or "",
            "question_type": meta.get("question_type") or "free_text",
            "weight": meta.get("weight") or 3,
            "control_id": control_code,
            "control_name": meta.get("control_name") or meta.get("sub_topic") or control_code,
            "control_statement": meta.get("control_statement") or "",
            "item_type": meta.get("item_type") or "",
            "scope_cadence": meta.get("scope_cadence") or "",
            "preferred_tooling": meta.get("preferred_tooling") or "",
            "maturity_guide": meta.get("maturity_guide") or {},
            "category_name": meta.get("category_name") or "",
            "domain_id": meta.get("domain_code") or "",
            "domain_name": meta.get("domain_name") or "",
            "sub_topic": meta.get("sub_topic") or control_code,
            "maturity_signals": meta.get("maturity_signals") or {},
            "gap_if_deficient": meta.get("gap_if_deficient") or "",
        }
        questions_list.append(q_entry)
        answers_list.append({
            "question_id": qid,
            "question_type": q_entry["question_type"],
            "question_text": q_entry["question_text"],
            "response": resp.response_value if resp else None,
            "note": resp.notes if resp else "",
            # Evidence accompanying this answer — feeds the 50% evidence half
            # of the per-question score in the root scorer.
            "evidence_preview": _question_evidence(
                resp, control_code, q_entry["domain_id"], ev_by_control, ev_by_domain
            ),
        })

    # AI rubric ratings (per-item 0-3 levels + strengths/gaps/recommendation)
    # produced by the Engagement-Document evaluation. These become the level
    # source for items with no manual anchor, so the score and the report read
    # the same numbers. Keyed by control_code.
    ai_item_ratings: Dict[str, Dict[str, Any]] = {}
    for cr in (getattr(assessment, "category_ratings", None) or []):
        for it in (getattr(cr, "item_ratings", None) or []):
            code = str(it.get("control_code") or "")
            if code:
                ai_item_ratings[code] = it

    # Restrict AI levels to controls that are in scope for this evaluation run.
    # category_ratings are stored at assessment level and may include ratings for
    # domains that are outside the current question_map — those must not influence
    # the rollup (category_ratings scope fix).
    in_scope_controls = {
        meta.get("control_code", "")
        for meta in question_map.values()
        if meta.get("control_code")
    }
    ai_item_levels = {
        code: it["level"]
        for code, it in ai_item_ratings.items()
        if isinstance(it.get("level"), int)
        and (not in_scope_controls or code in in_scope_controls)
    }

    questionnaire = {
        "questions": questions_list,
        "framework_name": framework_name or assessment.name,
        "name": assessment.name,
        "domain_name": assessment.organization or "",
        "ai_item_levels": ai_item_levels,
        "ai_item_ratings": ai_item_ratings,
    }
    answers = {
        "answers": answers_list,
        "questionnaire_id": assessment.id,
        "auditor": assessment.created_by or "Assessor",
        "answered_at": assessment.updated_at.isoformat() if assessment.updated_at else "",
        "domain_name": assessment.organization or "",
    }
    return questionnaire, answers


def compute_scoring(assessment, question_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Run the ROOT scorer for an assessment.

    When GROQ_API_KEY is available, uses LLM scoring that explicitly checks
    evidence-answer alignment (does the uploaded evidence actually support the
    stated answer?). Falls back to the deterministic scorer so results are
    always produced even without an LLM key.
    """
    questionnaire, answers = build_questionnaire_and_answers(assessment, question_map)
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if api_key:
        try:
            from backend.services.answer_questionnaire import _llm_score_in_memory
            from backend.core.utils import logger
            result = _llm_score_in_memory(questionnaire, answers, api_key=api_key)
            if result is not None:
                return result
        except Exception as exc:
            from backend.core.utils import logger
            logger.warning("LLM scoring failed in compute_scoring, using deterministic fallback: %s", exc)
    return _deterministic_fallback_score(questionnaire, answers)


def build_report_with_scoring(assessment, question_map: Dict[str, Dict[str, Any]]):
    """Return (scoring, report_data) from a single root computation, so the
    persisted assessment numbers and the rendered report can't diverge."""
    questionnaire, answers = build_questionnaire_and_answers(assessment, question_map)
    scoring = _deterministic_fallback_score(questionnaire, answers)
    return scoring, build_report_data(questionnaire, answers, scoring)


def build_assessment_report_data(assessment, question_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Build the cyber_prac report_data for a loaded Assessment ORM object."""
    _, report = build_report_with_scoring(assessment, question_map)
    return report
