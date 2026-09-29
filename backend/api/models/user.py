import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.api.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id:           Mapped[str]      = mapped_column(String(36),  primary_key=True, default=_uuid)
    name:         Mapped[str]      = mapped_column(String(255), nullable=False)
    email:        Mapped[str]      = mapped_column(String(255), nullable=False, unique=True)
    role:         Mapped[str]      = mapped_column(String(50),  nullable=False)
    #: Flat single-organization model: the org a person belongs to. The legacy
    #: `market` column stays in the DB but is no longer mapped.
    organization: Mapped[str|None] = mapped_column(String(255), nullable=True)
    phone:        Mapped[str|None] = mapped_column(String(50),  nullable=True)
    department:   Mapped[str|None] = mapped_column(String(255), nullable=True)
    job_title:    Mapped[str|None] = mapped_column(String(255), nullable=True)
    notes:        Mapped[str|None] = mapped_column(Text,        nullable=True)
    is_active:    Mapped[bool]     = mapped_column(Boolean,     default=True, nullable=False)
    created_at:   Mapped[datetime] = mapped_column(DateTime,    default=datetime.utcnow)
    updated_at:   Mapped[datetime] = mapped_column(DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_users_role",  "role"),
    )