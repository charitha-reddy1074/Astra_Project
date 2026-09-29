"""Orchestration: deterministic result in, recorded semantic result out.

The ordering here is the whole design. The deterministic evaluation is computed
first and is the platform's claim. The model is then asked whether the evidence
supports that claim, and its answer is recorded next to it — never merged into
it. Nothing in this module can change a status, a score, or a persisted
`ComplianceEvaluation` row, which is what keeps Part 1 authoritative and what
makes it safe to enable this layer on a live assessment.

Every path through here terminates in a `SemanticResult`. There is no exception
path that leaves a caller without an answer, because a missing semantic opinion
is a normal outcome, not an error.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from backend.api.compliance.enums import ComplianceStatus, ConfidenceBand
from backend.api.compliance.semantic.client import (
    SemanticCall,
    SemanticEvaluator,
    TIER_FAST,
    TIER_STRONG,
)
from backend.api.compliance.semantic.grounding import (
    GroundedContext,
    build_grounded_context,
    needs_semantic_review,
)
from backend.api.compliance.semantic.policy import (
    ConfidencePolicyInput,
    cap_band,
    check_assurance,
    is_more_cautious,
    policy_band,
    resolve_confidence_band,
)
from backend.api.compliance.semantic.retrieval import ControlRetriever
from backend.api.compliance.semantic.schema import SemanticResult
from backend.api.compliance.types import ControlSpec, EvaluationResult, EvidenceRef

logger = logging.getLogger(__name__)

#: How many controls are offered to the model per control evaluation. A single
#: control does not need its neighbours to be assessed, and offering more is the
#: fastest way to invite a model to answer about a control nobody asked about.
_TOP_K = 5


def _fallback_reasoning(result: EvaluationResult, reason: str) -> str:
    """The explanation shown in place of a model reading.

    Rule-derived and specific, so a reviewer is told *why* there is no semantic
    opinion rather than just seeing an empty field.
    """
    return (
        f"No semantic review was applied to {result.framework} "
        f"{result.control_id}: {reason}. The recorded result is the deterministic "
        f"evaluation ({result.status.value}, confidence "
        f"{result.confidence:.0%}, {result.evidence_count} readable evidence "
        f"item(s)), which remains authoritative."
    )


def _fallback_band(inputs: ConfidencePolicyInput) -> ConfidenceBand:
    """Band for a control that got no usable model answer.

    `HIGH` requires a strong framework match, and a review that never produced an
    answer has none, so the band is capped at `MEDIUM`. Below that the policy
    band is kept, so a well-evidenced control and a thinly-evidenced one are not
    reported identically. With no readable evidence at all it is `LOW`.
    """
    if not inputs.has_readable_evidence:
        return ConfidenceBand.LOW
    return cap_band(policy_band(inputs), ConfidenceBand.MEDIUM)


@dataclass
class SemanticService:
    """Applies the semantic layer to deterministic evaluations.

    `retriever` is built over the same in-scope `ControlSpec` list the
    deterministic run used, so retrieval cannot widen an assessment's scope and
    a control the assessment excluded is never offered to the model.
    """

    evaluator: SemanticEvaluator
    retriever: ControlRetriever
    top_k: int = _TOP_K
    #: Filled in with the assurance-violation text when a model answer was
    #: rejected for overclaiming, so the caller can surface it to a reviewer.
    last_assurance_warning: str = ""

    @classmethod
    def build(
        cls,
        controls: Sequence[ControlSpec] = (),
        **kwargs: Any,
    ) -> "SemanticService":
        """Construct a service with the production retriever and client."""
        return cls(
            evaluator=SemanticEvaluator(**kwargs),
            retriever=ControlRetriever(list(controls)),
            top_k=kwargs.get("top_k", _TOP_K),
        )

    # ── single control ─────────────────────────────────────────────────────

    def evaluate_control(
        self,
        *,
        control: ControlSpec,
        result: EvaluationResult,
        evidence: Sequence[EvidenceRef] = (),
        prior_results: Sequence[Mapping[str, Any]] = (),
        query: str = "",
    ) -> SemanticResult:
        """Produce the recorded semantic result for one evaluated control."""
        authoritative = result.status.value

        if not evidence:
            # No evidence was gathered at all: the deterministic evaluator has
            # already returned INSUFFICIENT_EVIDENCE, and a model reading of
            # nothing is both worthless and a hallucination risk.
            self.last_assurance_warning = ""
            return SemanticResult(
                framework=result.framework,
                control_id=result.control_id,
                authoritative_status=authoritative,
                confidence_band=ConfidenceBand.LOW,
                model_invoked=False,
                fallback_reason="no evidence supplied",
                fallback_reasoning=_fallback_reasoning(result, "no evidence supplied"),
            )

        retrieval_query = query or self._query_for(control, evidence)
        retrieved = self.retriever.with_controls(self.retriever.controls).retrieve(
            retrieval_query,
            framework=control.framework,
            top_k=self.top_k,
            # The control under evaluation is always in the candidate set, so a
            # narrow query can never exclude the control we are asking about.
            control_ids=[c.control_id for c in self.retriever.controls]
            if not self.retriever.controls else None,
        )
        if not any(c.control_id == control.control_id for c in retrieved):
            # The control being evaluated is not in the retriever's set (e.g.
            # a caller supplied an ad-hoc control). Offer it directly.
            retrieved = self._with_focus_control(control, retrieved)

        context = build_grounded_context(
            control=control,
            retrieved=retrieved,
            evidence=evidence,
            deterministic=result,
            prior_results=prior_results,
        )

        policy_inputs = ConfidencePolicyInput.from_context(context)

        if not needs_semantic_review(context):
            reason = self._skip_reason(context)
            self.last_assurance_warning = ""
            return SemanticResult(
                framework=result.framework,
                control_id=result.control_id,
                authoritative_status=authoritative,
                confidence_band=ConfidenceBand.LOW,
                model_invoked=False,
                fallback_reason=reason,
                fallback_reasoning=_fallback_reasoning(result, reason),
            )

        call = self.evaluator.evaluate(context)
        return self._to_result(
            context=context,
            result=result,
            policy_inputs=policy_inputs,
            call=call,
        )

    # ── batch ──────────────────────────────────────────────────────────────

    def evaluate_run(
        self,
        *,
        results: Sequence[EvaluationResult],
        controls_by_id: Mapping[str, ControlSpec],
        evidence_by_control: Mapping[str, Sequence[EvidenceRef]] | None = None,
        prior_results: Sequence[Mapping[str, Any]] = (),
    ) -> list[SemanticResult]:
        """Apply the layer across a whole deterministic run.

        Historical context is the prior run's results, which is what lets the
        model say "this was INSUFFICIENT_EVIDENCE last quarter" instead of
        restating the same gap in different words.
        """
        evidence_by_control = evidence_by_control or {}
        history = list(prior_results) + [
            {
                "control_id": r.control_id,
                "status": r.status.value,
                "confidence": round(r.confidence, 3),
                "evaluated_at": r.evaluated_at.isoformat(),
            }
            for r in results
        ]
        out: list[SemanticResult] = []
        for result in results:
            control = controls_by_id.get(result.control_id)
            if control is None:
                out.append(SemanticResult(
                    framework=result.framework,
                    control_id=result.control_id,
                    authoritative_status=result.status.value,
                    confidence_band=ConfidenceBand.LOW,
                    model_invoked=False,
                    fallback_reason="control not available to the semantic layer",
                    fallback_reasoning=_fallback_reasoning(
                        result, "the control was not available for semantic review",
                    ),
                ))
                continue
            out.append(self.evaluate_control(
                control=control,
                result=result,
                evidence=evidence_by_control.get(result.control_id, ()),
                prior_results=history,
            ))
        return out

    # ── helpers ────────────────────────────────────────────────────────────

    def _to_result(
        self,
        *,
        context: GroundedContext,
        result: EvaluationResult,
        policy_inputs: ConfidencePolicyInput,
        call: SemanticCall,
    ) -> SemanticResult:
        authoritative = result.status.value
        self.last_assurance_warning = ""

        if call.assessment is None:
            reason = call.error or "no usable model answer"
            return SemanticResult(
                framework=result.framework,
                control_id=result.control_id,
                authoritative_status=authoritative,
                confidence_band=_fallback_band(policy_inputs),
                model_invoked=call.cache_hit or call.model_tier != "",
                model_tier=call.model_tier,
                cache_hit=call.cache_hit,
                fallback_reason=reason,
                fallback_reasoning=_fallback_reasoning(result, reason),
            )

        assessment = call.assessment

        # An assurance overclaim invalidates the whole reading: the phrasing is
        # the problem, not one field of it, so the model opinion is dropped and
        # the deterministic result stands.
        violation = check_assurance(assessment, context.assurance_level)
        if violation:
            self.last_assurance_warning = violation
            return SemanticResult(
                framework=result.framework,
                control_id=result.control_id,
                authoritative_status=authoritative,
                confidence_band=ConfidenceBand.LOW,
                model_invoked=True,
                model_tier=call.model_tier,
                cache_hit=call.cache_hit,
                fallback_reason=f"assurance overclaim rejected: {violation}",
                fallback_reasoning=_fallback_reasoning(
                    result, f"the model answer was rejected because it {violation}",
                ),
            )

        band = resolve_confidence_band(policy_inputs, assessment)
        return SemanticResult(
            framework=result.framework,
            control_id=result.control_id,
            authoritative_status=authoritative,
            confidence_band=band,
            model_invoked=True,
            assessment=assessment,
            agrees_with_deterministic=assessment.decision == authoritative,
            more_cautious_than_deterministic=is_more_cautious(
                assessment.decision, authoritative,
            ),
            model_tier=call.model_tier,
            cache_hit=call.cache_hit,
        )

    @staticmethod
    def _skip_reason(context: GroundedContext) -> str:
        if not context.has_readable_evidence:
            return "no readable evidence to interpret"
        if context.deterministic.get("status") == ComplianceStatus.NOT_APPLICABLE.value:
            return "the control is not applicable to this assessment"
        return "a semantic review would not add anything"

    @staticmethod
    def _with_focus_control(
        control: ControlSpec,
        retrieved: Sequence[Any],
    ) -> tuple[Any, ...]:
        """Prepend the control under evaluation so it is always offered."""
        from backend.api.compliance.semantic.retrieval import RetrievedControl

        focus = RetrievedControl(
            control_id=control.control_id,
            statement=(control.statement or control.requirement.text)[:600],
            framework=control.framework,
            framework_name=control.framework_name,
            requirement_id=control.requirement.requirement_id,
            requirement=control.requirement.text[:600],
            clauses=control.requirement.clauses,
            domain_code=control.domain_code,
            domain_name=control.domain_name,
            criticality=control.criticality,
            expected_evidence_types=control.expected_evidence_types,
            score=1.0,
        )
        return (focus, *tuple(retrieved))

    @staticmethod
    def _query_for(control: ControlSpec, evidence: Sequence[EvidenceRef]) -> str:
        """Build the retrieval query from the requirement and the evidence.

        Deliberately not the evidence alone: the requirement is what decides
        which controls are relevant, and the evidence text is what decides
        which of them this submission speaks to.
        """
        parts = [
            control.requirement.requirement_id,
            control.domain_name,
            control.statement or control.requirement.text,
            *control.requirement.clauses[:3],
        ]
        for ref in list(evidence)[:3]:
            if ref.text.strip():
                parts.append(ref.text[:400])
        return " ".join(p for p in parts if p)


__all__ = ["SemanticService", "TIER_FAST", "TIER_STRONG"]
