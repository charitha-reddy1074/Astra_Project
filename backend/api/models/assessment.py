import uuid
from datetime import datetime
from sqlalchemy import (
    String, Text, Integer, Float, Boolean,
    DateTime, ForeignKey, JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.api.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    framework_ids: Mapped[list] = mapped_column(JSON, default=list)
    # Optional subset of domains to scope the questionnaire to. Each entry is
    # "<framework_id>:<domain_code>" (also matches a bare domain code/id). Empty = all.
    selected_domains: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    # draft | in_progress | in_review | completed
    organization: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str | None] = mapped_column(String(255))
    # Individual Assessor (email/username) this assessment is assigned to. An
    # Assessor only sees the assessments assigned to them. None = unassigned.
    assigned_to: Mapped[str | None] = mapped_column(String(255))
    overall_score: Mapped[float | None] = mapped_column(Float)
    maturity_level: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    pre_assessment_file_name: Mapped[str | None] = mapped_column(String(512))
    pre_assessment_file_path: Mapped[str | None] = mapped_column(String(1024))
    # How the AI should generate this assessment's questionnaire. Set in the
    # builder ("Generation Settings"). Shape:
    #   {strategy, depth, evidence_policy, custom_instructions, recommendation_mode}
    # strategy: framework_only | framework_plus_ai | ai_rewrite
    generation_config: Mapped[dict | None] = mapped_column(JSON)
    # Direct market association (1:1 model — one assessment, one market).
    market_id: Mapped[str | None] = mapped_column(String(255))
    market_label: Mapped[str | None] = mapped_column(String(255))

    questionnaire: Mapped["Questionnaire | None"] = relationship(
        "Questionnaire", back_populates="assessment",
        cascade="all, delete-orphan", uselist=False,
    )
    responses: Mapped[list["Response"]] = relationship(
        "Response", back_populates="assessment", cascade="all, delete-orphan",
    )
    findings: Mapped[list["Finding"]] = relationship(
        "Finding", back_populates="assessment", cascade="all, delete-orphan",
    )
    scores: Mapped[list["Score"]] = relationship(
        "Score", back_populates="assessment", cascade="all, delete-orphan",
    )
    followups: Mapped[list["FollowupQuestion"]] = relationship(
        "FollowupQuestion", back_populates="assessment", cascade="all, delete-orphan",
    )
    category_ratings: Mapped[list["CategoryRating"]] = relationship(
        "CategoryRating", back_populates="assessment", cascade="all, delete-orphan",
    )
    document_requests: Mapped[list["DocumentRequest"]] = relationship(
        "DocumentRequest", back_populates="assessment", cascade="all, delete-orphan",
    )
    evaluations: Mapped[list["ComplianceEvaluation"]] = relationship(
        "ComplianceEvaluation", back_populates="assessment", cascade="all, delete-orphan",
    )
    memory_records: Mapped[list["MemoryRecord"]] = relationship(
        "MemoryRecord", back_populates="assessment", cascade="all, delete-orphan",
    )


class Questionnaire(Base):
    __tablename__ = "questionnaires"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    question_ids: Mapped[list] = mapped_column(JSON, default=list)
    total_questions: Mapped[int] = mapped_column(Integer, default=0)
    answered_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assessment: Mapped["Assessment"] = relationship(
        "Assessment", back_populates="questionnaire"
    )


class Response(Base):
    __tablename__ = "responses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    # assessment_market_id column remains in DB but is unmapped
    question_id: Mapped[str] = mapped_column(String(36), nullable=False)
    control_id: Mapped[str | None] = mapped_column(String(36))
    response_value: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    answered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assessment: Mapped["Assessment"] = relationship(
        "Assessment", back_populates="responses"
    )
    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence", back_populates="response", cascade="all, delete-orphan",
    )


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    response_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("responses.id", ondelete="CASCADE"), nullable=False
    )
    assessment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    response: Mapped["Response"] = relationship("Response", back_populates="evidence")


