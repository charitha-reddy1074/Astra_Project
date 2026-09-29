"""Activity Log routes — READ-ONLY by design.

The activity log is the platform's permanent record of who did what, when, and
to what. It cannot be edited or deleted, and that guarantee is enforced here by
omission: this router defines GET routes only. There is no POST/PATCH/PUT/DELETE
for a log row anywhere in the API. Events are appended in-process by
services/activity_service.py::record, alongside the action they describe.

Access follows the authz.py convention: reviewers see everything, any other
IDENTIFIED role is rejected, and an unidentified caller (no X-User-Role header —
SSR, tests, curl) stays permissive. Same spoofable ceiling as the rest of the app.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.authz import Caller, get_caller
from backend.api.database import get_db
from backend.api.services.activity_service import ActivityService, parse_date_bound

router = APIRouter(prefix="/activity", tags=["activity"])


def require_activity_reader(caller: Caller = Depends(get_caller)) -> Caller:
    """Reviewers only (org owner, compliance manager, security manager, auditor).
    Unidentified callers are allowed (see module docstring)."""
    if caller.role is None:
        return caller
    if not caller.is_reviewer:
        raise HTTPException(status_code=403, detail="Activity log requires a reviewer role")
    return caller


@router.get("")
async def list_activity(
    q: str | None = Query(default=None, description="Free text across actor name/email, event, market, target and details"),
    action: str | None = Query(default=None, description="Event name, or a namespace prefix like 'assessment'. Comma-separate for several."),
    market: str | None = Query(default=None, description="Exact market label. Comma-separate for several."),
    actor: str | None = Query(default=None, description="Substring of the actor's name or email"),
    date_from: str | None = Query(default=None, alias="from", description="YYYY-MM-DD or DD/MM/YYYY (inclusive)"),
    date_to: str | None = Query(default=None, alias="to", description="YYYY-MM-DD or DD/MM/YYYY (inclusive, whole day)"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_activity_reader),
):
    """Chronological (newest-first) page of activity events."""
    try:
        start = parse_date_bound(date_from)
        end = parse_date_bound(date_to, end_of_day=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="'from' must be on or before 'to'")

    return await ActivityService(db).list_events(
        q=q, action=action, market=market, actor=actor,
        date_from=start, date_to=end, limit=limit, offset=offset,
    )


@router.get("/actions")
async def list_activity_actions(
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_activity_reader),
):
    """Filter facets: every distinct event name (with its count) and market
    present in the log. Feeds the Event / Market dropdowns."""
    svc = ActivityService(db)
    return {
        "actions": await svc.distinct_actions(),
        "markets": await svc.distinct_markets(),
    }
