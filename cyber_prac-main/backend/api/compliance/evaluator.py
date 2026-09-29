"""EvaluationResult construction.

NO LLM. This module assembles the persisted evaluation record from a
`ControlSpec` and the evidence gathered for it, by delegating the two decisions
to `status.py` and `confidence.py`. It owns no thresholds and no scoring of its
own — it only turns decisions into a self-describing, auditable record.

The reasoning string is assembled from the decision trace, not generated. That
matters for assurance work: every sentence a reviewer reads is traceable to a
rule, and the same inputs always yield the same words.
"""
from __future__ import annotations

from typing import Any, Sequence

from backend.api.compliance.adapters.base import FrameworkAdapter
from backend.api.compliance.confidence import calculate_confidence
from backend.api.compliance.enums import ComplianceStatus, Severity
from backend.api.compliance.status import calculate_status, is_not_applicable
from backend.api.compliance.types import (
    ControlSpec,
    EvaluationResult,
    EvidenceRef,
    summarise_evidence,
)

_SEVERITY_BY_CRITICALITY = {
    "critical": {
        ComplianceStatus.FAIL: Severity.CRITICAL,
        ComplianceStatus.PARTIAL: Severity.HIGH,
        ComplianceStatus.INSUFFICIENT_EVIDENCE: Severity.HIGH,
        ComplianceStatus.REVIEW_REQUIRED: Severity.MEDIUM,
        ComplianceStatus.NOT_EVALUATED: Severity.HIGH,
        ComplianceStatus.NOT_APPLICABLE: Severity.INFO,
        ComplianceStatus.PASS: Severity.INFO,
    },
    "standard": {
        ComplianceStatus.FAIL: Severity.HIGH,
        ComplianceStatus.PARTIAL: Severity.MEDIUM,
        ComplianceStatus.INSUFFICIENT_EVIDENCE: Severity.MEDIUM,
        ComplianceStatus.REVIEW_REQUIRED: Severity.LOW,
        ComplianceStatus.NOT_EVALUATED: Severity.MEDIUM,
        ComplianceStatus.NOT_APPLICABLE: Severity.INFO,
        ComplianceStatus.PASS: Severity.INFO,
    },
    "informational": {
        ComplianceStatus.FAIL: Severity.MEDIUM,
        ComplianceStatus.PARTIAL: Severity.LOW,
        ComplianceStatus.INSUFFICIENT_EVIDENCE: Severity.LOW,
        ComplianceStatus.REVIEW_REQUIRED: Severity.INFO,
        ComplianceStatus.NOT_EVALUATED: Severity.LOW,
        ComplianceStatus.NOT_APPLICABLE: Severity.INFO,
        ComplianceStatus.PASS: Severity.INFO,
    },
}

_DEFAULT_GAPS: dict[ComplianceStatus, str] = {
    ComplianceStatus.INSUFFICIENT_EVIDENCE: (
        "No evidence with readable content was supplied, so the requirement "
        "could not be evaluated."
    ),
    ComplianceStatus.NOT_EVALUATED: (
        "This control is in scope but has not been evaluated yet, so nothing can "
        "be said about it in either direction."
    ),
    ComplianceStatus.REVIEW_REQUIRED: (
        "Evidence was supplied but could not be confidently attributed to this "
        "control, so a reviewer must confirm or reject the mapping before the "
        "result means anything."
    ),
    ComplianceStatus.FAIL: (
        "The supplied evidence does not demonstrate that the requirement is met."
    ),
    ComplianceStatus.PARTIAL: (
        "Evidence is present but does not cover enough of the requirement to "
        "conclude compliance."
    ),
}

_DEFAULT_RECOMMENDATIONS: dict[ComplianceStatus, str] = {
    ComplianceStatus.NOT_EVALUATED: (
        "Run an evaluation for this control, or request the evidence it expects, "
        "so it stops sitting outside the coverage figures."
    ),
    ComplianceStatus.REVIEW_REQUIRED: (
        "Review the proposed evidence-to-control mappings and confirm or reject "
        "each one. A rejected mapping turns the control into a genuine evidence "
        "gap; a confirmed one lets the evaluation stand."
    ),
    ComplianceStatus.INSUFFICIENT_EVIDENCE: (
        "Collect an artefact that shows the requirement is met: a policy or "
        "procedure with its approval record, a configuration export, a dated "
        "walkthrough narrative, or a current-state screenshot."
    ),
    ComplianceStatus.FAIL: (
        "Remediate the control to satisfy the requirement, then re-evaluate with "
        "an artefact that evidences the implemented design."
    ),
    ComplianceStatus.PARTIAL: (
        "Supply evidence for the uncovered parts of the requirement and re-evaluate; "
        "the current evidence supports only part of the stated outcome."
    ),
}


def determine_severity(status: ComplianceStatus, criticality: str) -> Severity:
    table = _SEVERITY_BY_CRITICALITY.get(criticality, _SEVERITY_BY_CRITICALITY["standard"])
    return table.get(status, Severity.MEDIUM)


def _build_gaps(
    decision_reasons: Sequence[str],
    status: ComplianceStatus,
) -> tuple[str, ...]:
    """Gaps are specific, so they are carried on the decision trace itself."""
    gaps: list[str] = []
    for reason in decision_reasons:
        if reason.endswith(".") and (
            "Type 1" in reason or "Type 2" in reason
        ):
            gaps.append(reason)
    if not gaps and status in _DEFAULT_GAPS:
        gaps.append(_DEFAULT_GAPS[status])
    return tuple(gaps)


