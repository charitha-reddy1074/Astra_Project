"""Hindsight — the persistent organisational memory of the compliance agent.

Separable layers, each owning one concern:

* `enums`   — the closed vocabularies (memory events, classifications, statuses)
* `schema`  — the structured contract the model must satisfy when enriching
* `rules`   — the deterministic, pure classifier + the memory context assembly
* `enrich`  — the Groq call that narrates relationship-to-history, advisory only
* `service` — DB orchestration: reading the past, classifying, persisting memory

The invariant every module repeats: classification is deterministic and
authoritative (the model never changes a label), memory is organisation-scoped
(never shared across organisations), and a previous exception explains a
finding without ever hiding it.
"""
from __future__ import annotations

from backend.api.compliance.memory.enums import (
    ExceptionStatus,
    FindingType,
    HumanDecision,
    MemoryClassification,
    MemoryEvent,
    RemediationStatus,
)
from backend.api.compliance.memory.enrich import (
    MemoryEnrichUnavailable,
    MemoryEnricher,
    build_enrichment_prompt,
)
from backend.api.compliance.memory.rules import (
    Classification,
    ExceptionView,
    MemoryContext,
    PriorFinding,
    assemble_context,
    classify_finding,
    matched_memories,
)
from backend.api.compliance.memory.schema import (
    MemoryEnrichment,
    parse_enrichment,
)
from backend.api.compliance.memory.service import (
    MemoryError,
    MemoryReviewRun,
    MemoryService,
    organization_key,
)

__all__ = [
    "Classification",
    "ExceptionStatus",
    "ExceptionView",
    "FindingType",
    "HumanDecision",
    "MemoryClassification",
    "MemoryContext",
    "MemoryEnrichUnavailable",
    "MemoryEnrichment",
    "MemoryEnricher",
    "MemoryError",
    "MemoryEvent",
    "MemoryReviewRun",
    "MemoryService",
    "PriorFinding",
    "RemediationStatus",
    "assemble_context",
    "build_enrichment_prompt",
    "classify_finding",
    "matched_memories",
    "organization_key",
    "parse_enrichment",
]