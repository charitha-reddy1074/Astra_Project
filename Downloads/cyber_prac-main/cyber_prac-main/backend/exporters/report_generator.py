"""
report_generator.py — Deterministic observation and report-data builder.

Derives human-readable observations from raw questionnaire answers without
requiring an LLM, then assembles a structured report_data dict suitable for
rendering with web_ui/templates/questionnaire_report.html.

Steps:
  1. generate_observation()  — per-answer human-readable inference
  2. gap_flag()              — ✓ Addressed / ⚠ Partial / ✗ Missing
  3. aggregate_stats()       — yes/no counts, maturity bottleneck, evidence gap
  4. build_summary_sentences() — canned domain-level observation sentences
  5. derive_strengths/gaps   — from enriched review scores
  6. build_report_data()     — master function consumed by score_answers()
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.evaluation_metrics import market_recommendation

_MATURITY_ORDER = ["Initial", "Developing", "Defined", "Managed", "Optimizing"]

_MATURITY_MEANINGS: Dict[str, str] = {
    "Initial":     "ad hoc and undocumented processes",
    "Developing":  "basic processes being established",
    "Defined":     "processes documented and standardised",
    "Managed":     "processes measured and controlled with data",
    "Optimizing":  "continuous improvement driven by metrics",
}


# ── Per-answer observation generators ────────────────────────────────────────

def _observation_yes_no(response: Any, question_text: str) -> str:
    if str(response or "").lower() in ("yes", "y", "true", "1"):
        ctx = question_text[:70].rstrip("?").strip() if question_text else "this control"
        return f"Organisation confirmed: {ctx}."
    return "Not confirmed — this control may not be in place."


def _observation_maturity(response: Any) -> str:
    level = str(response or "").strip()
    meaning = _MATURITY_MEANINGS.get(level, "level not recognised")
    return f"Maturity is at '{level}', indicating {meaning}."


def _observation_evidence(response: Any, evidence_preview: str) -> str:
    resp = str(response or "").strip()
    if evidence_preview:
        fname = Path(resp).name if resp else "uploaded file"
        return f"Evidence provided: {fname}. Content extracted for review."
    if resp:
        return "Evidence file referenced but content could not be extracted."
    return "No evidence was uploaded for this question."


def _observation_free_text(response: Any, question_text: str) -> str:
    text = str(response or "").strip()
    if not text:
        return "No response provided — question was left unanswered."
    if len(text) < 20:
        return "Response is very brief and may lack sufficient detail."
    q_words = set(re.findall(r"\w+", question_text.lower())) if question_text else set()
    r_words = set(re.findall(r"\w+", text.lower()))
    overlap = len(q_words & r_words) / max(len(q_words), 1)
    if overlap >= 0.3:
        return "Response addresses the question with relevant context."
    return "Response provided but may not directly address the specific question."


def generate_observation(answer: Dict[str, Any]) -> str:
    """Derive a human-readable observation from a single answered question."""
    qtype = str(answer.get("question_type") or "free_text").lower()
    response = answer.get("response")
    question_text = str(answer.get("question_text") or "")
    evidence_preview = str(answer.get("evidence_preview") or "")

    if qtype in ("yes_no", "yes_no_with_detail"):
        return _observation_yes_no(response, question_text)
    if qtype == "maturity_rating":
        return _observation_maturity(response)
    if qtype == "evidence_upload":
        return _observation_evidence(response, evidence_preview)
    return _observation_free_text(response, question_text)


# ── Gap flag ──────────────────────────────────────────────────────────────────

def gap_flag(score: float, verdict: str) -> Dict[str, str]:
    """Return a display label and CSS class for the gap flag column."""
    v = str(verdict or "").lower()
    if score >= 80 or v == "strong":
        return {"label": "✓ Addressed", "css": "gap-addressed"}
    if score >= 50 or v == "partial":
        return {"label": "⚠ Partial", "css": "gap-partial"}
    return {"label": "✗ Missing", "css": "gap-missing"}


# ── Aggregate stats ───────────────────────────────────────────────────────────

def aggregate_stats(reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Count yes/no positives, maturity bottleneck, evidence gaps, verdict distribution."""
    stats: Dict[str, Any] = {
        "total": len(reviews),
        "yes_no_positive": 0, "yes_no_negative": 0, "yes_no_total": 0,
        "maturity_levels": [], "lowest_maturity": None,
        "missing_evidence": 0, "evidence_provided": 0,
        "free_text_empty": 0, "free_text_short": 0, "free_text_adequate": 0,
        "strong": 0, "partial": 0, "weak": 0, "missing": 0,
    }

    for r in reviews:
        verdict = str(r.get("verdict") or "").lower()
        qtype   = str(r.get("question_type") or "free_text").lower()
        resp    = str(r.get("response") or "").strip()

        if qtype in ("yes_no", "yes_no_with_detail"):
            stats["yes_no_total"] += 1
            if resp.lower() in ("yes", "y", "true", "1"):
                stats["yes_no_positive"] += 1
            else:
                stats["yes_no_negative"] += 1
        elif qtype == "maturity_rating":
            if resp in _MATURITY_ORDER:
                stats["maturity_levels"].append(resp)
        elif qtype == "evidence_upload":
            if resp:
                stats["evidence_provided"] += 1
            else:
                stats["missing_evidence"] += 1
        else:
            if not resp:
                stats["free_text_empty"] += 1
            elif len(resp) < 30:
                stats["free_text_short"] += 1
            else:
                stats["free_text_adequate"] += 1

        if verdict == "strong":  stats["strong"]  += 1
        elif verdict == "partial": stats["partial"] += 1
        elif verdict == "weak":    stats["weak"]    += 1
        elif verdict == "missing": stats["missing"] += 1

    if stats["maturity_levels"]:
        stats["lowest_maturity"] = min(
            stats["maturity_levels"],
            key=lambda m: _MATURITY_ORDER.index(m) if m in _MATURITY_ORDER else 99,
        )
    return stats


