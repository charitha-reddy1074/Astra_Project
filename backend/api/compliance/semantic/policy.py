"""Confidence and assurance policy for the semantic layer.

The confidence band that reaches storage is computed here, from the
deterministic result and the evidence actually supplied. It is deliberately
*not* the number the model returned. A model that reads evidence confidently is
not evidence, and letting its confidence through would be the single easiest way
for this layer to make the platform look more certain than it is.

The policy, stated once:

* **HIGH** — deterministic evidence present *and* a strong framework match:
  evidence with readable content scored at the framework's pass threshold, and
  no coverage penalty pending.
* **MEDIUM** — a strong semantic match but incomplete evidence: real content
  that covers only part of the requirement, or an answer recorded without
  artefacts.
* **LOW** — ambiguous or insufficient evidence: nothing readable, or evidence
  that does not address the requirement at all.

The model may only ever move a result *down* this ladder. That asymmetry is the
point: a second opinion is allowed to withhold, never to promote.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.api.compliance.enums import (
    AssuranceLevel,
    ComplianceStatus,
    ConfidenceBand,
)
from backend.api.compliance.semantic.schema import SemanticAssessment

#: Ordering used to take the more conservative of two bands.
_BAND_SEVERITY: dict[ConfidenceBand, int] = {
    ConfidenceBand.HIGH: 3,
    ConfidenceBand.MEDIUM: 2,
    ConfidenceBand.LOW: 1,
    ConfidenceBand.VERY_LOW: 0,
}

#: Phrases that would widen a SOC 2 claim beyond what a Type 1 examination
#: supports. Checked on the model's own prose, because the deterministic path
#: already refuses to produce them.
_TYPE_2_OVERCLAIM_RE = re.compile(
    r"operating\s+effectiveness|"
    r"type\s*2|"
    r"operated\s+effectively|"
    r"effective\s+throughout\s+the\s+period|"
    r"soc\s*2\s+compliant|"
    r"fully\s+soc\s*2\s+compliant",
    re.IGNORECASE,
)

#: Phrases asserting compliance as a conclusion rather than describing evidence.
_COMPLIANT_CLAIM_RE = re.compile(
    r"\b(?:is|are)\s+(?:fully\s+)?soc\s*2\s+compliant\b|"
    r"\bwe\s+are\s+compliant\b|"
    r"\bguarantees?\s+compliance\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConfidencePolicyInput:
    """The facts the policy is allowed to reason about.

    All of it is deterministic: status and score from the Part 1 evaluator,
    coverage from the evidence set. Nothing here originates with a model.
    """

    status: ComplianceStatus
    score: float
    evidence_score: float
    evidence_count: int
    requirement_id: str = ""
    assurance_level: str = AssuranceLevel.POINT_IN_TIME.value
    response_score: float | None = None
    has_readable_evidence: bool = True

    @classmethod
    def from_context(cls, context: Any) -> "ConfidencePolicyInput":
        """Build from a `GroundedContext` without importing it (no cycle)."""
        deterministic = getattr(context, "deterministic", {}) or {}
        try:
            status = ComplianceStatus(str(deterministic.get("status", "")))
        except ValueError:
            status = ComplianceStatus.INSUFFICIENT_EVIDENCE
        readable = sum(
            1 for e in getattr(context, "evidence", ())
            if getattr(e, "text", "").strip()
        )
        return cls(
            status=status,
            score=_as_float(deterministic.get("score")),
            evidence_score=_as_float(deterministic.get("evidence_score")),
            evidence_count=int(deterministic.get("evidence_count") or readable),
            requirement_id=str(deterministic.get("requirement_id", "")),
            assurance_level=str(
                deterministic.get("assurance_level")
                or getattr(context, "assurance_level", AssuranceLevel.POINT_IN_TIME.value)
            ),
            response_score=(
                _as_float(deterministic["response_score"])
                if deterministic.get("response_score") is not None else None
            ),
            has_readable_evidence=readable > 0,
        )


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def policy_band(inputs: ConfidencePolicyInput) -> ConfidenceBand:
    """The band the policy allows, before the model is consulted."""
    if not inputs.has_readable_evidence or inputs.status is ComplianceStatus.INSUFFICIENT_EVIDENCE:
        return ConfidenceBand.LOW
    if inputs.status is ComplianceStatus.NOT_APPLICABLE:
        return ConfidenceBand.LOW
    if inputs.status is ComplianceStatus.FAIL:
        # A fail we are sure about is still a low-confidence *claim* about
        # coverage, but it is a definite negative, so it is not capped at LOW.
        return ConfidenceBand.MEDIUM

    # PASS / PARTIAL
    if inputs.evidence_count > 0 and inputs.evidence_score >= 0.70:
        return ConfidenceBand.HIGH
    if inputs.evidence_count > 0 and inputs.evidence_score >= 0.40:
        return ConfidenceBand.MEDIUM
    if inputs.evidence_count > 0:
        return ConfidenceBand.LOW
    return ConfidenceBand.MEDIUM if inputs.response_score else ConfidenceBand.LOW


def more_cautious(left: ConfidenceBand, right: ConfidenceBand) -> ConfidenceBand:
    """The more conservative of two bands."""
    return left if _BAND_SEVERITY[left] <= _BAND_SEVERITY[right] else right


def cap_band(band: ConfidenceBand, ceiling: ConfidenceBand) -> ConfidenceBand:
    """Lower `band` to `ceiling` when it exceeds it.

    Used where something is *missing* rather than weak — a review that never
    produced an answer cannot justify `HIGH`, however well-evidenced the control
    is, so the policy band is capped instead of being discarded.
    """
    return band if _BAND_SEVERITY[band] <= _BAND_SEVERITY[ceiling] else ceiling


def resolve_confidence_band(
    inputs: ConfidencePolicyInput,
    assessment: SemanticAssessment | None,
) -> ConfidenceBand:
    """Combine the policy band with the model's reading.

    The model can lower the band; it can never raise it above what the
    deterministic evidence supports. `assessment is None` (the model failed,
    timed out, or was never called) leaves the policy band untouched.
    """
    base = policy_band(inputs)
    if assessment is None:
        return base

    if assessment.evidence_strength in ("none", "weak"):
        return more_cautious(base, ConfidenceBand.LOW)
    if assessment.evidence_strength == "moderate":
        return more_cautious(base, ConfidenceBand.MEDIUM)

    # "strong" evidence: the model agrees the evidence is good, but the
    # deterministic coverage is still the ceiling on the claim.
    if assessment.confidence < 0.5:
        return more_cautious(base, ConfidenceBand.MEDIUM)
    return base


def assurance_violation(text: str, assurance_level: str) -> str:
    """Return a description of an overclaim, or "" when the text is clean."""
    if assurance_level != AssuranceLevel.TYPE_1.value:
        return ""
    match = _TYPE_2_OVERCLAIM_RE.search(text or "")
    if match:
        return (
            f"text asserts '{match.group(0)}', which a {AssuranceLevel.TYPE_1.value} "
            f"examination cannot establish (it covers design and implementation "
            f"as of the assessment date, not operation over a period)"
        )
    match = _COMPLIANT_CLAIM_RE.search(text or "")
    if match:
        return (
            f"text asserts '{match.group(0)}'; a single automated evaluation "
            f"cannot establish organisation-wide compliance"
        )
    return ""


def check_assurance(
    assessment: SemanticAssessment | None,
    assurance_level: str,
) -> str:
    """Guard the model's own prose against widening the assurance claim."""
    if assessment is None:
        return ""
    parts = [assessment.reasoning, *assessment.identified_gaps, *assessment.recommended_actions]
    for part in parts:
        violation = assurance_violation(part or "", assurance_level)
        if violation:
            return violation
    return ""


def is_more_cautious(
    model_decision: str,
    authoritative_status: str,
) -> bool:
    """True when the model is stricter than the deterministic result.

    A model that is *less* strict is treated as a disagreement to be recorded,
    never as grounds to change the claim.
    """
    try:
        model = ComplianceStatus(model_decision)
    except ValueError:
        return False
    authoritative = ComplianceStatus(authoritative_status)
    if model is authoritative:
        return False
    # "We cannot tell" is always the more cautious reading; a FAIL is more
    # cautious than a PASS, a PARTIAL more cautious than a PASS.
    if model is ComplianceStatus.INSUFFICIENT_EVIDENCE:
        return True
    if authoritative is ComplianceStatus.PASS:
        return model in (ComplianceStatus.FAIL, ComplianceStatus.PARTIAL)
    return False


__all__ = [
    "ConfidencePolicyInput",
    "assurance_violation",
    "cap_band",
    "check_assurance",
    "is_more_cautious",
    "more_cautious",
    "policy_band",
    "resolve_confidence_band",
]
