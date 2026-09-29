"""Pydantic v2 schemas for the compliance evaluation API.

These mirror the persisted `ComplianceEvaluation` row and the deterministic
evaluation result it was built from. The assurance fields are part of the wire
format deliberately: a client that receives a SOC 2 result must be able to read
`assurance_level` and `asserts_operating_effectiveness` without a second call, so
it cannot accidentally present a Type 1 result as operating effectiveness.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ── read: catalogue ─────────────────────────────────────────────────────────

class FrameworkProfileOut(BaseModel):
    """A framework that has a compliance evaluation profile, and its scope."""

    model_config = ConfigDict(from_attributes=True)

    key: str = Field(description="Stable adapter key, e.g. 'soc2-type-1'")
    name: str
    version: str | None = None
    description: str | None = None
    assurance_level: str = Field(
        description="The only assurance this framework may ever claim."
    )
    pass_threshold: float = Field(ge=0.0, le=1.0)
    asserts_operating_effectiveness: bool = False
    assurance_statement: str = Field(
        description="What this framework's assurance level does and does not claim."
    )
    statement_aliases: list[str] = Field(default_factory=list)


class DatasetFormatOut(BaseModel):
    """The drop-in dataset contract, described so an operator can comply without
    reading the source. Served by `GET /compliance/frameworks`."""

    model_config = ConfigDict(from_attributes=True)

    shape: str
    root_key: str
    framework: list[str]
    domain: list[str]
    sub_domain: list[str]
    control: list[str]
    notes: list[str]


class FrameworkListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supported: list[FrameworkProfileOut]
    dataset_format: DatasetFormatOut


class RequirementOut(BaseModel):
    """The evaluable requirement: its text plus the clause list that a
    `clause: false` result is matched against."""

    model_config = ConfigDict(from_attributes=True)

    requirement_id: str | None = None
    text: str
    clauses: list[str] = Field(default_factory=list)


class ControlSpecOut(BaseModel):
    """One control as the evaluator sees it: requirement plus its evidence ask."""

    model_config = ConfigDict(from_attributes=True)

    control_id: str
    statement: str
    domain_code: str | None = None
    domain_name: str | None = None
    criticality: str
    expected_evidence_types: list[str] = Field(default_factory=list)
    requirement: RequirementOut


class ControlListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    framework: str
    framework_code: str
    assurance_level: str
    total: int
    controls: list[ControlSpecOut]


# ── read: evaluation ────────────────────────────────────────────────────────

class EvidenceRefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evidence_id: str | None = None
    kind: str
    source_name: str | None = None
    summary: str | None = None
    text: str | None = None
    collected_at: datetime | None = None


class ComplianceEvaluationOut(BaseModel):
    """One control's evaluated claim, as persisted."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    framework: str
    framework_code: str | None = None
    control_id: str | None = None
    control_code: str
    domain_code: str | None = None
    domain_name: str | None = None

    requirement_id: str | None = None
    requirement: str
    requirement_clauses: list[str] | None = None

    evidence_id: str | None = None
    evidence_summary: str | None = None
    evidence_refs: list[EvidenceRefOut] | None = None
    evidence_count: int = 0
    evidence_score: float = 0.0
    response_score: float = 0.0

    status: Literal[
        "PASS", "PARTIAL", "FAIL", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE",
    ]
    confidence: float = 0.0
    confidence_factors: dict[str, Any] | None = None
    score: float = 0.0
    reasoning: str | None = None
    gaps: list[str] | None = None
    recommendation: str | None = None

    assurance_level: str
    asserts_operating_effectiveness: bool = False

    criticality: str
    severity: str
    framework_metadata: dict[str, Any] | None = None
    actor: str | None = None
    evaluated_at: datetime
    is_current: bool = True