def build_summary_sentences(stats: Dict[str, Any]) -> str:
    """Generate canned observation sentences from aggregated counters."""
    parts: List[str] = []
    if stats["yes_no_total"] > 0:
        parts.append(
            f"{stats['yes_no_positive']} of {stats['yes_no_total']} yes/no controls were confirmed."
        )
    if stats["missing_evidence"] > 0:
        parts.append(f"{stats['missing_evidence']} control(s) have no evidence uploaded.")
    if stats["lowest_maturity"]:
        parts.append(f"Maturity bottleneck is at '{stats['lowest_maturity']}'.")
    if stats["free_text_empty"] > 0:
        parts.append(f"{stats['free_text_empty']} open-ended question(s) left unanswered.")
    return " ".join(parts) or "Assessment completed."


# ── Strengths / Gaps derivation ───────────────────────────────────────────────

def derive_strengths(reviews: List[Dict[str, Any]]) -> List[str]:
    result: List[str] = []
    for r in reviews:
        score   = float(r.get("score") or 0)
        verdict = str(r.get("verdict") or "")
        if score >= 80 and verdict == "strong":
            text = str(r.get("question_text") or r.get("question_id") or "")[:100]
            if text:
                result.append(text)
    return result


def derive_gaps(reviews: List[Dict[str, Any]]) -> List[str]:
    result: List[str] = []
    for r in reviews:
        score   = float(r.get("score") or 0)
        verdict = str(r.get("verdict") or "")
        if score < 50 or verdict in ("missing", "weak"):
            obs  = str(r.get("observation") or "")
            text = str(r.get("question_text") or "")[:80]
            desc = obs or text
            if desc:
                result.append(desc)
    return result


def _llm_summarize_insights(
    enriched: List[Dict[str, Any]],
    scoring: Dict[str, Any],
) -> tuple[Optional[List[str]], Optional[List[str]]]:
    """Call the LLM to produce 3-5 summarised strength bullets and 3-5 gap bullets.
    Returns (strengths, gaps) or (None, None) when the LLM is unavailable."""
    try:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            return None, None
        from backend.core.utils import LLMClient, parse_llm_json  # lazy import

        framework = str(
            scoring.get("framework_key") or scoring.get("framework_name") or "N/A"
        )
        total_score = float(scoring.get("control_score_percent") or 0)
        maturity = str(scoring.get("maturity_level") or "")

        # Build per-item lines, tagging score so the LLM knows which are strong/weak.
        lines: List[str] = []
        for r in enriched[:50]:
            qt = str(r.get("question_text") or r.get("control_name") or "")[:90]
            score = float(r.get("score") or 0)
            verdict = str(r.get("verdict") or "").lower()
            obs = str(r.get("observation") or "")[:120]
            tag = "STRONG" if (score >= 70 or verdict == "strong") else "WEAK"
            lines.append(f"[{tag}|{score:.0f}%] {qt} — {obs}")

        system_prompt = (
            "You are a senior cybersecurity assessor summarising a security assessment.\n"
            "Each item is tagged [STRONG|score%] or [WEAK|score%].\n\n"
            "Write 3-5 STRENGTHS (what is working well — based on STRONG items, mention specific "
            "controls by name) and 3-5 GAPS (what needs improvement — based on WEAK or low-scoring "
            "items, be specific about what is missing or partial).\n"
            "Rules:\n"
            "  - Each bullet is ONE clear sentence, 10-30 words.\n"
            "  - Name the actual security control or capability where possible.\n"
            "  - Always produce at least 3 gaps, even when most items are STRONG — "
            "highlight areas below 100%, missing evidence, or controls needing hardening.\n"
            '  - Return ONLY valid JSON: {"strengths": ["..."], "gaps": ["..."]}'
        )
        user_prompt = (
            f"Framework: {framework}  |  Score: {total_score:.1f}%  |  Maturity: {maturity}\n\n"
            + "\n".join(lines)
        )

        client = LLMClient(api_key=api_key, temperature=0.2)
        raw = client.invoke(system_prompt, user_prompt, max_tokens=700)
        parsed = parse_llm_json(raw)
        strengths = [str(s) for s in (parsed.get("strengths") or []) if s][:5]
        gaps = [str(g) for g in (parsed.get("gaps") or []) if g][:5]
        return strengths or None, gaps or None
    except Exception:
        pass
    return None, None


# ── Security level label ──────────────────────────────────────────────────────

