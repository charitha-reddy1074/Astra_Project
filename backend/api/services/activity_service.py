"""Activity Log — the platform's permanent, read-only record of major actions.

Backed by the `audit_logs` table (models/audit.py), which is APPEND-ONLY: this
module only ever INSERTs and SELECTs. There is deliberately no update/delete
helper here, and routers/activity.py exposes no write route, so a log row cannot
be altered through the API once written.

Two entry points:

  record(...)        append one event. Call it from anywhere a major action
                     completes (assessment scored, report exported, owner
                     created, …). Never raises into the caller's transaction on
                     a formatting mistake — but it does NOT swallow DB errors,
                     because a silently-missing audit row is worse than a 500.

  list_events(...)   the query behind GET /activity: free-text search, event and
                     market filters, inclusive from/to date range, pagination,
                     newest first.

EVENT NAMES are dotted and namespaced: "<subject>.<past-tense verb>", e.g.
`assessment.created`, `ai.control_score`, `document.accepted`, `report.exported`.
Rows written by the catalog editor before this convention existed carry a bare
verb ("create", "update"); `event_expr()` normalises those to
"<entity_type>.<verb>" in SQL so search, filtering, the distinct-event list and
the API response all agree on one vocabulary without rewriting history.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timezone
from typing import Any, Sequence

from sqlalchemy import String, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.models.audit import AuditLog

# ── event vocabulary ─────────────────────────────────────────────────────────

# Legacy catalog verbs → their dotted equivalent. Keyed by (entity_type, action).
# Anything not listed falls through to "<entity_type>.<action>".
LEGACY_EVENT_ALIASES: dict[tuple[str, str], str] = {
    ("control", "create"):    "catalog.control_created",
    ("control", "update"):    "catalog.control_updated",
    ("control", "delete"):    "catalog.control_deleted",
    ("control", "move"):      "catalog.control_moved",
    ("control", "duplicate"): "catalog.control_duplicated",
}


def event_expr():
    """SQL expression yielding the normalised dotted event name for a row.

    Rows whose `action` already contains a "." are passed through untouched, so
    everything written by `record()` is authoritative about its own event name.
    """
    from sqlalchemy import case

    whens = [(AuditLog.action.like("%.%"), AuditLog.action)]
    for (entity_type, action), event in LEGACY_EVENT_ALIASES.items():
        whens.append((
            (AuditLog.entity_type == entity_type) & (AuditLog.action == action),
            literal(event),
        ))
    return case(
        *whens,
        else_=func.coalesce(AuditLog.entity_type, literal("system"))
              + literal(".") + AuditLog.action,
    ).label("event")


def normalize_event(entity_type: str | None, action: str | None) -> str:
    """Python mirror of `event_expr()` — used when serialising a fetched row."""
    action = action or ""
    if "." in action:
        return action
    alias = LEGACY_EVENT_ALIASES.get(((entity_type or ""), action))
    return alias or f"{entity_type or 'system'}.{action}"


# ── date parsing ─────────────────────────────────────────────────────────────

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y")


def parse_date_bound(raw: str | None, *, end_of_day: bool = False) -> datetime | None:
    """Parse a `from`/`to` filter value into a naive-UTC datetime bound.

    `created_at` is stored as a naive UTC datetime (`datetime.utcnow()`), so every
    bound is normalised to naive UTC before it is compared.

    Accepted forms:
      * a full ISO instant WITH an offset ("2026-07-26T18:30:00Z", "…+05:30") —
        converted to UTC. This is what the UI sends: it turns the picked day into
        the UTC instants bounding that day in the operator's timezone, so "27 Jul"
        means a whole IST day rather than a whole UTC day.
      * a bare date ("2026-07-27" from <input type=date>, or the dd/mm/yyyy the
        client's mock shows) — taken as a UTC day. `to` is pushed to the end of
        that day so the range reads inclusively.
      * a bare ISO timestamp with no offset — taken as UTC.
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        return datetime.combine(d, time.max if end_of_day else time.min)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(
            f"Unrecognised date '{raw}' — use YYYY-MM-DD, DD/MM/YYYY or an ISO timestamp"
        ) from e
    if dt.tzinfo is not None:
        # Convert, don't just strip: dropping "+05:30" would shift the bound 5½h.
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_utc_iso(dt: datetime | None) -> str | None:
    """Serialise a stored timestamp as an explicitly-UTC ISO-8601 string.

    Stored values are naive UTC. Emitting them bare ("2026-07-27T05:15:04") makes
    `new Date(...)` in a browser read them as LOCAL time, which silently shifts
    every row by the viewer's UTC offset. The trailing "Z" removes the ambiguity;
    conversion to a display timezone is the client's job.
    """
    if dt is None:
        return None
    aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


# ── serialisation ────────────────────────────────────────────────────────────

def serialize(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        # Always UTC with an explicit "Z" — see to_utc_iso.
        "date": to_utc_iso(row.created_at),
        "event": normalize_event(row.entity_type, row.action),
        "actor_name": row.actor_name,
        "actor_email": row.user_email,
        "actor_role": row.user_role,
        "market": row.market,
        "details": row.summary,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
    }


class ActivityService:
    """Read/append access to the activity log. No mutation of existing rows."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── append ───────────────────────────────────────────────────────────────
    async def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: str,
        summary: str,
        market: str | None = None,
        actor_email: str | None = None,
        actor_name: str | None = None,
        actor_role: str | None = None,
        changes: dict | None = None,
        occurred_at: datetime | None = None,
        flush: bool = True,
    ) -> AuditLog:
        """Append one event to the log. Returns the (not yet committed) row.

        The caller's session owns the commit, so an event recorded alongside a
        business mutation lands atomically with it. `action` should be a dotted
        event name; `summary` a complete human-readable sentence, because that
        is what the Activity Log renders verbatim in its Details column.
        """
        row = AuditLog(
            id=str(uuid.uuid4()),
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
            changes=changes,
            user_email=(actor_email or None),
            user_role=(actor_role or None),
            actor_name=(actor_name or None),
            market=(market or None),
            summary=summary,
            created_at=occurred_at or datetime.utcnow(),
        )
        self.db.add(row)
        if flush:
            await self.db.flush()
        return row

    # ── read ─────────────────────────────────────────────────────────────────
    def _filtered(self, stmt, *, q, action, market, actor, date_from, date_to):
        event = event_expr()

        if q:
            needle = f"%{q.strip().lower()}%"
            stmt = stmt.where(or_(
                func.lower(func.coalesce(AuditLog.actor_name, "")).like(needle),
                func.lower(func.coalesce(AuditLog.user_email, "")).like(needle),
                func.lower(func.coalesce(AuditLog.summary, "")).like(needle),
                func.lower(func.coalesce(AuditLog.market, "")).like(needle),
                func.lower(func.coalesce(AuditLog.entity_type, "")).like(needle),
                func.lower(func.coalesce(cast(AuditLog.entity_id, String), "")).like(needle),
                func.lower(event).like(needle),
            ))
        if action:
            wanted = [a.strip() for a in _as_list(action) if a and a.strip()]
            if wanted:
                # Match the normalised event, or a whole namespace prefix
                # ("assessment" matches every assessment.* event).
                clauses = []
                for a in wanted:
                    clauses.append(func.lower(event) == a.lower())
                    clauses.append(func.lower(event).like(f"{a.lower()}.%"))
                stmt = stmt.where(or_(*clauses))
        if market:
            wanted = [m.strip() for m in _as_list(market) if m and m.strip()]
            if wanted:
                stmt = stmt.where(func.lower(func.coalesce(AuditLog.market, "")).in_(
                    [m.lower() for m in wanted]))
        if actor:
            needle = f"%{actor.strip().lower()}%"
            stmt = stmt.where(or_(
                func.lower(func.coalesce(AuditLog.actor_name, "")).like(needle),
                func.lower(func.coalesce(AuditLog.user_email, "")).like(needle),
            ))
        if date_from is not None:
            stmt = stmt.where(AuditLog.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(AuditLog.created_at <= date_to)
        return stmt

    async def list_events(
        self,
        *,
        q: str | None = None,
        action: str | Sequence[str] | None = None,
        market: str | Sequence[str] | None = None,
        actor: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Newest-first page of events plus the total matching the same filters."""
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        f = dict(q=q, action=action, market=market, actor=actor,
                 date_from=date_from, date_to=date_to)

        total = await self.db.scalar(
            self._filtered(select(func.count()).select_from(AuditLog), **f)
        ) or 0

        rows = (await self.db.execute(
            self._filtered(select(AuditLog), **f)
            # id as the tiebreaker keeps paging stable when timestamps collide,
            # which they do for events written in the same transaction.
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit).offset(offset)
        )).scalars().all()

        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "items": [serialize(r) for r in rows],
        }

    async def distinct_actions(self) -> list[dict[str, Any]]:
        """Every event name present in the log, with its count — filter dropdown."""
        event = event_expr()
        rows = (await self.db.execute(
            select(event, func.count().label("count"))
            .group_by(event).order_by(event)
        )).all()
        return [{"action": r[0], "count": int(r[1])} for r in rows]

    async def distinct_markets(self) -> list[str]:
        rows = (await self.db.execute(
            select(AuditLog.market).where(AuditLog.market.isnot(None))
            .distinct().order_by(AuditLog.market)
        )).scalars().all()
        return [m for m in rows if m]


def _as_list(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return value.split(",")
    return list(value)


# ── module-level convenience ─────────────────────────────────────────────────

async def record(db: AsyncSession, **kwargs) -> AuditLog:
    """Append an event without constructing the service: `await record(db, ...)`."""
    return await ActivityService(db).record(**kwargs)
