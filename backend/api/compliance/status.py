"""Deterministic status calculation.

NO LLM. NO NETWORK. This module is a pure function of its inputs, and that is the
whole point: an evaluation's status is reproducible and auditable.

The evidence gate
-----------------
A control can only reach PASS, PARTIAL or FAIL when at least one evidence item
carries readable content. Without that the result is INSUFFICIENT_EVIDENCE —
never a guess. This is enforced structurally, before any threshold is applied,
so no amount of answer text, LLM output or framework-specific cap can route
around it.

Score composition mirrors the existing deterministic engine
(``backend.services.answer_questionnaire._deterministic_fallback_score``): a
50/50 blend of the answer and the evidence, so a control cannot pass on a good
answer with no artefacts, nor on artefacts with a self-declared "yes".
"""
from __future__ import annotations

from typing import Sequence

from backend.api.compliance.adapters.base import FrameworkAdapter
from backend.api.compliance.confidence import is_contradictory
from backend.api.compliance.enums import ComplianceStatus
from backend.api.compliance.types import EvidenceRef, StatusDecision
from backend.core.evaluation_metrics import score_evidence_quality

#: Weight of the answer half of the blended score. Mirrors the existing
#: deterministic scorer's 50/50 answer/evidence blend.
ANSWER_WEIGHT = 0.5
EVIDENCE_WEIGHT = 0.5

#: Ceiling on the blended score when the evidence text contradicts the recorded
#: answer. Matches the cap the existing engine's rubric rules land on for
#: "implemented" answers backed by "not implemented" evidence (0-40 band).
_CONTRADICTION_SCORE_CAP = 0.35

#: Minimum deterministic evidence grade required to reach PASS, whatever the
#: blended score says. The 50/50 blend lets a fully confident answer carry a
#: weak artefact to the threshold on its own — a confident "yes" plus an
#: off-topic note lands exactly on it. Compliance cannot be concluded from
#: evidence the rubric only grades as weak, so PASS requires evidence in hand.
_MIN_EVIDENCE_FOR_PASS = 0.60

#: Response values that mean "this control does not apply here".
_NOT_APPLICABLE_TOKENS = frozenset({
    "n/a", "na", "not applicable", "not-applicable", "notapplicable", "none",
})

#: A control that produced no answer at all.
_NO_ANSWER = 0.0


def is_not_applicable(response_value: str | None) -> bool:
    return str(response_value or "").strip().lower() in _NOT_APPLICABLE_TOKENS


def usable_evidence(evidence: Sequence[EvidenceRef]) -> list[EvidenceRef]:
    """Evidence with something to grade — not merely a referenced filename."""
    return [e for e in evidence if e.has_content]


def grade_evidence(
    evidence: Sequence[EvidenceRef],
    response_value: str | None,
    framework_key: str,
) -> float:
    """0.0-1.0 evidence quality, reusing the existing deterministic rubric.

    Delegates to `backend.core.evaluation_metrics.score_evidence_quality`
    (operational 100 / policy-implemented 75 / policy-only 40 / filename-only 20
    / missing 0) on the concatenated evidence text, so a control's evidence
    score means exactly what it means in the published report.
    """
    usable = usable_evidence(evidence)
    if not usable:
        return 0.0
    text = "\n".join(e.text for e in usable if e.text.strip())
    if not text.strip():
        # Filenames only. score_evidence_quality would grade this 20
        # (filename_only); the evidence gate treats it as ungraded.
        return 0.0
    return max(0.0, min(1.0, score_evidence_quality(
        response_value or "evidence provided", text, framework_key,
    ) / 100.0))


