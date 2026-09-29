"""Hindsight: the persistent *organizational memory* of the compliance agent.

This is deliberately NOT a replacement for the compliance database. The
`compliance_evaluations` table remains the source of structured truth for what
was claimed about a control; what lives here is the contextual record of how the
organisation got there — findings raised across assessments and runs, human
accept/reject/exception decisions, approved exceptions, and remediation
activity. Every row is scoped by `organization_id`, which is the isolation
boundary: one organisation's history is never offered as context to another.

Two tables:

* `MemoryRecord` — one event in the organisation's history. A finding raised, a
  human decision recorded, an exception granted, a remediation started or
  completed, an escalation. `classification` carries the memory-aware label
  (NEW_FINDING / KNOWN_EXCEPTION / RECURRING_FINDING /
  RESOLVED_RECURRING_FINDING / ESCALATION_REQUIRED / PATTERN_DETECTED) that the
  review workflow computed, and `meta` holds the contextual metadata and the
  memory that was actually used to reach it.

* `ComplianceException` — an approved deviation from a control requirement with
  a reason, a creator, an optional expiry, and a status. Only `ACTIVE` (and not
  passed `expires_at`) exceptions count during classification, so an expired
  exception makes the control actionable again.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    String, Text, Integer, DateTime, ForeignKey, JSON, Index, Boolean,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.api.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class MemoryRecord(Base):
    """One stored event in an organisation's compliance memory.

    Append-only in spirit: a later review event is a *new* row that supersedes,
    never an UPDATE of an earlier claim. The audit trail this builds is part of
    the value — telling "we learnt it was an exception" apart from "we always
    treated it that way" needs both rows.
    """

    __tablename__ = "memory_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    #: The isolation key. Memory is organisation-scoped and never crosses it.
    organization_id: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Optional back-reference to where this memory came from. Nullable so
    #: organisational events (a pattern note, a remediation) can exist without
    #: being pinned to one assessment.
    assessment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="SET NULL")
    )
    #: The evaluation row this memory extends, when it came out of a review of
    #: a recorded evaluation. Used to keep one record per (evaluation, control).
    evaluation_id: Mapped[str | None] = mapped_column(String(36))

    framework: Mapped[str] = mapped_column(String(64), nullable=False)
    framework_code: Mapped[str | None] = mapped_column(String(64))
    #: Portable control code (CC6.1, GV.OC-01), matching `control_code` on the
    #: evaluation row.
    control_id: Mapped[str] = mapped_column(String(64), nullable=False)
    domain_code: Mapped[str | None] = mapped_column(String(64))
    domain_name: Mapped[str | None] = mapped_column(String(255))

    #: What kind of memory this is: a control failure, partial coverage, an
    #: evidence gap, an exception, a remediation, a risk pattern, an evidence
    #: interpretation decision, or a human decision. Values from
    #: `FindingType` in compliance/memory/enums.py.
    finding_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: What happened ("finding_raised", "exception_granted", ...). Values from
    #: `MemoryEvent`.
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The memory-aware label the review workflow computed, or "" for events
    #: that are not classifications (a remediation, a decision).
    classification: Mapped[str] = mapped_column(String(32), default="")

    #: The compliance status that was recorded (FAIL / PARTIAL /
    #: INSUFFICIENT_EVIDENCE) for a finding, or the human decision
    #: ("accepted", "rejected", "exception", "escalated", "remediated") for a
    #: decision record.
    decision: Mapped[str | None] = mapped_column(String(64))
    #: Position of this occurrence on this control: 1 = first time.
    occurrence: Mapped[int] = mapped_column(Integer, default=1)

    severity: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str | None] = mapped_column(String(32))
    #: The assessor's own words when accepting / rejecting / escalating.
    human_feedback: Mapped[str | None] = mapped_column(Text)
    #: open | acknowledged | in_progress | remediated | accepted
    remediation_status: Mapped[str | None] = mapped_column(String(32))
    remediation_actions: Mapped[list | None] = mapped_column(JSON)
    #: Who created this memory (spoofable identity, same as the rest of the app).
    actor: Mapped[str | None] = mapped_column(String(255))

    #: Contextual metadata: what memory was retrieved and used, evidence counts,
    #: the confidence band, etc. Everything a later reviewer needs to see why the
    #: classification was reached.
    meta: Mapped[dict | None] = mapped_column(JSON)
    #: The advisory Groq enrichment (relationship_to_history, root causes, ...).
    #: Null when enrichment was skipped or failed. Never influences a status.
    enrichment: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assessment: Mapped["Assessment"] = relationship(
        "Assessment", back_populates="memory_records",
        foreign_keys=[assessment_id],
    )

    __table_args__ = (
        Index("ix_memory_org_control", "organization_id", "control_id"),
        Index("ix_memory_org_finding", "organization_id", "finding_type"),
        Index("ix_memory_org_class", "organization_id", "classification"),
        Index("ix_memory_eval", "evaluation_id"),
    )


class ComplianceException(Base):
    """An approved deviation from a control requirement.

    Only a row with `status == "active"` and (`expires_at` NULL or in the
    future) shields a finding from escalation — and even then the finding is
    still *recorded*, never suppressed. An exception that has passed
    `expires_at` no longer counts as active, which is how an expired exception
    makes the control actionable again.
    """

    __tablename__ = "compliance_exceptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(255), nullable=False)
    assessment_id: Mapped[str | None] = mapped_column(String(36))
    framework: Mapped[str] = mapped_column(String(64), nullable=False)
    framework_code: Mapped[str | None] = mapped_column(String(64))
    control_id: Mapped[str] = mapped_column(String(64), nullable=False)

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    creator: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    #: NULL = no expiry (permanent). Any active row past this date is treated as
    #: expired by `MemoryService` and no longer counts as an active exception.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    #: active | expired | revoked | superseded
    status: Mapped[str] = mapped_column(String(32), default="active")
    severity: Mapped[str | None] = mapped_column(String(16))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_by: Mapped[str | None] = mapped_column(String(255))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    #: The memory record this exception was approved on, when one exists.
    memory_record_id: Mapped[str | None] = mapped_column(String(36))
    meta: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_exceptions_org_control", "organization_id", "control_id"),
        Index("ix_exceptions_org_status", "organization_id", "status"),
        Index("ix_exceptions_status", "status"),
    )


__all__ = ["ComplianceException", "MemoryRecord"]