"""Pydantic v2 schemas for the Hindsight (organisational memory) API.

These mirror the stored memory record and exception, and the classification
that came out of a review. The wire format deliberately carries the *basis* and
`matched_memories` of every classification: a client displaying a
`KNOWN_EXCEPTION` or `RECURRING_FINDING` must be able to show *why*, i.e. prove
that the label came from stored memory and not from a black box.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ── requests ─────────────────────────────────────────────────────────────────

class MemoryReviewRequest(BaseModel):
    """Review an assessment's recorded evaluations against stored memory."""

    framework_id: str = Field(description="Database id of the evaluated framework")
    control_codes: list[str] | None = Field(
        default=None, description="Restrict the review to these control codes"
    )
    model_inference: bool = Field(
        default=True,
        description="Whether to attempt Groq enrichment (advisory only)",
    )


class MemoryDecisionRequest(BaseModel):
    """A human's decision on a finding, stored as a memory event."""

    control_code: str = Field(description="Portable code, e.g. 'CC6.1'")
    framework: str = Field(
        description="Stable adapter key the finding was recorded under, e.g. 'soc2-type-1'"
    )
    decision: Literal["accepted", "rejected", "exception", "escalated", "remediated"]
    human_feedback: str = Field(default="")
    reason: str = Field(
        default="", description="Required when decision == 'exception'"
    )
    expires_at: datetime | None = Field(
        default=None, description="Exception expiry; None = no expiry"
    )
    remediation_actions: list[str] | None = Field(default=None)


class ExceptionCreateRequest(BaseModel):
    """Grant an exception explicitly (equivalent to the 'exception' decision)."""

    framework: str = Field(description="Stable adapter key")
    control_code: str = Field(description="Portable code, e.g. 'CC6.1'")
    reason: str = Field(min_length=1)
    creator: str = Field(default="assessor")
    expires_at: datetime | None = None
    severity: str | None = None


class ExceptionRevokeRequest(BaseModel):
    reason: str = Field(default="")


# ── read: responses ──────────────────────────────────────────────────────────

class MemoryRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    assessment_id: str | None = None
    evaluation_id: str | None = None
    framework: str
    framework_code: str | None = None
    control_id: str
    domain_code: str | None = None
    finding_type: str
    event: str
    classification: str
    decision: str | None = None
    occurrence: int = 1
    severity: str | None = None
    status: str | None = None
    human_feedback: str = ""
    remediation_status: str | None = None
    remediation_actions: list[str] | None = None
    actor: str | None = None
    meta: dict[str, Any] | None = None
    enrichment: dict[str, Any] | None = None
    created_at: datetime


class ExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    assessment_id: str | None = None
    framework: str
    control_id: str
    reason: str
    creator: str
    created_by: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    status: str
    severity: str | None = None
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revoked_reason: str | None = None


class MemoryReviewItemOut(BaseModel):
    """One control's review result, with the memory trail that drove it."""

    record_id: str
    assessment_id: str
    control_id: str
    status: str
    finding_type: str
    classification: str
    basis: list[str] = Field(default_factory=list)
    occurrence: int = 1
    memory_summary: str = ""
    matched_memories: list[str] = Field(default_factory=list)
    enrichment: dict[str, Any] | None = None
    enrichment_error: str | None = None
    created_at: datetime


class MemoryReviewOut(BaseModel):
    assessment_id: str
    framework: str
    organization_id: str
    reviewed: int
    recorded: int
    skipped: int
    items: list[MemoryReviewItemOut]


class MemoryDecisionOut(BaseModel):
    record_id: str
    organization_id: str
    control_id: str
    decision: str
    event: str
    exception_id: str | None = None
    human_feedback: str = ""


class ExceptionGrantedOut(BaseModel):
    exception_id: str
    organization_id: str
    control_id: str
    status: str
    created_at: datetime


class ControlHistoryOut(BaseModel):
    organization_id: str
    control_id: str
    memory: list[MemoryRecordOut] = Field(default_factory=list)
    exceptions: list[ExceptionOut] = Field(default_factory=list)


class SimilarFindingsOut(BaseModel):
    organization_id: str
    control_id: str
    similar: list[MemoryRecordOut] = Field(default_factory=list)


class RecurringFindingItem(BaseModel):
    control_id: str
    finding_type: str
    occurrences: int
    last_seen: datetime | None = None
    latest_classification: str | None = None
    remediated: bool = False


class RecurringFindingsOut(BaseModel):
    organization_id: str
    threshold: int
    total: int
    items: list[RecurringFindingItem] = Field(default_factory=list)


class RiskSummaryOut(BaseModel):
    organization_id: str
    memory_records: int
    by_finding_type: dict[str, int] = Field(default_factory=dict)
    by_classification: dict[str, int] = Field(default_factory=dict)
    recurrence_leaderboard: list[dict[str, Any]] = Field(default_factory=list)
    open_findings: int = 0
    finding_records: int = 0
    exceptions: dict[str, int] = Field(default_factory=dict)


class ExceptionsListOut(BaseModel):
    organization_id: str
    total: int
    exceptions: list[ExceptionOut] = Field(default_factory=list)


__all__ = [
    "ControlHistoryOut",
    "ExceptionCreateRequest",
    "ExceptionGrantedOut",
    "ExceptionOut",
    "ExceptionRevokeRequest",
    "ExceptionsListOut",
    "MemoryDecisionOut",
    "MemoryDecisionRequest",
    "MemoryRecordOut",
    "MemoryReviewItemOut",
    "MemoryReviewOut",
    "MemoryReviewRequest",
    "RecurringFindingItem",
    "RecurringFindingsOut",
    "RiskSummaryOut",
    "SimilarFindingsOut",
]