class StatusCounts(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    PASS: int = 0
    PARTIAL: int = 0
    FAIL: int = 0
    INSUFFICIENT_EVIDENCE: int = 0
    NOT_APPLICABLE: int = 0


class FrameworkSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    framework: str
    framework_code: str | None = None
    assurance_level: str
    asserts_operating_effectiveness: bool = False
    total: int
    counts: StatusCounts
    conclusive: int = Field(
        description="Controls with a conclusive status: PASS, PARTIAL or FAIL."
    )
    conclusive_pct: float
    mean_confidence: float


class ComplianceSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assessment_id: str
    total: int
    frameworks: list[FrameworkSummaryOut]


class EvaluationListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assessment_id: str
    total: int
    evaluations: list[ComplianceEvaluationOut]


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    control_code: str
    framework_code: str | None = None
    domain_code: str | None = None
    title: str
    gap_description: str | None = None
    recommendation: str | None = None
    severity: str
    status: str
    evaluation_id: str | None = None


class EvaluationRunOut(BaseModel):
    """The result of a POST evaluation, including what it raised."""

    model_config = ConfigDict(from_attributes=True)

    run_id: str
    assessment_id: str
    framework: str
    assurance_level: str
    asserts_operating_effectiveness: bool = False
    total: int
    counts: StatusCounts
    conclusive: int
    conclusive_pct: float
    mean_confidence: float
    evaluations: list[ComplianceEvaluationOut]
    findings_raised: int
    findings: list[FindingOut]


# ── write ───────────────────────────────────────────────────────────────────

class EvaluationRequest(BaseModel):
    """Body for POST /assessments/{id}/evaluations.

    A deliberate "coverage over convenience" default: a request that names no
    controls evaluates the whole in-scope set, so a partial evaluation can never
    happen by omission.
    """

    model_config = ConfigDict(extra="forbid")

    framework_id: str = Field(description="The Framework row to evaluate against.")
    control_codes: list[str] | None = Field(
        default=None,
        description=(
            "Restrict to these control codes. Omit to evaluate every in-scope "
            "control, which is the recommended default."
        ),
    )
    create_findings: bool = Field(
        default=True,
        description="Raise a Finding for every non-conclusive control.",
    )


class EvaluationAuditOut(BaseModel):
    """An evaluation event from the immutable activity log."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    summary: str | None = None
    changes: dict[str, Any] | None = None
    user_email: str | None = None
    actor_name: str | None = None
    user_role: str | None = None
    created_at: datetime


class EvaluationAuditListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assessment_id: str
    total: int
    events: list[EvaluationAuditOut]


# ── semantic (LLM) review ───────────────────────────────────────────────────
#
# Advisory only. `authoritative_status` is repeated on every item so a client
# rendering this alongside the evaluation report cannot mistake the model's
# reading for the platform's claim: the two are separate fields, and only the
# first one is ever a compliance conclusion.

class SemanticRequest(BaseModel):
    """What to review, and how much of it."""

    model_config = ConfigDict(extra="forbid")

    framework_id: str = Field(description="The Framework row that was evaluated.")
    run_id: str | None = Field(
        default=None,
        description="Restrict to one evaluation run. Defaults to the current rows.",
    )
    include_passing: bool = Field(
        default=False,
        description=(
            "Also review controls that passed. Off by default because a model's "
            "explanation of an already-clear PASS is the most expensive output "
            "per unit of value in this layer."
        ),
    )
    control_codes: list[str] | None = Field(
        default=None, description="Restrict to these control codes.",
    )
    top_k: int = Field(
        default=5, ge=1, le=20,
        description="How many in-scope controls each prompt is offered.",
    )


class SemanticAssessmentOut(BaseModel):
    """The model's reading, in the exact shape it was required to return."""

    model_config = ConfigDict(from_attributes=True)

    decision: str
    confidence: float
    control_id: str
    evidence_strength: str
    reasoning: str
    identified_gaps: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


class SemanticReviewItemOut(BaseModel):
    """One control's semantic review, with what the platform claims."""

    model_config = ConfigDict(from_attributes=True)

    framework: str
    control_id: str
    authoritative_status: str = Field(
        description="The deterministic status. This is the platform's claim."
    )
    confidence_band: str = Field(
        description=(
            "Derived from the deterministic evidence, never from the model. "
            "HIGH requires a confirmed framework match, so a control whose "
            "review fell back is capped at MEDIUM however well evidenced it is."
        )
    )
    model_invoked: bool
    assessment: SemanticAssessmentOut | None = None
    agrees_with_deterministic: bool | None = None
    more_cautious_than_deterministic: bool = False
    model_tier: str = ""
    cache_hit: bool = False
    fallback_reason: str = ""
    fallback_reasoning: str = ""


class SemanticReviewOut(BaseModel):
    """The whole review, with the cost and trust counters that explain it."""

    model_config = ConfigDict(from_attributes=True)

    assessment_id: str
    framework: str
    run_id: str | None = None
    asserts_operating_effectiveness: bool = False
    advisory: bool = Field(
        default=True,
        description="Always true. This layer never changes a compliance status.",
    )
    requested: int
    reviewed: int
    model_invocations: int
    cache_hits: int
    rejected: int = Field(
        description="Controls where the model's answer was refused as untrustworthy.",
    )
    results: list[SemanticReviewItemOut]
    warnings: list[str] = Field(default_factory=list)


# ── assessment pipeline (Part 4) ─────────────────────────────────────────
#
# The combined assessment path and the report shape it produces. Every field is
# advisory except the deterministic status/score/confidence a control row
# reports; `evidence_quality`, `format`, `historical_context` and
# `semantic_advisory` describe or annotate, they never decide.

class NormalizedEvidenceOut(BaseModel):
    """One evidence artefact after normalisation (format/quality/hash)."""

    model_config = ConfigDict(from_attributes=True)

    evidence_id: str | None = None
    kind: str
    source_name: str | None = None
    summary: str | None = None
    format: str = ""
    quality: str = ""
    content_hash: str | None = None
    chars: int = 0
    has_content: bool = False


class EvidencePackageOut(BaseModel):
    """A control's evidence package with the real budget accounting."""

    model_config = ConfigDict(from_attributes=True)

    summary: str = ""
    count: int = 0
    score: float = 0.0
    total_items: int = 0
    deduplicated: int = 0
    dropped_over_budget: int = 0
    truncated: int = 0
    chunks: int = 0
    items: list[NormalizedEvidenceOut] = Field(default_factory=list)


class EvidenceQualityOut(BaseModel):
    """The deterministic band over the evidence rubric."""

    model_config = ConfigDict(from_attributes=True)

    band: str = "MISSING"
    score: float = 0.0
    is_implementation_grade: bool = False
    formats: list[str] = Field(default_factory=list)


class HistoricalContextOut(BaseModel):
    """What organisational memory says about this control's history."""

    model_config = ConfigDict(from_attributes=True)

    classification: str = ""
    basis: list[str] = Field(default_factory=list)
    occurrence: int = 0
    matched_memories: list[str] = Field(default_factory=list)
    enrichment: dict[str, Any] | None = None
    enrichment_error: str | None = None


class SemanticAdvisoryOut(BaseModel):
    """The recorded Groq reading of a control (advisory, never applied)."""

    model_config = ConfigDict(from_attributes=True)

    model_invoked: bool = False
    decision: str | None = None
    confidence: float | None = None
    evidence_strength: str | None = None
    reasoning: str | None = None
    identified_gaps: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    band: str = ""
    more_cautious_than_deterministic: bool = False
    cache_hit: bool = False
    fallback_reason: str = ""
    fallback_reasoning: str = ""


class FindingRefOut(BaseModel):
    """The finding a failing control raised, in report form."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    control_code: str | None = None
    title: str
    gap_description: str | None = None
    severity: str
    status: str


class RiskOut(BaseModel):
    """A control's risk reading: severity, criticality and rating."""

    model_config = ConfigDict(from_attributes=True)

    severity: str
    criticality: str
    rating: str


class RecommendationOut(BaseModel):
    """What to do, plus the recorded remediation state."""

    model_config = ConfigDict(from_attributes=True)

    text: str = ""
    remediation_status: str | None = None
    remediation_actions: list[str] = Field(default_factory=list)


class AssessmentControlOut(BaseModel):
    """One control in the report: the deterministic claim + annotations."""

    model_config = ConfigDict(from_attributes=True)

    framework: str
    framework_code: str | None = None
    control: ControlSpecOut
    requirement: RequirementOut
    status: str
    is_conclusive: bool = False
    is_failure: bool = False
    reasoning: str | None = None
    gaps: list[str] = Field(default_factory=list)
    score: float = 0.0
    response_score: float = 0.0
    evidence_score: float = 0.0
    evidence: EvidencePackageOut
    evidence_quality: EvidenceQualityOut
    confidence: float = 0.0
    confidence_band: str = ""
    confidence_factors: dict[str, Any] = Field(default_factory=dict)
    historical_context: HistoricalContextOut
    finding: FindingRefOut | None = None
    risk: RiskOut
    recommendation: RecommendationOut
    semantic_advisory: SemanticAdvisoryOut | None = None
    assurance_level: str = "POINT_IN_TIME"


class PipelineMetricsOut(BaseModel):
    """The real cost ledger for a pipeline run or a read-only report."""

    model_config = ConfigDict(from_attributes=True)

    frameworks: int = 0
    controls_reviewed: int = 0
    evidence_files_seen: int = 0
    deduplicated: int = 0
    dropped_over_budget: int = 0
    truncated: int = 0
    chunks_built: int = 0
    semantic_evaluations: int = 0
    semantic_cache_hits: int = 0
    controls_skipped_semantic: int = 0
    avoided_llm_calls: int = 0
    memory_records_written: int = 0
    llm_calls_by_tier: dict[str, int] = Field(default_factory=dict)


class FrameworkReportOut(BaseModel):
    """One framework's view of the assessment, with its summary."""

    model_config = ConfigDict(from_attributes=True)

    framework: str
    framework_code: str | None = None
    framework_name: str | None = None
    assurance_level: str = "POINT_IN_TIME"
    asserts_operating_effectiveness: bool = False
    run_id: str | None = None
    summary: dict[str, Any]
    controls: list[AssessmentControlOut] = Field(default_factory=list)


class AssessmentReportOut(BaseModel):
    """The combined assessment report (write path and read path)."""

    model_config = ConfigDict(from_attributes=True)

    assessment_id: str
    assessment_name: str = ""
    organization: str | None = None
    assurance_level: str = "POINT_IN_TIME"
    generated_at: datetime
    summary: dict[str, Any]
    frameworks: list[FrameworkReportOut] = Field(default_factory=list)
    metrics: PipelineMetricsOut
    advisory: bool = True
    mode: Literal["pipeline", "report"] = "report"


class AssessmentPipelineRequest(BaseModel):
    """Body for the write-path assessment pipeline."""

    model_config = ConfigDict(extra="forbid")

    framework_id: str | None = Field(
        default=None,
        description=(
            "Evaluate one framework. Omit to pipeline every framework the "
            "assessment has selected."
        ),
    )
    control_codes: list[str] | None = Field(
        default=None, description="Restrict to these control codes.",
    )
    create_findings: bool = Field(
        default=True, description="Raise a Finding for every non-conclusive control.",
    )
    include_memory_review: bool = Field(
        default=True,
        description=(
            "Classify results against stored organisational memory and persist "
            "the records (Hindsight). Memory enrichment calls Groq only where "
            "explicitly enabled; the classification itself is deterministic."
        ),
    )
    top_k: int = Field(
        default=5, ge=1, le=20,
        description="How many in-scope controls each semantic prompt is offered.",
    )


class AssessmentPipelineOut(AssessmentReportOut):
    """The write-path response: the just-generated report and its ledger."""

    mode: Literal["pipeline", "report"] = "pipeline"
