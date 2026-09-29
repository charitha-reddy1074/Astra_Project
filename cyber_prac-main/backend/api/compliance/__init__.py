"""Unified internal compliance model for evidence-based control evaluation.

The chain the whole package implements:

    Framework -> Control -> Requirement -> Evidence -> Evaluation -> Finding
             -> Recommendation

Layering (each module owns exactly one concern):

    dataset.py     read a drop-in framework dataset JSON (see module docstring)
    types.py       the value objects that cross layer boundaries
    enums.py       the closed vocabularies (status, confidence, severity, …)
    adapters/      framework abstraction: NIST CSF 2.0 and SOC 2 Type 1
    status.py      deterministic status calculation  (no LLM, ever)
    confidence.py  deterministic confidence scoring  (no LLM, ever)
    evidence.py    gather evidence for a control from existing storage
    evaluator.py   EvaluationResult construction     (no LLM, ever)
    service.py     DB orchestration, persistence, findings, audit trail

Everything from `status.py` through `evaluator.py` is pure and deterministic:
the same inputs always produce the same status. An LLM may *summarise* an
evaluation but may never decide one — a control with no evidence can only ever
reach INSUFFICIENT_EVIDENCE.
"""

from backend.api.compliance.enums import (
    AssuranceLevel,
    ComplianceStatus,
    ConfidenceBand,
    EvidenceKind,
    Severity,
)
from backend.api.compliance.types import (
    ControlSpec,
    DatasetControl,
    EvaluationResult,
    EvidenceRef,
    FrameworkSpec,
    RequirementSpec,
    StatusDecision,
)
from backend.api.compliance.adapters import (
    FrameworkAdapter,
    NistCsf20Adapter,
    Soc2Type1Adapter,
    get_adapter,
    list_adapters,
    supported_framework_keys,
)
from backend.api.compliance.evaluator import evaluate_control, evaluate_controls

__all__ = [
    "AssuranceLevel",
    "ComplianceStatus",
    "ConfidenceBand",
    "EvidenceKind",
    "Severity",
    "ControlSpec",
    "DatasetControl",
    "EvaluationResult",
    "EvidenceRef",
    "FrameworkSpec",
    "RequirementSpec",
    "StatusDecision",
    "FrameworkAdapter",
    "NistCsf20Adapter",
    "Soc2Type1Adapter",
    "get_adapter",
    "list_adapters",
    "supported_framework_keys",
    "evaluate_control",
    "evaluate_controls",
]