# Appearance keyed by the framework's own risk rating, so the gauge colour always
# matches the displayed risk label. (Previously the colour was re-banded from the
# raw score with different thresholds, e.g. green only ≥90, which made an 85.4 /
# "Low" result paint yellow — the label and colour disagreed.)
_RISK_APPEARANCE: Dict[str, Dict[str, str]] = {
    "low":      {"css": "level-low",      "color": "#16a34a"},
    "medium":   {"css": "level-medium",   "color": "#ca8a04"},
    "high":     {"css": "level-high",     "color": "#ea580c"},
    "critical": {"css": "level-critical", "color": "#dc2626"},
}


def _security_level(score: float) -> Dict[str, str]:
    """Fallback score-banding used only when the risk rating is unknown.
    Ranges match evaluation_metrics: >=80 Low, 60-79 Medium, 40-59 High, <40 Critical."""
    if score >= 80:
        return {"label": "Low Risk",      "css": "level-low",      "color": "#16a34a"}
    if score >= 60:
        return {"label": "Medium Risk",   "css": "level-medium",   "color": "#ca8a04"}
    if score >= 40:
        return {"label": "High Risk",     "css": "level-high",     "color": "#ea580c"}
    return     {"label": "Critical Risk", "css": "level-critical", "color": "#dc2626"}


# The "Security Level Attained" widget describes how secure the org is, so its
# label must be POSITIVE polarity (higher score → better). The framework's risk
# rating is NEGATIVE polarity (Low risk = good), so showing the risk verbatim
# there reads backwards ("Low" under a security-level heading looks like weak
# security). Map the risk rating to a posture term for that widget.
_RISK_TO_POSTURE: Dict[str, str] = {
    "low":      "Strong",
    "medium":   "Moderate",
    "high":     "Weak",
    "critical": "Critical",
}


def _normalise_risk_key(risk_label: str) -> str:
    key = str(risk_label or "").strip().lower()
    if "critical" in key:
        return "critical"
    if key.startswith("low"):
        return "low"
    if key.startswith("medium") or key.startswith("moderate"):
        return "medium"
    if key.startswith("high"):
        return "high"
    return key


def _security_posture(risk_label: str, score: float) -> str:
    """Positive-polarity security posture for the 'Security Level Attained' widget."""
    posture = _RISK_TO_POSTURE.get(_normalise_risk_key(risk_label))
    if posture:
        return posture
    # Fallback from score when risk label is unknown.
    if score >= 80:
        return "Strong"
    if score >= 60:
        return "Moderate"
    if score >= 40:
        return "Weak"
    return "Critical"


def _risk_appearance(risk_label: str, score: float) -> Dict[str, str]:
    """Map the framework's risk rating to gauge css/colour.

    Falls back to score-banding when the label is empty/unrecognised. The
    "critical nc" (NIST) and similar variants normalise to "critical".
    """
    appearance = _RISK_APPEARANCE.get(_normalise_risk_key(risk_label))
    if appearance:
        return dict(appearance)
    return {k: v for k, v in _security_level(score).items() if k in ("css", "color")}


# ── Score distribution for CSS bar chart ─────────────────────────────────────

