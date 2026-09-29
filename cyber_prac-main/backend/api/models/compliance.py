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


class EvidenceControlMapping(Base):
    """One evidence-to-control candidate, and what a human did about it.

    This is the many-to-many edge the whole control-centric workflow turns on.
    The compliance engine has always *implied* a mapping (a file uploaded
    against a document request scoped to a control, or a response attachment);
    implying it is not enough, because "this document is about this control" is
    a claim a reviewer has to be able to see, score, and overrule.

    Two properties matter and are enforced structurally rather than by
    convention:

    * **A mapping is a candidate, never a proof.** `relevance_score` is a
      retrieval score, not a verdict. A row below `AUTO_ACCEPT_THRESHOLD` is
      written as `pending_review` with `requires_review=True`, and the control
      it points at is held at REVIEW_REQUIRED by `status.py` until a reviewer
      acts. Nothing here can turn into a compliance conclusion on its own.
    * **The edge is symmetric.** `control_id` and `evidence_id` are indexed
      independently, so "what does this control have" and "what does this
      document claim to support" are both one indexed read. Document-centric
      reverse mapping is not a different query dressed up — it is the same rows
      read the other way round.
    """

    __tablename__ = "evidence_control_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    #: Same run grouping as `ComplianceEvaluation`, so a re-map supersedes the
    #: previous candidate set without destroying the record of what was offered.
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # ── the evidence side ───────────────────────────────────────────────────
    #: The opaque `EvidenceRef.evidence_id` produced by the gathering layer. Opaque
    #: on purpose: response attachments, document-request files and vendor
    #: answers all live under different tables, and forcing them into one would
    #: mean inventing a row for each. The reverse index is what makes the edge
    #: queryable.
    evidence_id: Mapped[str] = mapped_column(String(512), nullable=False)
    evidence_source_name: Mapped[str | None] = mapped_column(String(512))
    evidence_kind: Mapped[str | None] = mapped_column(String(32))
    evidence_nature: Mapped[str] = mapped_column(
        String(32), default="UNDETERMINED",
    )
    #: POLICY_DESIGN | TECHNICAL_IMPLEMENTATION | OPERATIONAL_ACTIVITY |
    #: UNDETERMINED. Persisted because the *nature* of what was attached is the
    #: difference between design evidence and a period artefact, and a report has
    #: to be able to say which it is looking at without re-reading the file.
    evidence_format: Mapped[str | None] = mapped_column(String(32))

    # ── the control side ────────────────────────────────────────────────────
    #: The Framework row's adapter key ("nist-csf-2-0" / "soc2-type-1"). An
    #: adapter key rather than free text so a mapping can never name a framework
    #: the platform does not evaluate.
    framework: Mapped[str] = mapped_column(String(64), nullable=False)
    framework_code: Mapped[str | None] = mapped_column(String(64))
    #: This installation's `Control` primary key, so the edge can be joined.
    control_id: Mapped[str | None] = mapped_column(String(36))
    #: The portable control identifier, e.g. `GV.OC-03` / `CC6.1`. Stored
    #: alongside the row id because it is the identifier a report, a dataset
    #: export and a reviewer all use.
    control_code: Mapped[str] = mapped_column(String(64), nullable=False)
    domain_code: Mapped[str | None] = mapped_column(String(64))
    domain_name: Mapped[str | None] = mapped_column(String(255))

    # ── why this edge exists ────────────────────────────────────────────────
    #: 0.0-1.0 retrieval relevance. Advisory: it orders candidates and drives
    #: the review gate, it never contributes to a status directly.
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    #: Which parts of the control the artefact speaks to — requirement clause
    #: ids, control sub-sections, or matched excerpt headings. Free-form on
    #: purpose: the framework datasets publish different granularities, and a
    #: fixed vocabulary here would either lose detail or invent structure.
    matched_sections: Mapped[list | None] = mapped_column(JSON)
    #: The explanation a reviewer reads before deciding. Always populated by
    #: this layer, never left blank: an unexplained edge is not reviewable.
    mapping_reason: Mapped[str] = mapped_column(Text, default="")
    #: How the edge was produced: lexical | vector | requested | manual.
    #: `manual` means a human asserted it, which is the only provenance that can
    #: be `confirmed`.
    mapping_method: Mapped[str] = mapped_column(String(16), default="lexical")

    # ── human-in-the-loop ───────────────────────────────────────────────────
    #: auto_accepted | pending_review | confirmed | rejected.
    review_status: Mapped[str] = mapped_column(String(16), default="pending_review")
    requires_review: Mapped[bool] = mapped_column(Boolean, default=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    #: Resolved by name from the registry, matching the convention the other
    #: models in this package use to keep `models/assessment.py` importable first.
    assessment: Mapped["Assessment"] = relationship(
        "Assessment", back_populates="evidence_mappings"
    )

    __table_args__ = (
        Index("ix_evidence_map_assessment", "assessment_id"),
        Index("ix_evidence_map_control", "assessment_id", "framework", "control_code"),
        Index("ix_evidence_map_evidence", "assessment_id", "evidence_id"),
        Index("ix_evidence_map_review", "assessment_id", "review_status"),
        Index("ix_evidence_map_unique", "assessment_id", "framework", "control_code", "evidence_id",
              unique=True),
    )
