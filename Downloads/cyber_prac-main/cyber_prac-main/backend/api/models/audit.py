import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.api.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AuditLog(Base):
    """Append-only activity record for every major action taken in the platform.

    Originally the change history for catalog mutations (framework management):
    every create/update/delete/duplicate/move on a control is recorded here with
    a before/after JSON snapshot and the (spoofable) caller identity, so the
    admin "Manage" surface can show per-control edit history. Ported from the
    knowledge_base FMS `audit_logs` table, adapted to the main SQLAlchemy store.

    It is now ALSO the backing store for the platform-wide Activity Log
    (`GET /activity`, see services/activity_service.py). That surface needs three
    things the catalog history never carried, all nullable so existing rows stay
    valid:

      actor_name  the display name of the person who acted, denormalised at write
                  time so the log stays readable after a user is renamed/deleted
      market      the market the action affected ("France", "ASIA", …) — NULL for
                  global/platform-level actions such as catalog edits
      summary     one human-readable sentence describing what happened, e.g.
                  "AI control scoring ran for IAM-C06."

    INVARIANT — this table is append-only. Rows are INSERTed and never UPDATEd or
    DELETEd, and no HTTP route exposes a write/edit/delete of a log row. The only
    sanctioned exception is scripts/backfill_activity_log.py filling the three
    columns above on rows written before they existed.
    """
    __tablename__ = "audit_logs"

    id:          Mapped[str]      = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_type: Mapped[str]      = mapped_column(String(32), nullable=False)
    entity_id:   Mapped[str]      = mapped_column(String(36), nullable=False)
    # Dotted, namespaced event name for new rows ("ai.control_score",
    # "report.exported"). Legacy catalog rows hold a bare verb ("create") and are
    # normalised to "<entity_type>.<verb>" on read — see activity_service.event_expr.
    action:      Mapped[str]      = mapped_column(String(64), nullable=False)
    changes:     Mapped[dict|None] = mapped_column(JSON)        # {"before": ..., "after": ...}
    user_email:  Mapped[str|None] = mapped_column(String(255))
    user_role:   Mapped[str|None] = mapped_column(String(50))
    actor_name:  Mapped[str|None] = mapped_column(String(255))
    market:      Mapped[str|None] = mapped_column(String(255))
    summary:     Mapped[str|None] = mapped_column(Text)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_created_at", "created_at"),
        Index("ix_audit_action", "action"),
    )