def _score_distribution(reviews: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets = [
        {"range": "0–20",   "min": 0,  "max": 20,  "count": 0},
        {"range": "21–40",  "min": 21, "max": 40,  "count": 0},
        {"range": "41–60",  "min": 41, "max": 60,  "count": 0},
        {"range": "61–80",  "min": 61, "max": 80,  "count": 0},
        {"range": "81–100", "min": 81, "max": 100, "count": 0},
    ]
    for r in reviews:
        score = float(r.get("score") or 0)
        for b in buckets:
            if b["min"] <= score <= b["max"]:
                b["count"] += 1
                break
    total_count = sum(b["count"] for b in buckets) or 1
    for b in buckets:
        b["pct"] = round(b["count"] / total_count * 100)
    return buckets


# ── Master function ───────────────────────────────────────────────────────────

_MARKET_LEVEL_META = [
    {"level": 0, "label": "Not in place",        "risk": "Critical", "color": "#dc2626"},
    {"level": 1, "label": "Partially in place",  "risk": "High",     "color": "#f97316"},
    {"level": 2, "label": "Passable",            "risk": "Medium",   "color": "#f59e0b"},
    {"level": 3, "label": "Strong",              "risk": "Low",      "color": "#16a34a"},
]


_MARKET_LABEL_LEVEL = {
    "not in place": 0,
    "partially in place": 1,
    "passable": 2,
    "strong": 3,
    # Numeric anchors (e.g. an AI prefill that read the 0-3 rubric) resolve too,
    # matching evaluation_metrics._MARKET_LABEL_LEVEL so report and score agree.
    "0": 0,
    "1": 1,
    "2": 2,
    "3": 3,
}


def _market_level(pct: float) -> int:
    if pct >= 83:
        return 3
    if pct >= 50:
        return 2
    if pct >= 17:
        return 1
    return 0


def build_market_analytics(
    questionnaire: Dict[str, Any],
    answers: Dict[str, Any],
    scoring: Dict[str, Any],
    enriched: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the Organizational Assessment 0-3 analytics block: control-wise and
    question-wise findings, per-domain achieved/possible roll-up, a domain×level
    heatmap, maturity comparison (achieved vs Strong target), and cross-domain
    risk trends. N/A items (no answered questions) are excluded from denominators.
    """
    # Control metadata from the questionnaire (name, type, domain, category).
    cmeta: Dict[str, Dict[str, Any]] = {}
    dmeta: Dict[str, str] = {}
    for q in questionnaire.get("questions", []):
        cid = q.get("control_id") or ""
        if not cid:
            continue
        dcode = q.get("domain_id") or ""
        dmeta.setdefault(dcode, q.get("domain_name") or dcode)
        cmeta.setdefault(cid, {
            "control_id": cid,
            "control_name": q.get("control_name") or q.get("sub_topic") or cid,
            "item_type": q.get("item_type") or "",
            "domain_code": dcode,
            "domain_name": q.get("domain_name") or dcode,
            "category_name": q.get("category_name") or "",
            "statement": q.get("control_statement") or "",
            "scope_cadence": q.get("scope_cadence") or "",
            "preferred_tooling": q.get("preferred_tooling") or "",
            "maturity_guide": q.get("maturity_guide") or {},
        })

    resp_map = {a.get("question_id"): a for a in answers.get("answers", [])}
    # AI rubric-derived per-item ratings (level 0-3 + strengths/gaps/reco), keyed
    # by control_id. The level source order matches evaluation_metrics
    # .resolve_market_level so the report and the persisted score never diverge.
    ai_ratings: Dict[str, Dict[str, Any]] = questionnaire.get("ai_item_ratings") or {}

    # Group reviews by control.
    by_control: Dict[str, List[Dict[str, Any]]] = {}
    for r in enriched:
        by_control.setdefault(r.get("control_id") or "", []).append(r)

    controls_out: List[Dict[str, Any]] = []
    for cid, revs in by_control.items():
        if not cid:
            continue
        meta = cmeta.get(cid, {"control_id": cid, "control_name": cid, "item_type": "",
                               "domain_code": "", "domain_name": "", "category_name": "", "statement": ""})
        # Weighted control score; N/A when nothing was answered. The item's 0-3
        # level follows the xlsx pattern: the assessor's single maturity rating
        # (the MULTI_CHOICE anchor) is authoritative when present; the supporting
        # questions provide evidence/context and a fallback roll-up.
        w_sum = w_tot = 0.0
        answered = False
        has_evidence = False
        anchor_level = None
        q_findings = []
        strengths, gaps = [], []
        notes = ""
        for r in revs:
            resp = r.get("response")
            if str(resp or "").strip():
                answered = True
            qid = r.get("question_id")
            if str((resp_map.get(qid) or {}).get("evidence_preview") or "").strip():
                has_evidence = True
            w = float(r.get("weight") or 3)
            sc = float(r.get("score") or 0)
            w_sum += sc * w
            w_tot += w
            resp_norm = str(resp or "").strip().lower()
            if str(r.get("question_type") or "").lower() == "multi_choice" and resp_norm in _MARKET_LABEL_LEVEL:
                anchor_level = _MARKET_LABEL_LEVEL[resp_norm]
            verdict = str(r.get("verdict") or "weak").lower()
            q_findings.append({
                "question_text": r.get("question_text") or "",
                "question_type": r.get("question_type") or "",
                "response": resp,
                "score": round(sc, 1),
                "verdict": verdict,
                "observation": r.get("observation") or "",
                "gap_flag": r.get("gap_flag") or "",
            })
            qt = str(r.get("question_text") or "")
            qt_lower = qt.lower()
            if sc >= 75:
                strengths.append(qt)
            elif sc < 50:
                resp_str = str(resp or "").strip()
                if resp_str:
                    # Context-aware gap text: distinguish deployment-absent,
                    # partial-coverage, and generic weak answers so the gap text
                    # matches what the evidence actually shows.
                    if (
                        str(r.get("question_type") or "").lower() == "yes_no"
                        and resp_str.upper() == "NO"
                        and "deploy" in qt_lower
                        and "coverage" not in qt_lower
                    ):
                        gaps.append(
                            "Control not fully deployed — establish a deployment "
                            "baseline and enforce across all in-scope systems."
                        )
                    elif (
                        str(r.get("question_type") or "").lower() == "yes_no"
                        and resp_str.upper() == "NO"
                        and "coverage" in qt_lower
                    ):
                        if notes and re.search(r"\d+\s*%", notes):
                            gaps.append(
                                "Coverage is incomplete — the gap percentage is "
                                "documented; close the remaining gap to reach "
                                "full coverage across all in-scope systems."
                            )
                        else:
                            gaps.append(
                                "Coverage is not complete — expand enforcement "
                                "to the full in-scope environment."
                            )
                    else:
                        gaps.append(r.get("observation") or qt)
                else:
                    # Null/blank response: flag specifically for evidence and
                    # current-state questions so they always appear as gaps
                    # even though the heuristic cannot score them.
                    if "supporting evidence" in qt_lower:
                        gaps.append(
                            "No supporting evidence provided — attach "
                            "documentation to substantiate this control."
                        )
                    elif "describe the current state" in qt_lower:
                        gaps.append(
                            "Current state not described — document what is in "
                            "place, known gaps, and any compensating controls."
                        )
            if "describe the current state" in qt_lower and str(resp or "").strip():
                notes = str(resp)

        na = not answered
        rollup_pct = round((w_sum / w_tot) if w_tot else 0.0, 1)   # weighted avg question score (0-100)
        ai = ai_ratings.get(cid) or {}
        ai_level = ai.get("level") if isinstance(ai.get("level"), int) else None
        # Level precedence (mirrors evaluation_metrics.resolve_market_level):
        # manual anchor -> AI rubric level -> capped weighted fallback.
        contradiction_note: str | None = None
        if anchor_level is not None:
            # Contradiction check: if the assessor's MULTI_CHOICE says Level ≥ 2
            # (Passable/Strong) but their own deployment YES/NO answered NO, the
            # two signals are inconsistent. Cap the level and flag it so the
            # evaluator can resolve the contradiction before finalising.
            if anchor_level >= 2:
                for _r in revs:
                    _qt = str(_r.get("question_text") or "").lower()
                    _resp = str(_r.get("response") or "").strip().upper()
                    if (
                        str(_r.get("question_type") or "").lower() == "yes_no"
                        and _resp == "NO"
                        and "deploy" in _qt
                        and "coverage" not in _qt
                    ):
                        _old_label = _MARKET_LEVEL_META[anchor_level]["label"]
                        anchor_level = min(anchor_level, 1)
                        level_source = "assessor-corrected"
                        contradiction_note = (
                            f"Assessor rated '{_old_label}' but the deployment "
                            f"YES/NO was answered NO — level capped at 1 "
                            f"(Partially in place). Resolve this inconsistency "
                            f"before finalising."
                        )
                        break
            level = anchor_level
            if not contradiction_note:
                level_source = "assessor"
        elif ai_level is not None:
            level = max(0, min(3, ai_level))
            level_source = "ai"
        else:
            level = _market_level(rollup_pct)
            if not has_evidence:
                level = min(level, 2)   # Strong needs an anchor, AI rating, or evidence
            level_source = "fallback"
        pct = round(level / 3 * 100, 1)   # xlsx pattern: score/3 (0-3 is authoritative)
        lvl_meta = _MARKET_LEVEL_META[level]

        # Strengths / gaps / recommendation: prefer the AI rubric evaluation
        # (grounded in the item's Technology/Process rubric, scope and evidence),
        # else the heuristic derivation + the deterministic next-level guidance.
        if ai.get("strengths"):
            strengths = list(ai["strengths"])
        if ai.get("gaps"):
            gaps = list(ai["gaps"])
        recommendation = ai.get("recommendation") or market_recommendation(
            level, meta["control_name"], meta.get("maturity_guide")
        )
        # Controls at level 0 or 1 must always surface at least one gap so the
        # Detailed Findings section never shows "None recorded" when improvement
        # is clearly needed. The default text is level-specific and actionable.
        if not gaps and not na and level <= 1:
            if level == 0:
                gaps = [
                    "Not implemented — establish this control from scratch: "
                    "assign ownership, define the process, and document a baseline."
                ]
            else:
                gaps = [
                    "Partially implemented — formalize the process, expand coverage "
                    "to all in-scope systems, and retain supporting evidence to progress."
                ]
        controls_out.append({
            **meta,
            "score_pct": pct,
            "level": level,
            "level_label": lvl_meta["label"],
            "level_source": level_source,
            "risk": lvl_meta["risk"],
            "color": lvl_meta["color"],
            "na": na,
            "coverage": ai.get("coverage") or "",
            "tooling_present": ai.get("tooling_present"),
            "evidence_class": ai.get("evidence_class") or "",
            "ai_rationale": ai.get("rationale") or "",
            "contradiction_note": contradiction_note,
            "strengths": strengths[:4],
            "gaps": gaps[:4],
            "recommendation": recommendation,
            "notes": notes,
            "questions": q_findings,
        })

    controls_out.sort(key=lambda c: (c["domain_code"], c["control_id"]))

    # Domain roll-up (achieved/possible, N/A excluded).
    domain_rollup = []
    dom_group: Dict[str, List[Dict[str, Any]]] = {}
    for c in controls_out:
        dom_group.setdefault(c["domain_code"], []).append(c)
    for dcode, items in dom_group.items():
        scored = [c for c in items if not c["na"]]
        achieved = sum(c["level"] for c in scored)
        possible = len(scored) * 3
        pct = round(achieved / possible * 100, 1) if possible else 0.0
        lvl = _market_level(pct)
        domain_rollup.append({
            "domain_code": dcode,
            "domain_name": dmeta.get(dcode, dcode),
            "items": len(items),
            "na_items": len(items) - len(scored),
            "achieved": achieved,
            "possible": possible,
            "pct": pct,
            "level": lvl,
            "level_label": _MARKET_LEVEL_META[lvl]["label"],
            "counts": [sum(1 for c in scored if c["level"] == L) for L in range(4)],
        })
    domain_rollup.sort(key=lambda d: d["domain_name"])

    total_achieved = sum(d["achieved"] for d in domain_rollup)
    total_possible = sum(d["possible"] for d in domain_rollup)
    overall_pct = round(total_achieved / total_possible * 100, 1) if total_possible else 0.0
    overall_level = _market_level(overall_pct)

    # Heatmap = domain × level counts (rows already carry counts + pct).
    heatmap = {
        "levels": _MARKET_LEVEL_META,
        "rows": [
            {"domain_code": d["domain_code"], "domain_name": d["domain_name"],
             "counts": d["counts"], "pct": d["pct"], "level": d["level"]}
            for d in domain_rollup
        ],
    }

    # Maturity comparison: achieved vs Strong (level 3 / 100%) target.
    maturity_comparison = [
        {
            "domain_code": d["domain_code"],
            "domain_name": d["domain_name"],
            "current_pct": d["pct"],
            "target_pct": 100.0,
            "avg_level": round(d["achieved"] / (d["possible"] / 3), 2) if d["possible"] else 0.0,
            "target_level": 3,
            "gap_pct": round(100.0 - d["pct"], 1),
        }
        for d in domain_rollup
    ]

    # Risk trends (cross-domain within this assessment).
    by_domain_sorted = sorted(domain_rollup, key=lambda d: d["pct"])
    scored_controls = [c for c in controls_out if not c["na"]]
    weakest_items = sorted(scored_controls, key=lambda c: (c["level"], c["score_pct"]))[:8]
    biggest_gaps = [c for c in scored_controls if c["level"] <= 1]
    risk_trends = {
        "by_domain": [
            {"domain_code": d["domain_code"], "domain_name": d["domain_name"],
             "pct": d["pct"], "level": d["level"],
             "risk": _MARKET_LEVEL_META[d["level"]]["risk"]}
            for d in by_domain_sorted
        ],
        "weakest_items": [
            {"control_id": c["control_id"], "control_name": c["control_name"],
             "domain_name": c["domain_name"], "item_type": c["item_type"],
             "level": c["level"], "level_label": c["level_label"], "score_pct": c["score_pct"]}
            for c in weakest_items
        ],
        "biggest_gaps": [
            {"control_id": c["control_id"], "control_name": c["control_name"],
             "domain_name": c["domain_name"], "level": c["level"],
             "level_label": c["level_label"],
             "gaps": c.get("gaps") or [],
             "contradiction_note": c.get("contradiction_note")}
            for c in sorted(biggest_gaps, key=lambda c: c["level"])
        ],
    }

    return {
        "is_market": True,
        "scale": {"min": 0, "max": 3, "levels": _MARKET_LEVEL_META},
        "overall": {
            "items": sum(d["items"] for d in domain_rollup),
            "na_items": sum(d["na_items"] for d in domain_rollup),
            "achieved": total_achieved,
            "possible": total_possible,
            "pct": overall_pct,
            "level": overall_level,
            "level_label": _MARKET_LEVEL_META[overall_level]["label"],
        },
        "controls": controls_out,
        "domain_rollup": domain_rollup,
        "heatmap": heatmap,
        "maturity_comparison": maturity_comparison,
        "risk_trends": risk_trends,
    }


def aggregate_market_analytics(per_market: list) -> "dict | None":
    """Aggregate a list of per-market analytics dicts into a combined view.

    For each control, the maturity level is averaged across markets that have a
    non-N/A response. Markets that have not assessed a control (na=True) are
    excluded from that control's average. Only submitted markets should be
    included in per_market (caller is responsible for filtering).
    """
    if not per_market:
        return None

    # Gather all control instances by control_id across markets.
    all_controls: Dict[str, list] = {}
    for analytics in per_market:
        for c in analytics.get("controls", []):
            cid = c["control_id"]
            all_controls.setdefault(cid, []).append(c)

    controls_out: List[Dict[str, Any]] = []
    for cid, instances in all_controls.items():
        non_na = [c for c in instances if not c.get("na")]
        ref = instances[0]  # use as metadata template
        if not non_na:
            controls_out.append({**ref, "na": True, "questions": []})
            continue
        avg_level = round(sum(c["level"] for c in non_na) / len(non_na))
        avg_score_pct = round(sum(c["score_pct"] for c in non_na) / len(non_na), 1)
        lvl_meta = _MARKET_LEVEL_META[avg_level]
        # Merge unique strengths and gaps across all markets.
        strengths = list(dict.fromkeys(
            s for c in non_na for s in (c.get("strengths") or [])
        ))[:4]
        gaps = list(dict.fromkeys(
            g for c in non_na for g in (c.get("gaps") or [])
        ))[:4]
        # Averaged level ≤ 1 must always have at least one gap surfaced.
        if not gaps and avg_level <= 1:
            if avg_level == 0:
                gaps = [
                    "Not implemented — establish from scratch, assign ownership, "
                    "and document the baseline process."
                ]
            else:
                gaps = [
                    "Partially implemented — formalize, expand coverage to all "
                    "in-scope systems, and retain supporting evidence."
                ]
        controls_out.append({
            **ref,
            "level": avg_level,
            "score_pct": avg_score_pct,
            "level_label": lvl_meta["label"],
            "risk": lvl_meta["risk"],
            "color": lvl_meta["color"],
            "na": False,
            "strengths": strengths,
            "gaps": gaps,
            # Per-question detail from the first non-NA market (representative sample).
            "questions": non_na[0].get("questions", []),
        })

    controls_out.sort(key=lambda c: (c["domain_code"], c["control_id"]))

    # Re-build domain rollup from aggregated controls.
    dom_group: Dict[str, list] = {}
    dmeta: Dict[str, str] = {}
    for c in controls_out:
        dom_group.setdefault(c["domain_code"], []).append(c)
        dmeta[c["domain_code"]] = c.get("domain_name", c["domain_code"])

    domain_rollup: List[Dict[str, Any]] = []
    for dcode, items in dom_group.items():
        scored = [c for c in items if not c.get("na")]
        achieved = sum(c["level"] for c in scored)
        possible = len(scored) * 3
        pct = round(achieved / possible * 100, 1) if possible else 0.0
        lvl = _market_level(pct)
        domain_rollup.append({
            "domain_code": dcode,
            "domain_name": dmeta.get(dcode, dcode),
            "items": len(items),
            "na_items": len(items) - len(scored),
            "achieved": achieved,
            "possible": possible,
            "pct": pct,
            "level": lvl,
            "level_label": _MARKET_LEVEL_META[lvl]["label"],
            "counts": [sum(1 for c in scored if c["level"] == L) for L in range(4)],
        })
    domain_rollup.sort(key=lambda d: d["domain_name"])

    total_achieved = sum(d["achieved"] for d in domain_rollup)
    total_possible = sum(d["possible"] for d in domain_rollup)
    overall_pct = round(total_achieved / total_possible * 100, 1) if total_possible else 0.0
    overall_level = _market_level(overall_pct)

    heatmap = {
        "levels": _MARKET_LEVEL_META,
        "rows": [
            {"domain_code": d["domain_code"], "domain_name": d["domain_name"],
             "counts": d["counts"], "pct": d["pct"], "level": d["level"]}
            for d in domain_rollup
        ],
    }
    maturity_comparison = [
        {
            "domain_code": d["domain_code"],
            "domain_name": d["domain_name"],
            "current_pct": d["pct"],
            "target_pct": 100.0,
            "avg_level": round(d["achieved"] / (d["possible"] / 3), 2) if d["possible"] else 0.0,
            "target_level": 3,
            "gap_pct": round(100.0 - d["pct"], 1),
        }
        for d in domain_rollup
    ]

    by_domain_sorted = sorted(domain_rollup, key=lambda d: d["pct"])
    scored_controls = [c for c in controls_out if not c.get("na")]
    weakest_items = sorted(scored_controls, key=lambda c: (c["level"], c["score_pct"]))[:8]

    risk_trends = {
        "by_domain": [
            {"domain_code": d["domain_code"], "domain_name": d["domain_name"],
             "pct": d["pct"], "level": d["level"],
             "risk": _MARKET_LEVEL_META[d["level"]]["risk"]}
            for d in by_domain_sorted
        ],
        "weakest_items": [
            {"control_id": c["control_id"], "control_name": c["control_name"],
             "domain_name": c["domain_name"], "item_type": c["item_type"],
             "level": c["level"], "level_label": c["level_label"],
             "score_pct": c["score_pct"]}
            for c in weakest_items
        ],
        "biggest_gaps": [
            {"control_id": c["control_id"], "control_name": c["control_name"],
             "domain_name": c["domain_name"], "level": c["level"],
             "level_label": c["level_label"],
             "gaps": c.get("gaps") or [],
             "contradiction_note": c.get("contradiction_note")}
            for c in sorted(
                [c for c in scored_controls if c["level"] <= 1],
                key=lambda c: c["level"]
            )
        ],
    }

    return {
        "is_market": True,
        "scale": {"min": 0, "max": 3, "levels": _MARKET_LEVEL_META},
        "markets_count": len(per_market),
        "overall": {
            "items": sum(d["items"] for d in domain_rollup),
            "na_items": sum(d["na_items"] for d in domain_rollup),
            "achieved": total_achieved,
            "possible": total_possible,
            "pct": overall_pct,
            "level": overall_level,
            "level_label": _MARKET_LEVEL_META[overall_level]["label"],
        },
        "controls": controls_out,
        "domain_rollup": domain_rollup,
        "heatmap": heatmap,
        "maturity_comparison": maturity_comparison,
        "risk_trends": risk_trends,
    }


def build_report_data(
    questionnaire: Dict[str, Any],
    answers: Dict[str, Any],
    scoring: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build the full report_data dict consumed by questionnaire_report.html.

    Enriches each question review with:
      - observation  (human-readable inference, no LLM)
      - gap_flag     (✓ Addressed / ⚠ Partial / ✗ Missing)
    Then aggregates domain-level stats, derives strengths/gaps, and identifies
    the top-3 vulnerabilities for the dashboard widget.
    """
    q_map = {q.get("question_id"): q for q in questionnaire.get("questions", [])}
    a_map = {a.get("question_id"): a for a in answers.get("answers", [])}
    reviews = list(scoring.get("question_reviews", []))

    # ── Enrich each review ────────────────────────────────────────────────────
    enriched: List[Dict[str, Any]] = []
    for rev in reviews:
        qid     = rev.get("question_id")
        ans     = a_map.get(qid, {})
        q       = q_map.get(qid, {})
        score   = float(rev.get("score") or 0)
        verdict = str(rev.get("verdict") or "weak").lower()

        obs = generate_observation({
            "question_type":    rev.get("question_type") or ans.get("question_type", "free_text"),
            "question_text":    rev.get("question_text") or ans.get("question_text", ""),
            "response":         rev.get("response") if rev.get("response") is not None
                                else ans.get("response"),
            "evidence_preview": ans.get("evidence_preview", ""),
        })

        enriched.append({
            **rev,
            "observation": obs,
            "gap_flag":    gap_flag(score, verdict),
            "weight":      q.get("weight") or rev.get("weight") or 3,
        })

    # ── Aggregate ─────────────────────────────────────────────────────────────
    stats   = aggregate_stats(enriched)
    summary = build_summary_sentences(stats)

    strengths = list(scoring.get("strengths") or [])
    gaps = (
        list(scoring.get("identified_gaps") or [])
        or list(scoring.get("gaps") or [])
    )

    # When scoring doesn't carry pre-computed insights (or has only 1-2 generic
    # items), ask the LLM for a concise executive-style summary.
    if not strengths or len(gaps) < 3:
        _llm_s, _llm_g = _llm_summarize_insights(enriched, scoring)
        if not strengths:
            strengths = _llm_s or derive_strengths(enriched)
        if len(gaps) < 3:
            gaps = _llm_g or gaps or derive_gaps(enriched)

    # ── Top 3 vulnerabilities (worst verdict, lowest score) ───────────────────
    _verdict_order = {"missing": 0, "weak": 1, "partial": 2, "strong": 3}
    sorted_reviews = sorted(
        enriched,
        key=lambda r: (
            _verdict_order.get(str(r.get("verdict") or "weak").lower(), 1),
            float(r.get("score") or 0),
        ),
    )
    top_vulnerabilities = []
    for r in sorted_reviews[:3]:
        top_vulnerabilities.append({
            "title":       str(r.get("question_text") or "")[:100],
            "observation": str(r.get("observation") or ""),
            "score":       float(r.get("score") or 0),
            "verdict":     str(r.get("verdict") or "weak"),
        })

    # ── Organizational Assessment 0-3 analytics (findings / heatmap / trends) ─────
    market_analytics = None
    if str(scoring.get("framework_key") or "") == "market-assessment":
        market_analytics = build_market_analytics(questionnaire, answers, scoring, enriched)

    # ── Security level widget data ────────────────────────────────────────────
    score_pct = float(scoring.get("control_score_percent") or 0)
    risk      = str(scoring.get("overall_risk_rating") or "Unknown")
    maturity  = str(scoring.get("maturity_level")      or "Unknown")
    # For the Market Assessment, the gauge follows the xlsx achieved/possible
    # roll-up (sum of item 0-3 scores ÷ items×3) so every number in the report
    # is consistent with the spreadsheet's scoring pattern.
    if market_analytics:
        overall = market_analytics.get("overall", {})
        score_pct = float(overall.get("pct") or 0)
        lvl = int(overall.get("level") or 0)
        risk = _MARKET_LEVEL_META[lvl]["risk"]
        maturity = overall.get("level_label") or _MARKET_LEVEL_META[lvl]["label"]
    # Colour the gauge from the authoritative risk rating so it matches the label.
    sec       = _risk_appearance(risk, score_pct)
    posture   = _security_posture(risk, score_pct)

    # ── Maturity-signal classification (framework's own ladder) ───────────────
    signal_maturity   = scoring.get("signal_maturity") or {}
    maturity_breakdown = list(scoring.get("maturity_breakdown") or [])

    return {
        "meta": {
            "questionnaire_id": answers.get("questionnaire_id") or answers.get("control_id") or "",
            "auditor":          answers.get("auditor") or "Unknown",
            "answered_at":      str(answers.get("answered_at") or ""),
            "scope":            answers.get("domain_name") or answers.get("control_id") or "",
            "framework":        questionnaire.get("framework_name") or questionnaire.get("name") or "N/A",
            "scoring_mode":     scoring.get("scoring_mode") or "deterministic",
        },
        "security_level": {
            # Gauge fill colour/css follow the authoritative risk rating. The
            # widget heading is "Security Level Attained", so its label is the
            # POSITIVE-polarity posture (Low risk → "Strong"); the raw risk
            # rating is kept in `risk` and shown as a separate chip.
            "css":           sec["css"],
            "color":         sec["color"],
            "label":         posture,
            "posture":       posture,
            "score":         round(score_pct, 1),
            "score_degrees": round(score_pct * 3.6, 1),
            "risk":          risk,
            "maturity":      maturity,
        },
        "top_vulnerabilities": top_vulnerabilities,
        "stats":              stats,
        "domain_summary":     summary,
        "enriched_reviews":   enriched,
        "strengths":          strengths,
        "gaps":               gaps,
        "score_distribution": _score_distribution(enriched),
        # Maturity-signal classification on the framework's own
        # Mature / Partial / Critical-Gap ladder.
        "signal_maturity":    signal_maturity,
        "maturity_breakdown": maturity_breakdown,
        "market_analytics":   market_analytics,
    }


