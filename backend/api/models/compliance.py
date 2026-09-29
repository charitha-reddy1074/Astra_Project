"""Persistence for compliance evaluations.

One row per (assessment, control) per run. Deliberately a separate table rather
than columns on `Finding` or `Score`: a compliance evaluation is a claim with an
assurance scope, an evidence trail and a reasoning trace, and none of that
belongs on a finding or a maturity score.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    String, Text, Integer, Float, Boolean, DateTime, ForeignKey, JSON, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.api.database import Base

if TYPE_CHECKING:  # pragma: no cover - import cycle broken at runtime by the string
    from backend.api.models.assessment import Assessment


def _uuid() -> str:
    return str(uuid.uuid4())


class ComplianceEvaluation(Base):
    """The persisted result of evaluating one control against one requirement.

    Every field on `EvaluationResult` that a reviewer or auditor needs is stored
    explicitly rather than only inside `metadata`, so the table is queryable
    without parsing JSON. `metadata` holds framework-specific extras (NIST's
    category objective, SOC 2's AWS service guidance) that no query needs.
    """

    __tablename__ = "compliance_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    #: Groups every row produced by one evaluation run, so a re-run supersedes
    #: the previous one for the same (assessment, control) without deleting the
    #: history of what was previously claimed.
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)

    framework_id: Mapped[str | None] = mapped_column(String(36))
    #: Stable framework key of the adapter that produced the row
    #: ("nist-csf-2-0" / "soc2-type-1"). Denormalised off Framework.code so a
    #: report never has to re-resolve the adapter.
    framework: Mapped[str] = mapped_column(String(64), nullable=False)
    framework_code: Mapped[str | None] = mapped_column(String(64))

    control_id: Mapped[str | None] = mapped_column(String(36))
    control_code: Mapped[str] = mapped_column(String(64), nullable=False)
    domain_code: Mapped[str | None] = mapped_column(String(64))
    domain_name: Mapped[str | None] = mapped_column(String(255))

    # ── the requirement that was evaluated ─────────────────────────────────
    requirement_id: Mapped[str | None] = mapped_column(String(64))
    requirement: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_clauses: Mapped[list | None] = mapped_column(JSON)

    # ── the evidence considered ────────────────────────────────────────────
    evidence_id: Mapped[str | None] = mapped_column(String(512))
    evidence_summary: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list | None] = mapped_column(JSON)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    response_score: Mapped[float] = mapped_column(Float, default=0.0)

    # ── the decision ───────────────────────────────────────────────────────
    #: PASS | PARTIAL | FAIL | INSUFFICIENT_EVIDENCE | NOT_APPLICABLE
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_factors: Mapped[dict | None] = mapped_column(JSON)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning: Mapped[str | None] = mapped_column(Text)
    gaps: Mapped[list | None] = mapped_column(JSON)
    recommendation: Mapped[str | None] = mapped_column(Text)

    # ── assurance scope: what this row is allowed to claim ─────────────────
    #: TYPE_1 | POINT_IN_TIME. Pinned per row so no surface can present a
    #: SOC 2 result as anything other than Type 1.
    assurance_level: Mapped[str] = mapped_column(String(32), default="POINT_IN_TIME")
    #: False for every framework — the platform does not conclude operating
    #: effectiveness. Explicit rather than implied, so a query can assert it.
    asserts_operating_effectiveness: Mapped[bool] = mapped_column(
        Boolean, default=False
    )

    criticality: Mapped[str] = mapped_column(String(16), default="standard")
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    #: Framework-specific extras (NIST's category objective, SOC 2's AWS service
    #: guidance). Named `framework_metadata` because `metadata` is reserved by
    #: SQLAlchemy's declarative API.
    framework_metadata: Mapped[dict | None] = mapped_column(JSON)

    actor: Mapped[str | None] = mapped_column(String(255))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    #: True for the newest row per (assessment_id, control_code); older rows are
    #: retained for the audit trail.
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)

    #: Resolved by name from SQLAlchemy's registry, so the models package can
    #: import this module before assessment.py without a circular import.
    assessment: Mapped["Assessment"] = relationship("Assessment", back_populates="evaluations")

    __table_args__ = (
        Index("ix_compliance_eval_assessment", "assessment_id"),
        Index("ix_compliance_eval_run", "run_id"),
        Index("ix_compliance_eval_current", "assessment_id", "control_code", "is_current"),
        Index("ix_compliance_eval_status", "status"),
    )


class SemanticReviewRecord(Base):
    """A Groq semantic review persisted beside the deterministic evaluation.

    The `/assessment-pipeline` route runs the LLM layer over a recorded run and
    stores one advisory row per control, so a later report can show *why* Groq
    read the evidence the way it did without re-invoking the model. This is the
    "the dashboard must not re-pay for the model every time it renders" record.

    Advisory by construction: every field mirrors the semantic contract, the
    `authoritative_status` is the deterministic status the platform claims, and
    nothing here influences a `ComplianceEvaluation` status or score. A row with
    `model_invoked = False` (or a fallback reason) documents that no model
    opinion was obtainable — which is a normal outcome, not an error.
    """

    __tablename__ = "compliance_semantic"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)

    framework: Mapped[str] = mapped_column(String(64), nullable=False)
    framework_code: Mapped[str | None] = mapped_column(String(64))
    control_id: Mapped[str | None] = mapped_column(String(36))
    control_code: Mapped[str] = mapped_column(String(64), nullable=False)
    domain_code: Mapped[str | None] = mapped_column(String(64))

    #: The deterministic status of the evaluated control. The platform's claim.
    authoritative_status: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Policy-derived confidence band (never the model's own number).
    confidence_band: Mapped[str] = mapped_column(String(16), nullable=False)

    model_invoked: Mapped[bool] = mapped_column(Boolean, default=False)
    assessment: Mapped[dict | None] = mapped_column(JSON)
    agrees_with_deterministic: Mapped[bool | None] = mapped_column(Boolean)
    more_cautious_than_deterministic: Mapped[bool] = mapped_column(Boolean, default=False)
    model_tier: Mapped[str] = mapped_column(String(16), default="")
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_reason: Mapped[str] = mapped_column(String(255), default="")
    fallback_reasoning: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_semantic_assessment", "assessment_id"),
        Index("ix_semantic_run", "run_id"),
        Index("ix_semantic_control", "assessment_id", "control_code"),
    )
