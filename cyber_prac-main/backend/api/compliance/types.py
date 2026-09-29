"""Value objects exchanged between the compliance layers.

Frozen dataclasses, no ORM and no FastAPI imports, so the whole package stays
importable from a unit test with no database, no vector store and no LLM key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from backend.api.compliance.enums import (
    AssuranceLevel,
    ComplianceStatus,
    ConfidenceBand,
    EvidenceKind,
    EvidenceNature,
    Severity,
)


#: Human labels for the persisted nature strings, kept out of the dataclass so
#: `types.py` stays free of the heuristics that decide a nature.
_NATURE_LABELS: dict[str, str] = {n.value: n.label for n in EvidenceNature}


# ── Framework / Control / Requirement ───────────────────────────────────────

@dataclass(frozen=True)
class RequirementSpec:
    """The requirement a control is evaluated against.

    `requirement_id` is the stable identifier of the requirement inside the
    framework dataset (for NIST the Category, e.g. `GV.OC`; for SOC 2 the
    criteria series, e.g. `CC1`), and `text` is the verbatim requirement
    statement taken from the dataset. `clauses` carries the dataset's finer-grained
    decomposition when it publishes one (SOC 2 `points_of_focus`, NIST outcome
    statement) — those are the specific things evidence is matched against.
    """
    requirement_id: str
    text: str
    clauses: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "text": self.text,
            "clauses": list(self.clauses),
        }


@dataclass(frozen=True)
class ControlSpec:
    """One evaluable control, resolved from the framework dataset.

    Deliberately not the SQLAlchemy `Control` row: this is the *dataset view*
    (framework identity + requirement), assembled by an adapter so evaluation
    can be unit-tested without a database. The DB row is the persistence of it.
    """
    framework: str
    framework_name: str
    control_id: str
    requirement: RequirementSpec
    domain_code: str = ""
    domain_name: str = ""
    statement: str = ""
    criticality: str = "standard"
    expected_evidence_types: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FrameworkSpec:
    """Framework identity plus the policy that governs its evaluations."""
    key: str
    name: str
    version: str = ""
    assurance: AssuranceLevel = AssuranceLevel.POINT_IN_TIME
    description: str = ""
    #: Human-readable restatement of what this evaluation does and does not claim.
    assurance_statement: str = ""
    #: score >= threshold => PASS (see status.py)
    pass_threshold: float = 0.70

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "version": self.version,
            "assurance_level": self.assurance.value,
            "description": self.description,
            "assurance_statement": self.assurance_statement,
            "pass_threshold": self.pass_threshold,
        }


# ── Evidence ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceRef:
    """A single piece of evidence considered for a control.

    `text` is the already-extracted preview the rest of the platform writes
    alongside every uploaded artefact (the `<file>.preview.txt` sidecar), so
    evidence quality can be graded with the same deterministic rubric the
    existing report uses.

    `format`, `quality` and `content_hash` are the output of evidence
    *normalisation* (see `compliance/normalization.py`) and are advisory: they
    describe the artefact for the report and for cost accounting, and never
    change the deterministic status — that is decided by the evidence text
    through the existing rubric.
    """
    evidence_id: str
    #: Defaults to EXTERNAL (an artefact referenced without a recorded
    #: submission path). Provenance still matters to the audit trail, but the
    #: evaluation grades evidence text, so a caller with no provenance context
    #: is not forced to invent one.
    kind: EvidenceKind = EvidenceKind.EXTERNAL
    summary: str = ""
    text: str = ""
    source_name: str = ""
    collected_at: datetime | None = None
    #: Normalisation outputs ("" when not normalised). Advisory, never graded.
    format: str = ""
    quality: str = ""
    nature: str = ""
    content_hash: str = ""

    @property
    def has_content(self) -> bool:
        """True when there is readable content to grade.

        Deliberately `text` only. A `summary` here is a human-written
        *description* of an artefact, and a description of evidence is not
        evidence — letting it satisfy the gate would let "attached the policy"
        read as compliance. The gate in `status.py` keys off this property.
        """
        return bool(self.text.strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind.value,
            "summary": self.summary,
            "source_name": self.source_name,
            "has_content": self.has_content,
            "format": self.format or None,
            "quality": self.quality or None,
            "nature": self.nature or None,
            "nature_label": _NATURE_LABELS.get(self.nature, ""),
            "content_hash": self.content_hash or None,
        }


# ── Evaluation ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StatusDecision:
    """Output of the deterministic status calculator.

    `score` is the effective 0.0-1.0 coverage figure the threshold was applied
    to *after* every framework cap, so the stored number always explains the
    stored status.
    """
    status: ComplianceStatus
    score: float
    response_score: float
    evidence_score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationResult:
    """The complete, self-describing result of evaluating one control.

    Carries every field the compliance contract requires to be preserved:
    framework, control_id, requirement, evidence_id, evidence_summary, status,
    confidence, reasoning, gaps, recommendation and a timestamp.
    """
    framework: str
    control_id: str
    requirement: str
    status: ComplianceStatus
    confidence: float
    confidence_factors: Mapping[str, float] = field(default_factory=dict)
    reasoning: str = ""
    gaps: tuple[str, ...] = ()
    recommendation: str = ""
    evidence_id: str | None = None
    evidence_summary: str = ""
    evidence_refs: tuple[EvidenceRef, ...] = ()
    requirement_id: str = ""
    requirement_clauses: tuple[str, ...] = ()
    domain_code: str = ""
    domain_name: str = ""
    assurance_level: str = AssuranceLevel.POINT_IN_TIME.value
    score: float = 0.0
    response_score: float = 0.0
    evidence_score: float = 0.0
    evidence_count: int = 0
    criticality: str = "standard"
    severity: Severity = Severity.MEDIUM
    evaluated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def confidence_band(self) -> ConfidenceBand:
        return ConfidenceBand.from_score(self.confidence)

    @property
    def is_conclusive(self) -> bool:
        return self.status.is_conclusive

    def as_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "control_id": self.control_id,
            "requirement": self.requirement,
            "requirement_id": self.requirement_id,
            "requirement_clauses": list(self.requirement_clauses),
            "domain_code": self.domain_code,
            "domain_name": self.domain_name,
            "evidence_id": self.evidence_id,
            "evidence_summary": self.evidence_summary,
            "evidence_refs": [e.as_dict() for e in self.evidence_refs],
            "evidence_count": self.evidence_count,
            "status": self.status.value,
            "confidence": round(self.confidence, 3),
            "confidence_band": self.confidence_band.value,
            "confidence_factors": dict(self.confidence_factors),
            "reasoning": self.reasoning,
            "gaps": list(self.gaps),
            "recommendation": self.recommendation,
            "score": round(self.score, 4),
            "response_score": round(self.response_score, 4),
            "evidence_score": round(self.evidence_score, 4),
            "assurance_level": self.assurance_level,
            "criticality": self.criticality,
            "severity": self.severity.value,
            "evaluated_at": self.evaluated_at.isoformat(),
            "metadata": dict(self.metadata),
        }


# ── Dataset views (what the reader produces, before the DB import) ──────────

@dataclass(frozen=True)
class DatasetControl:
    """A control exactly as it appears in a framework dataset file."""
    control_id: str
    statement: str
    domain_id: str = ""
    domain_name: str = ""
    sub_domain_id: str = ""
    sub_domain_name: str = ""
    sub_domain_description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


def summarise_evidence(evidence: Sequence[EvidenceRef], limit: int = 3) -> str:
    """One-line, human-readable summary of what evidence was considered."""
    usable = [e for e in evidence if e.has_content]
    if not usable:
        if evidence:
            names = ", ".join(sorted({e.source_name or e.evidence_id for e in evidence})[:limit])
            return (
                f"No readable evidence content (referenced only: {names})."
                if names else "No evidence supplied."
            )
        return "No evidence supplied."
    parts: list[str] = []
    for item in usable[:limit]:
        label = item.source_name or item.evidence_id
        summary = (item.summary or "").strip()
        parts.append(f"{label} ({summary})" if summary else label)
    extra = len(usable) - limit
    text = "; ".join(parts) + (f"; +{extra} more" if extra > 0 else "")
    return f"{len(usable)} readable evidence item(s): {text}."