def _build_recommendation(
    adapter: FrameworkAdapter,
    status: ComplianceStatus,
    evidence: Sequence[EvidenceRef],
) -> str:
    temporality = [adapter.classify_temporality(e.text) for e in evidence if e.has_content]
    override = adapter.recommendation_for(status, evidence_kinds=temporality)
    if override:
        return override
    return _DEFAULT_RECOMMENDATIONS.get(status, "")


def _reasoning(
    *,
    adapter: FrameworkAdapter,
    control: ControlSpec,
    status: ComplianceStatus,
    decision_reasons: Sequence[str],
    confidence: float,
    confidence_factors: dict[str, float],
) -> str:
    """Assemble the human-readable trace. Every clause is rule-derived."""
    header = (
        f"{control.framework_name} {control.control_id} evaluated at "
        f"{adapter.spec.assurance.value} scope against requirement "
        f"{control.requirement.requirement_id}: {status.value}."
    )
    body = " ".join(decision_reasons)
    if status.is_conclusive:
        weakest = min(
            ((k, v) for k, v in confidence_factors.items() if k != "penalty"),
            key=lambda kv: kv[1],
            default=("coverage", 0.0),
        )
        tail = (
            f" Confidence {confidence:.0%} ({weakest[0]} is the weakest factor at "
            f"{weakest[1]:.0%})."
        )
    else:
        tail = " No compliance conclusion is asserted."
    return f"{header} {body}{tail}".strip()


def evaluate_control(
    *,
    adapter: FrameworkAdapter,
    control: ControlSpec,
    evidence: Sequence[EvidenceRef] = (),
    response_value: str | None = None,
    response_score: float | None = None,
    applicable: bool = True,
    mapping_requires_review: bool = False,
    metadata: dict[str, Any] | None = None,
) -> EvaluationResult:
    """Evaluate one control and return the full, persistable result.

    `response_score` is the existing platform's answer score for the control
    (0.0-1.0). Pass None when there is no recorded answer; the evidence gate
    still decides the outcome.

    `mapping_requires_review` comes from `mapping.controls_requiring_review`: it
    is True when *every* evidence-to-control edge for this control is still
    awaiting a human. The evaluator does not decide it, which keeps the review
    gate a property of the mapping rather than of the grade.
    """
    evidence = tuple(evidence)
    decision = calculate_status(
        adapter=adapter,
        control_id=control.control_id,
        response_value=response_value,
        response_score=response_score,
        evidence=evidence,
        clauses=control.requirement.clauses,
        applicable=applicable and not is_not_applicable(response_value),
        mapping_requires_review=mapping_requires_review,
    )
    confidence, factors = calculate_confidence(
        adapter=adapter,
        control_id=control.control_id,
        response_value=response_value,
        clauses=control.requirement.clauses,
        expected_evidence_types=control.expected_evidence_types,
        evidence=evidence,
        evidence_score=decision.evidence_score,
        status_is_conclusive=decision.status.is_conclusive,
    )

    gaps = _build_gaps(decision.reasons, decision.status)
    recommendation = "" if decision.status is ComplianceStatus.PASS else (
        _build_recommendation(adapter, decision.status, evidence)
    )

    return EvaluationResult(
        framework=control.framework,
        control_id=control.control_id,
        requirement=control.requirement.text,
        requirement_id=control.requirement.requirement_id,
        requirement_clauses=control.requirement.clauses,
        domain_code=control.domain_code,
        domain_name=control.domain_name,
        evidence_id=evidence[0].evidence_id if evidence else None,
        evidence_summary=summarise_evidence(evidence),
        evidence_refs=evidence,
        evidence_count=sum(1 for e in evidence if e.has_content),
        status=decision.status,
        confidence=confidence,
        confidence_factors=factors,
        reasoning=_reasoning(
            adapter=adapter,
            control=control,
            status=decision.status,
            decision_reasons=decision.reasons,
            confidence=confidence,
            confidence_factors=factors,
        ),
        gaps=gaps,
        recommendation=recommendation,
        score=decision.score,
        response_score=decision.response_score,
        evidence_score=decision.evidence_score,
        assurance_level=adapter.spec.assurance.value,
        criticality=control.criticality,
        severity=determine_severity(decision.status, control.criticality),
        metadata=dict(metadata or {}),
    )


def evaluate_controls(
    *,
    adapter: FrameworkAdapter,
    controls: Sequence[ControlSpec],
    evidence_by_control: dict[str, Sequence[EvidenceRef]] | None = None,
    response_by_control: dict[str, tuple[str | None, float | None]] | None = None,
    applicable: Sequence[str] | None = None,
    review_required: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[EvaluationResult]:
    """Evaluate a batch of controls.

    Every control in `controls` yields a result — a control with no evidence and
    no answer still produces an INSUFFICIENT_EVIDENCE row rather than being
    skipped, so coverage of the framework is always visible. Controls named in
    `review_required` are additionally held at REVIEW_REQUIRED.
    """
    evidence_by_control = evidence_by_control or {}
    response_by_control = response_by_control or {}
    in_scope = set(applicable) if applicable is not None else None
    held = {c.strip().upper() for c in (review_required or ())}
    results: list[EvaluationResult] = []
    for control in controls:
        value, score = response_by_control.get(control.control_id, (None, None))
        results.append(evaluate_control(
            adapter=adapter,
            control=control,
            evidence=evidence_by_control.get(control.control_id, ()),
            response_value=value,
            response_score=score,
            applicable=in_scope is None or control.control_id in in_scope,
            mapping_requires_review=control.control_id.strip().upper() in held,
            metadata=metadata,
        ))
    return results
