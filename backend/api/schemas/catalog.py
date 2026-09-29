"""Pydantic contract for the admin framework-catalog management API.

Ported from knowledge_base/.../webapp/backend/app/schemas/models.py, adapted to
the main app's schema (Framework → Domain → Category → Control → Question) instead
of the knowledge_base's recursive framework_nodes. All ids are UUID strings.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── Questions ────────────────────────────────────────────────────────────────
class QuestionIn(BaseModel):
    id: str | None = None
    text: str
    help_text: str | None = None
    question_type: str = "YES_NO"
    choices: list | None = None
    weight: float = 1.0
    maturity_level: int | None = None
    expected_evidence_types: list | None = None
    order_index: int = 0


class QuestionOut(QuestionIn):
    id: str


# ── Controls ─────────────────────────────────────────────────────────────────
class ControlDetail(BaseModel):
    id: str
    framework_id: str
    framework_code: str
    domain_id: str
    domain_code: str
    domain_name: str
    category_id: str | None = None
    category_code: str | None = None
    category_name: str | None = None
    code: str
    name: str | None = None
    statement: str | None = None
    weight: float
    criticality: str
    order_index: int
    questions: list[QuestionOut] = []
    # Soft optimistic-concurrency token over the editable state.
    row_hash: str


class ControlCreate(BaseModel):
    category_id: str = Field(..., description="Owning category (sub-domain)")
    code: str
    name: str | None = None
    statement: str | None = None
    weight: float = 1.0
    criticality: str = "medium"
    questions: list[QuestionIn] = []


class ControlUpdate(BaseModel):
    name: str | None = None
    statement: str | None = None
    weight: float | None = None
    criticality: str | None = None
    questions: list[QuestionIn] | None = None
    row_hash: str | None = None


class ControlMove(BaseModel):
    target_category_id: str


class ControlListItem(BaseModel):
    id: str
    code: str
    name: str | None = None
    statement: str | None = None
    domain_id: str
    category_id: str | None = None
    criticality: str
    question_count: int
    order_index: int


# ── Validation ───────────────────────────────────────────────────────────────
class ValidationIssue(BaseModel):
    severity: str            # "error" | "warning"
    rule: str
    message: str
    entity_type: str         # "control" | "category" | "domain"
    entity_id: str | None = None
    entity_ref: str | None = None


class ValidationReport(BaseModel):
    framework_id: str
    ok: bool
    error_count: int
    warning_count: int
    issues: list[ValidationIssue] = []


# ── Search ───────────────────────────────────────────────────────────────────
class SearchHit(BaseModel):
    control_id: str
    framework_id: str
    framework_code: str
    code: str
    name: str | None = None
    statement: str | None = None
    domain_code: str
    match_field: str


# ── Dashboard / stats ────────────────────────────────────────────────────────
class FrameworkStat(BaseModel):
    id: str
    code: str
    name: str
    version: str
    total_domains: int
    total_categories: int
    total_controls: int
    total_questions: int


class ActivityEntry(BaseModel):
    id: str
    entity_type: str
    entity_id: str
    action: str
    user_email: str | None = None
    created_at: datetime | None = None


class CatalogStats(BaseModel):
    frameworks: int
    domains: int
    categories: int
    controls: int
    questions: int
    controls_without_questions: int
    validation_error_frameworks: int
    frameworks_detail: list[FrameworkStat] = []
    recent_activity: list[ActivityEntry] = []


# ── History ──────────────────────────────────────────────────────────────────
class HistoryEntry(BaseModel):
    id: str
    action: str
    changes: dict | None = None
    user_email: str | None = None
    user_role: str | None = None
    created_at: datetime | None = None
