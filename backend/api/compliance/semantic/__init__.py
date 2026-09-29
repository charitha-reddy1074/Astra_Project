"""Semantic (LLM-assisted) layer over the deterministic compliance core.

Import surface only. The layers below are separable on purpose:

* `retrieval` — which controls the model is allowed to talk about
* `grounding` — what it is allowed to read, deduplicated and size-capped
* `schema`   — the exact JSON it must return, and the set of control IDs
* `policy`   — the confidence band and assurance claims it is not allowed to make
* `client`   — the Groq call itself, with tiering, caching and timeouts
* `service`  — the orchestration that keeps the deterministic result authoritative

Nothing here writes to the database or changes a compliance status.
"""
from __future__ import annotations

from backend.api.compliance.semantic.client import (
    SemanticCall,
    SemanticEvaluator,
    SemanticError,
    SemanticTimeout,
    build_user_prompt,
    select_tier,
)
from backend.api.compliance.semantic.grounding import (
    GroundedContext,
    GroundedEvidence,
    build_grounded_context,
    dedupe_evidence,
    historical_context,
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
from backend.api.compliance.semantic.retrieval import (
    ControlRetriever,
    RetrievedControl,
    retrieve_relevant_controls,
)
from backend.api.compliance.semantic.schema import (
    SemanticAssessment,
    SemanticBatch,
    SemanticResult,
    parse_assessment,
)
from backend.api.compliance.semantic.service import SemanticService

__all__ = [
    "ConfidencePolicyInput",
    "ControlRetriever",
    "GroundedContext",
    "GroundedEvidence",
    "RetrievedControl",
    "SemanticAssessment",
    "SemanticBatch",
    "SemanticCall",
    "SemanticError",
    "SemanticEvaluator",
    "SemanticResult",
    "SemanticService",
    "SemanticTimeout",
    "build_grounded_context",
    "build_user_prompt",
    "cap_band",
    "check_assurance",
    "dedupe_evidence",
    "historical_context",
    "is_more_cautious",
    "needs_semantic_review",
    "parse_assessment",
    "policy_band",
    "resolve_confidence_band",
    "retrieve_relevant_controls",
    "select_tier",
]
