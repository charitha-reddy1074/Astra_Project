"""Scoring service — wired to the ROOT backend's evaluation_metrics.

Replaces the previous unweighted average with the root scoring intelligence:
  • domain weights        (governance/identify weighted higher than recover, etc.)
  • control criticality    (critical controls count double)
  • per-framework risk/maturity bands
  • evidence-quality grading for evidence-upload questions
Findings carry severity + gap description + recommendation for the report view.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.models.assessment import Assessment, Response, Finding, Score
from backend.core.evaluation_metrics import (
    get_framework_profile,
    score_evidence_quality,
    CRITICALITY_MULT,
)

_MATURITY_TIER_MAP = {"mature": 1.0, "critical_gap": 0.0, "critical gap": 0.0}
# Organizational Assessment 0-3 maturity labels → 0.0-1.0.
_MARKET_LEVEL_MAP = {
    "not in place": 0.0,
    "partially in place": 1.0 / 3.0,
    "passable": 2.0 / 3.0,
    "strong": 1.0,
}
_OPEN_ENDED_TYPES = {"INTERVIEW", "PRE_ASSESSMENT", "FREE_TEXT"}

# Root maturity labels → the frontend's 1-5 integer scale.
_MATURITY_INT = {
    "Optimizing": 5, "Managed": 4, "Defined": 3, "Developing": 2, "Initial": 1,
    "IG3": 5, "IG2": 4, "IG1 partial": 3, "Below IG1": 1,
    "Conformant": 5, "Minor NC": 4, "Major NC": 2, "Critical NC": 1,
}


class ScoringService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def calculate(
        self,
        assessment: Assessment,
        responses: list[Response],
        control_map: dict | None = None,
        framework_key: str = "nist-csf-2-0",
    ) -> tuple[list[Score], list[Finding], float, int]:
        """Weighted maturity scoring using the root evaluation_metrics profile."""
        control_map = control_map or {}
        profile = get_framework_profile(framework_key)
        domain_weights = profile["domain_weights"]
        criticality_fn = profile["criticality_fn"]
        classify = profile["classify"]

        domain_agg: dict[str, list[float]] = {}   # domain -> [weighted_sum, weight_total]
        w_sum = 0.0
        w_total = 0.0
        findings: list[Finding] = []
        answered = 0

        for resp in responses:
            if resp.score is None:
                continue
            answered += 1
            code = resp.control_id or ""
            domain = _domain_from_control(code)
            w_domain = domain_weights.get(domain.upper()) or domain_weights.get(domain) or 3.0
            criticality = criticality_fn(code)               # critical / standard / informational
            weight = w_domain * CRITICALITY_MULT.get(criticality, 1.0)

            w_sum += resp.score * weight
            w_total += weight
            bucket = domain_agg.setdefault(domain, [0.0, 0.0])
            bucket[0] += resp.score * weight
            bucket[1] += weight

            if resp.score < 0.5:
                ctrl = control_map.get(code)
                stmt = (getattr(ctrl, "statement", None) or "").strip() or code
                findings.append(Finding(
                    assessment_id=assessment.id,
                    control_id=code,
                    control_code=code,
                    framework_code=framework_key,
                    domain_code=domain,
                    title=f"Gap: {stmt[:80] + ('…' if len(stmt) > 80 else '')}",
                    gap_description=(
                        f"Response indicates control '{code}' is not fully implemented. "
                        f"Requirement: {stmt[:300]}"
                    ),
                    recommendation=(
                        f"Implement and document control {code} in line with the framework "
                        f"requirement, and retain operational evidence (logs, configs, reports)."
                    ),
                    severity=_severity(criticality, resp.score),
                    status="open",
                ))

        overall_pct = (w_sum / w_total * 100) if w_total else 0.0
        _risk, maturity_label = classify(overall_pct)
        maturity = _MATURITY_INT.get(maturity_label) or _pct_to_maturity(overall_pct)

        scores: list[Score] = [Score(
            assessment_id=assessment.id, level="framework",
            score=round(w_sum, 2), max_score=round(w_total, 2),
            percentage=round(overall_pct, 1), maturity_level=maturity,
            answered_questions=answered, total_questions=len(responses),
        )]
        for domain, (d_sum, d_w) in domain_agg.items():
            d_pct = (d_sum / d_w * 100) if d_w else 0.0
            scores.append(Score(
                assessment_id=assessment.id, level="domain", domain_code=domain,
                score=round(d_sum, 2), max_score=round(d_w, 2),
                percentage=round(d_pct, 1), maturity_level=_pct_to_maturity(d_pct),
                answered_questions=0, total_questions=0,
            ))

        return scores, findings, round(overall_pct, 1), maturity


def score_response(value: str | None, question_type: str = "YES_NO",
                   evidence_preview: str = "", framework_key: str = "nist-csf-2-0") -> float | None:
    """Convert a raw response value to a 0.0–1.0 score."""
    if value is None:
        return None
    v = value.upper().strip()

    tier = _MATURITY_TIER_MAP.get(v.lower())
    if tier is not None:
        return tier
    market = _MARKET_LEVEL_MAP.get(v.lower())
    if market is not None:
        return market
    if v in ("YES", "TRUE", "1"):
        return 1.0
    if v in ("NO", "FALSE", "0"):
        return 0.0
    if v == "PARTIAL":
        return 0.5

    try:
        n = float(v)
        if 1 <= n <= 5:
            return (n - 1) / 4.0
        return min(max(n, 0.0), 1.0)
    except ValueError:
        pass

    # evidence-upload questions: grade the supplied evidence (root rubric, 0-100 → 0-1)
    if str(question_type).upper() in ("EVIDENCE_UPLOAD", "EVIDENCE"):
        return score_evidence_quality(value, evidence_preview, framework_key) / 100.0

    if question_type in _OPEN_ENDED_TYPES:
        return 0.5 if value.strip() else None
    return 0.5 if value.strip() else None


# --- helpers ---

def _domain_from_control(control_id: str | None) -> str:
    if not control_id:
        return "unknown"
    return control_id.split("-")[0].split(".")[0]


def _pct_to_maturity(pct: float) -> int:
    if pct >= 80:
        return 5
    if pct >= 60:
        return 4
    if pct >= 40:
        return 3
    if pct >= 20:
        return 2
    return 1


def _severity(criticality: str, score: float) -> str:
    if criticality == "critical":
        return "critical" if score == 0.0 else "high"
    if criticality == "informational":
        return "low"
    return "medium"

