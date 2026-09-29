"""Pydantic request/response models (the API contract)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# --- Frameworks --------------------------------------------------------------
class Framework(BaseModel):
    id: int
    code: str
    name: str
    version: str
    description: Optional[str] = None
    source_format: Optional[str] = None
    source_file: Optional[str] = None
    external_uuid: Optional[str] = None
    ingested_at: Optional[str] = None
    created_at: str
    # aggregates
    node_count: int = 0
    control_count: int = 0
    question_count: int = 0


# --- Tree --------------------------------------------------------------------
class TreeNode(BaseModel):
    id: int
    parent_id: Optional[int]
    node_type: str
    code: str
    name: str
    control_count: int = 0          # controls directly under this node
    descendant_control_count: int = 0
    children: list["TreeNode"] = Field(default_factory=list)


# --- Controls ----------------------------------------------------------------
class Question(BaseModel):
    id: Optional[int] = None
    question_code: Optional[str] = None
    text: str
    question_type: str = "FREE_TEXT"
    choices: Optional[Any] = None
    help_text: Optional[str] = None
    weight: Optional[float] = None
    is_synthesized: bool = False
    sort_order: int = 0


class ControlListItem(BaseModel):
    id: int
    control_id: str
    name: Optional[str]
    statement: str
    node_id: int
    node_code: str
    requires_evidence: bool
    question_count: int
    sort_order: int


class ControlDetail(BaseModel):
    id: int
    framework_id: int
    node_id: int
    node_code: str
    node_path: list[str]                    # breadcrumb of node codes
    control_id: str
    name: Optional[str]
    statement: str
    requires_evidence: bool
    sort_order: int
    attributes: Optional[dict[str, Any]] = None
    questions: list[Question] = Field(default_factory=list)
    evidence_types: list[str] = Field(default_factory=list)
    row_hash: str                           # soft optimistic-concurrency token


class ControlCreate(BaseModel):
    node_id: int
    control_id: str = Field(min_length=1)
    name: Optional[str] = None
    statement: str = Field(min_length=1)
    requires_evidence: bool = False
    attributes: Optional[dict[str, Any]] = None
    questions: list[Question] = Field(default_factory=list)
    evidence_types: list[str] = Field(default_factory=list)


class ControlUpdate(BaseModel):
    name: Optional[str] = None
    statement: Optional[str] = None
    requires_evidence: Optional[bool] = None
    attributes: Optional[dict[str, Any]] = None
    questions: Optional[list[Question]] = None
    evidence_types: Optional[list[str]] = None
    row_hash: Optional[str] = None          # if provided, checked for conflicts


class ControlMove(BaseModel):
    target_node_id: int


# --- Search ------------------------------------------------------------------
class SearchHit(BaseModel):
    control_pk: int
    framework_id: int
    framework_code: str
    control_id: str
    name: Optional[str]
    statement: str
    node_code: str
    match_field: str


# --- Validation --------------------------------------------------------------
class ValidationIssue(BaseModel):
    severity: Literal["error", "warning"]
    rule: str
    message: str
    entity_type: str
    entity_id: Optional[int] = None
    entity_ref: Optional[str] = None


class ValidationReport(BaseModel):
    framework_id: int
    ok: bool
    error_count: int
    warning_count: int
    issues: list[ValidationIssue]


# --- Stats -------------------------------------------------------------------
class DashboardStats(BaseModel):
    frameworks: int
    nodes: int
    controls: int
    questions: int
    assessments: int
    synthesized_questions: int
    controls_without_questions: int
    validation_error_frameworks: int
    frameworks_detail: list[Framework]
    recent_activity: list[dict[str, Any]]


# --- History -----------------------------------------------------------------
class HistoryEntry(BaseModel):
    id: int
    action: str
    changes: Optional[Any]
    user_id: Optional[int]
    created_at: str


# --- Import ------------------------------------------------------------------
class ImportDiffItem(BaseModel):
    kind: Literal["framework", "node", "control"]
    change: Literal["create", "update", "unchanged"]
    ref: str
    detail: Optional[str] = None


class ImportPreview(BaseModel):
    detected_code: str
    detected_name: str
    detected_version: str
    adapter: str
    exists: bool
    summary: dict[str, int]
    diff: list[ImportDiffItem]


TreeNode.model_rebuild()
