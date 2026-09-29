import uuid
from datetime import datetime
from sqlalchemy import (
    String, Text, Integer, Float, Boolean,
    DateTime, ForeignKey, JSON, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.api.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Framework(Base):
    __tablename__ = "frameworks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="active")
    total_domains: Mapped[int] = mapped_column(Integer, default=0)
    total_categories: Mapped[int] = mapped_column(Integer, default=0)
    total_controls: Mapped[int] = mapped_column(Integer, default=0)
    total_questions: Mapped[int] = mapped_column(Integer, default=0)
    maturity_levels: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    domains: Mapped[list["Domain"]] = relationship(
        "Domain", back_populates="framework", cascade="all, delete-orphan",
        order_by="Domain.order_index",
    )
    mappings_from: Mapped[list["FrameworkMapping"]] = relationship(
        "FrameworkMapping", foreign_keys="FrameworkMapping.source_framework_id",
        cascade="all, delete-orphan",
    )


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    framework_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("frameworks.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("framework_id", "code"),)

    framework: Mapped["Framework"] = relationship("Framework", back_populates="domains")
    categories: Mapped[list["Category"]] = relationship(
        "Category", back_populates="domain", cascade="all, delete-orphan",
        order_by="Category.order_index",
    )
    # Read-only convenience view of every control in the domain (regardless of
    # category). Controls are owned/managed through Category; this relationship
    # exists for back-compat with consumers that iterate `domain.controls`.
    controls: Mapped[list["Control"]] = relationship(
        "Control", back_populates="domain", order_by="Control.order_index",
        viewonly=True,
    )


class Category(Base):
    """Sub-domain grouping between Domain and Control. Carries the criteria
    statement describing what this sub-domain evaluates when scoring its
    questions."""
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    framework_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("frameworks.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    criteria_statement: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    domain: Mapped["Domain"] = relationship("Domain", back_populates="categories")
    controls: Mapped[list["Control"]] = relationship(
        "Control", back_populates="category", cascade="all, delete-orphan",
        order_by="Control.order_index",
    )


class Control(Base):
    __tablename__ = "controls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("categories.id", ondelete="SET NULL")
    )
    framework_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("frameworks.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(512))
    statement: Mapped[str | None] = mapped_column(Text)
    category_code: Mapped[str | None] = mapped_column(String(64))
    category_name: Mapped[str | None] = mapped_column(String(255))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    criticality: Mapped[str] = mapped_column(String(16), default="medium")
    maturity_level: Mapped[int | None] = mapped_column(Integer)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    cross_refs: Mapped[list | None] = mapped_column(JSON)
    maturity_criteria: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (UniqueConstraint("framework_id", "code"),)

    domain: Mapped["Domain"] = relationship("Domain", back_populates="controls")
    category: Mapped["Category | None"] = relationship("Category", back_populates="controls")
    questions: Mapped[list["Question"]] = relationship(
        "Question", back_populates="control", cascade="all, delete-orphan",
    )
    mappings_source: Mapped[list["FrameworkMapping"]] = relationship(
        "FrameworkMapping", foreign_keys="FrameworkMapping.source_control_id",
        cascade="all, delete-orphan",
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    control_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("controls.id", ondelete="CASCADE"), nullable=False
    )
    framework_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("frameworks.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    help_text: Mapped[str | None] = mapped_column(Text)
    question_type: Mapped[str] = mapped_column(String(32), default="YES_NO")
    choices: Mapped[list | None] = mapped_column(JSON)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    maturity_level: Mapped[int | None] = mapped_column(Integer)
    expected_evidence_types: Mapped[list | None] = mapped_column(JSON)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    # Maturity-diagnostic metadata (populated by the diagnostic questionnaire
    # generator; null for plain control questions).
    sub_topic: Mapped[str | None] = mapped_column(String(255))
    maturity_signals: Mapped[dict | None] = mapped_column(JSON)
    gap_if_deficient: Mapped[str | None] = mapped_column(Text)

    control: Mapped["Control"] = relationship("Control", back_populates="questions")


class FrameworkMapping(Base):
    __tablename__ = "framework_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_framework_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("frameworks.id", ondelete="CASCADE"), nullable=False
    )
    source_control_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("controls.id", ondelete="CASCADE")
    )
    target_framework_code: Mapped[str] = mapped_column(String(64), nullable=False)
    target_control_code: Mapped[str] = mapped_column(String(64), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(32), default="RELATED")
    confidence: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