def calculate_status(
    *,
    adapter: FrameworkAdapter,
    control_id: str,
    response_value: str | None,
    response_score: float | None,
    evidence: Sequence[EvidenceRef],
    clauses: Sequence[str] = (),
    applicable: bool = True,
) -> StatusDecision:
    """Derive the compliance status for one control. Pure and deterministic.

    Precedence, highest first:

    1. not applicable (explicit, or the caller marked it out of scope)
    2. no usable evidence          -> INSUFFICIENT_EVIDENCE   (the evidence gate)
    3. framework ceiling applied   (e.g. SOC 2 Type 1 period-evidence cap)
    4. score >= pass_threshold     -> PASS
    5. score > 0                   -> PARTIAL
    6. score == 0                  -> FAIL
    """
    reasons: list[str] = []
    framework_key = adapter.key

    if not applicable or is_not_applicable(response_value):
        return StatusDecision(
            status=ComplianceStatus.NOT_APPLICABLE,
            score=_NO_ANSWER,
            response_score=max(0.0, response_score or 0.0),
            evidence_score=0.0,
            reasons=("Marked not applicable for this assessment scope.",),
        )

    usable = usable_evidence(evidence)
    answer = max(0.0, min(1.0, response_score if response_score is not None else 0.0))

    if not usable:
        return StatusDecision(
            status=ComplianceStatus.INSUFFICIENT_EVIDENCE,
            score=_NO_ANSWER,
            response_score=answer,
            evidence_score=0.0,
            reasons=(
                "No evidence with readable content was supplied for this control, "
                "so no compliance conclusion can be drawn. A response value alone "
                "does not establish compliance.",
            ),
        )

    ev_score = grade_evidence(usable, response_value, framework_key)
    temporality = [adapter.classify_temporality(e.text) for e in usable]

    ev_score, cap_gaps = adapter.cap_evidence(
        control_id=control_id,
        evidence_score=ev_score,
        evidence_kinds=temporality,
    )
    reasons.extend(cap_gaps)

    blended = ANSWER_WEIGHT * answer + EVIDENCE_WEIGHT * ev_score
    threshold = adapter.spec.pass_threshold

    # The evidence text negates what the recorded answer asserts. Cap the
    # blended score before the threshold is applied, so a confident-sounding
    # answer cannot outrank its own contradicting artefacts.
    combined_text = "\n".join(e.text for e in usable)
    if is_contradictory(response_value, combined_text):
        if blended > _CONTRADICTION_SCORE_CAP:
            reasons.append(
                "The recorded answer asserts the control is in place but the "
                "evidence text describes it as absent or unimplemented, so the "
                f"score is capped at {_CONTRADICTION_SCORE_CAP:.0%} pending "
                "remediation and fresh evidence."
            )
        blended = min(blended, _CONTRADICTION_SCORE_CAP)

    if blended >= threshold:
        status = ComplianceStatus.PASS
        reasons.append(
            f"Blended coverage {blended:.0%} meets the {framework_key} pass "
            f"threshold of {threshold:.0%} (answer {answer:.0%}, evidence {ev_score:.0%})."
        )
    elif blended > 0.0:
        status = ComplianceStatus.PARTIAL
        reasons.append(
            f"Blended coverage {blended:.0%} is below the {threshold:.0%} pass "
            f"threshold (answer {answer:.0%}, evidence {ev_score:.0%}) but the "
            f"control is partly evidenced."
        )
    else:
        status = ComplianceStatus.FAIL
        reasons.append(
            "Evidence was supplied but neither the response nor the evidence "
            "demonstrates the requirement (answer 0%, evidence 0%)."
        )

    status = adapter.status_ceiling(status, evidence_kinds=temporality)
    if status is not ComplianceStatus.PASS and not cap_gaps:
        reasons.append("Framework assurance limits applied.")

    # Evidence-strength floor. A blend alone lets a confident answer carry weak
    # evidence across the threshold, so PASS additionally requires the evidence
    # half to stand on its own.
    if status is ComplianceStatus.PASS and ev_score < _MIN_EVIDENCE_FOR_PASS:
        status = ComplianceStatus.PARTIAL
        reasons.append(
            f"Blended coverage met the threshold but the evidence itself grades "
            f"at {ev_score:.0%}, below the {_MIN_EVIDENCE_FOR_PASS:.0%} required "
            "to conclude compliance, so the result is capped at PARTIAL."
        )

    return StatusDecision(
        status=status,
        score=blended,
        response_score=answer,
        evidence_score=ev_score,
        reasons=tuple(reasons),
    )