class FollowupQuestion(Base):
    """An AI-generated follow-up question for a specific assessment, appended at
    the end of its category's questions in the Conduct tab. Stored per-assessment
    (NOT on the shared framework Question table) so it never leaks into other
    assessments using the same framework."""
    __tablename__ = "followup_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    # assessment_market_id column remains in DB but is unmapped
    framework_code: Mapped[str | None] = mapped_column(String(64))
    domain_code: Mapped[str | None] = mapped_column(String(64))
    domain_name: Mapped[str | None] = mapped_column(String(255))
    category_code: Mapped[str | None] = mapped_column(String(64))
    category_name: Mapped[str | None] = mapped_column(String(255))
    control_code: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    help_text: Mapped[str | None] = mapped_column(Text)
    question_type: Mapped[str] = mapped_column(String(32), default="FREE_TEXT")
    weight: Mapped[float] = mapped_column(Float, default=2.0)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    # "ai" (evidence-driven follow-up), "pre_assessment" (custom questionnaire
    # uploaded by the Central Admin), or "diagnostic" (maturity-diagnostic
    # questions generated per-assessment — never stored in the shared questions
    # table to prevent cross-assessment leakage).
    source: Mapped[str] = mapped_column(String(32), default="ai")
    # Diagnostic-specific metadata (populated when source='diagnostic').
    choices: Mapped[list | None] = mapped_column(JSON)
    expected_evidence_types: Mapped[list | None] = mapped_column(JSON)
    sub_topic: Mapped[str | None] = mapped_column(String(255))
    maturity_signals: Mapped[dict | None] = mapped_column(JSON)
    gap_if_deficient: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assessment: Mapped["Assessment"] = relationship("Assessment", back_populates="followups")


class CategoryRating(Base):
    """AI rating of the uploaded evidence for one category (sub-domain), keyed to
    its criteria statement. Detail shown in the AI Assistance tab; summarised in
    Reports."""
    __tablename__ = "category_ratings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    framework_code: Mapped[str | None] = mapped_column(String(64))
    domain_code: Mapped[str | None] = mapped_column(String(64))
    domain_name: Mapped[str | None] = mapped_column(String(255))
    category_code: Mapped[str | None] = mapped_column(String(64))
    category_name: Mapped[str | None] = mapped_column(String(255))
    criteria_statement: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    verdict: Mapped[str | None] = mapped_column(String(32))
    rationale: Mapped[str | None] = mapped_column(Text)
    missing: Mapped[list | None] = mapped_column(JSON)
    evidence_files: Mapped[list | None] = mapped_column(JSON)
    # Per-item (control) rubric ratings the LLM produced for this category:
    # [{control_code, level 0-3, level_label, rationale, coverage, tooling_present,
    #   evidence_class, strengths[], gaps[], recommendation}]. Feeds the 0-3
    # market roll-up as the level source when no manual anchor is set.
    item_ratings: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # assessment_market_id column remains in DB but is unmapped

    assessment: Mapped["Assessment"] = relationship("Assessment", back_populates="category_ratings")


class DocumentRequest(Base):
    """A document/evidence request the assessor sends to the owner for one
    expected evidence type. The owner uploads files against the request (they
    are auto-linked as engagement evidence for this assessment); the assessor
    then accepts or rejects what was provided."""
    __tablename__ = "document_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    # assessment_market_id column remains in DB but is unmapped
    evidence_type: Mapped[str] = mapped_column(String(512), nullable=False)
    domain_code: Mapped[str | None] = mapped_column(String(64))
    domain_name: Mapped[str | None] = mapped_column(String(255))
    control_code: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="requested")
    # requested | provided | accepted | rejected
    requested_by: Mapped[str | None] = mapped_column(String(255))
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # stored_name entries in the assessment's evidence folder, uploaded by the owner
    provided_files: Mapped[list] = mapped_column(JSON, default=list)
    provided_by: Mapped[str | None] = mapped_column(String(255))
    provided_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    assessment: Mapped["Assessment"] = relationship(
        "Assessment", back_populates="document_requests"
    )


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    # assessment_market_id column remains in DB but is unmapped
    control_id: Mapped[str | None] = mapped_column(String(36))
    control_code: Mapped[str | None] = mapped_column(String(64))
    framework_code: Mapped[str | None] = mapped_column(String(64))
    domain_code: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    gap_description: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    # critical | high | medium | low | info
    status: Mapped[str] = mapped_column(String(32), default="open")
    # open | acknowledged | remediated | accepted
    # The compliance evaluation that produced this finding, when it came from
    # one. Null for findings written by the existing scoring path, so existing
    # rows stay valid and the back-reference is optional.
    evaluation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("compliance_evaluations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assessment: Mapped["Assessment"] = relationship(
        "Assessment", back_populates="findings"
    )


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    # assessment_market_id column remains in DB but is unmapped
    framework_id: Mapped[str | None] = mapped_column(String(36))
    framework_code: Mapped[str | None] = mapped_column(String(64))
    domain_code: Mapped[str | None] = mapped_column(String(64))
    control_code: Mapped[str | None] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(16), default="framework")
    # framework | domain | control
    score: Mapped[float] = mapped_column(Float, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, default=0.0)
    percentage: Mapped[float] = mapped_column(Float, default=0.0)
    maturity_level: Mapped[int] = mapped_column(Integer, default=1)
    answered_questions: Mapped[int] = mapped_column(Integer, default=0)
    total_questions: Mapped[int] = mapped_column(Integer, default=0)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assessment: Mapped["Assessment"] = relationship("Assessment", back_populates="scores")
