"""Hindsight routes — the persistent organisational memory surface.

Every route is gated to reviewers (org owner, compliance manager, security
manager, auditor; permissive when no `X-User-Role` header is present, matching
`authz.py`). Memory is historical risk about an organisation, so it is not a
surface contributors are allowed to read. Assessment-scoped routes additionally
verify the caller may access that assessment, which keeps contributors out of
engagements they are not involved in through the
`/assessments/{id}/hindsight/*` surface (e.g. running memory review against an
assessment's recorded findings). Reviewers are organisation-wide by design: the
platform has a single organisation, so there is no cross-tenant reviewer
boundary to enforce here.

The POST review route reads already-recorded deterministic evaluations and
classifies them against stored memory before optionally asking Groq to enrich.
It never changes an evaluation row, a finding, or a compliance status — the
memory layer is additive by construction.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.authz import Caller, require_reviewer
from backend.api.compliance.memory.service import MemoryError, MemoryService
from backend.api.database import get_db
from backend.api.schemas.memory import (
    ControlHistoryOut,
    ExceptionCreateRequest,
    ExceptionGrantedOut,
    ExceptionOut,
    ExceptionRevokeRequest,
    ExceptionsListOut,
    MemoryDecisionOut,
    MemoryDecisionRequest,
    MemoryReviewOut,
    MemoryReviewRequest,
    RecurringFindingsOut,
    RiskSummaryOut,
    SimilarFindingsOut,
)

router = APIRouter(tags=["hindsight"])


def _400_or_404(exc: MemoryError) -> HTTPException:
    """Map service errors: unknowns are 404, invalid requests are 400."""
    message = str(exc)
    if any(tag in message for tag in ("not found", "Unknown decision")):
        return HTTPException(status_code=404, detail=message)
    return HTTPException(status_code=400, detail=message)


async def _ensure_assessment_access(
    db: AsyncSession, caller: Caller, assessment_id: str, what: str
) -> None:
    """Reject callers who are identified but not allowed near an assessment.

    Contributions (team members / evidence contributors) are not allowed here at
    all — `require_reviewer` already blocks them — but a reviewer who is involved
    in assessment A must still not run memory review against assessment B's
    findings through a guessed id. `caller_can_access` scopes that.
    """
    from backend.api.services.assessment_service import AssessmentService

    if not await AssessmentService(db).caller_can_access(caller, assessment_id):
        raise HTTPException(
            status_code=403,
            detail=f"Not assigned to this assessment ({what}).",
        )


@router.post(
    "/assessments/{assessment_id}/hindsight/review",
    response_model=MemoryReviewOut,
)
async def hindsight_review(
    assessment_id: str,
    payload: MemoryReviewRequest,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    """Classify the assessment's recorded findings against stored memory.

    Each finding gets a deterministic memory-aware classification with a human-
    readable `basis` and the list of `matched_memories` that were used, so the
reason for a `KNOWN_EXCEPTION` or `RECURRING_FINDING` is always visible.
    """
    await _ensure_assessment_access(db, caller, assessment_id, what="memory")
    service = MemoryService(db)
    try:
        run = await service.review(
            assessment_id,
            payload.framework_id,
            control_codes=payload.control_codes,
            model_inference=payload.model_inference,
            actor=caller.email,
        )
        await db.commit()
    except MemoryError as exc:
        await db.rollback()
        raise _400_or_404(exc)
    return MemoryReviewOut(
        assessment_id=run.assessment_id,
        framework=run.framework,
        organization_id=run.organization_id,
        reviewed=run.recorded + run.skipped,
        recorded=run.recorded,
        skipped=run.skipped,
        items=run.items,
    )


@router.post(
    "/assessments/{assessment_id}/hindsight/decisions",
    response_model=MemoryDecisionOut,
)
async def hindsight_decision(
    assessment_id: str,
    payload: MemoryDecisionRequest,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    """Record a human's decision on a finding as a memory event.

    `decision == "exception"` additionally creates a `ComplianceException` with
    the given reason and optional expiry, so future reviews label the finding
`KNOWN_EXCEPTION` while the exception is active.
    """
    await _ensure_assessment_access(db, caller, assessment_id, what="memory")
    service = MemoryService(db)
    try:
        outcome = await service.record_decision(
            assessment_id=assessment_id,
            control_code=payload.control_code,
            framework=payload.framework,
            decision=payload.decision,
            human_feedback=payload.human_feedback,
            reason=payload.reason,
            expires_at=payload.expires_at,
            remediation_actions=payload.remediation_actions,
            actor=caller.email,
        )
        await db.commit()
    except MemoryError as exc:
        await db.rollback()
        raise _400_or_404(exc)
    return MemoryDecisionOut(**outcome)


@router.post(
    "/assessments/{assessment_id}/hindsight/exceptions",
    response_model=ExceptionGrantedOut,
)
async def hindsight_grant_exception(
    assessment_id: str,
    payload: ExceptionCreateRequest,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    """Grant an approved exception on a control explicitly."""
    await _ensure_assessment_access(db, caller, assessment_id, what="memory")
    service = MemoryService(db)
    try:
        exception = await service.grant_exception_on_assessment(
            assessment_id,
            framework=payload.framework,
            control_code=payload.control_code,
            reason=payload.reason,
            creator=payload.creator,
            expires_at=payload.expires_at,
            severity=payload.severity,
            actor=caller.email,
        )
        await db.commit()
    except MemoryError as exc:
        await db.rollback()
        raise _400_or_404(exc)
    return ExceptionGrantedOut(
        exception_id=exception.id,
        organization_id=exception.organization_id,
        control_id=exception.control_id,
        status=exception.status,
        created_at=exception.created_at,
    )


@router.post("/hindsight/exceptions/{exception_id}/revoke", response_model=ExceptionOut)
async def hindsight_revoke_exception(
    exception_id: str,
    payload: ExceptionRevokeRequest,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    """Revoke an exception; the control becomes actionable again."""
    service = MemoryService(db)
    try:
        exception = await service.revoke_exception(
            exception_id, reason=payload.reason, actor=caller.email
        )
        await db.commit()
    except MemoryError as exc:
        await db.rollback()
        raise _400_or_404(exc)
    return ExceptionOut.model_validate(exception)


@router.get(
    "/hindsight/organizations/{organization_id}/controls/{control_id}/history",
    response_model=ControlHistoryOut,
)
async def hindsight_control_history(
    organization_id: str,
    control_id: str,
    framework: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    service = MemoryService(db)
    try:
        payload = await service.control_history(
            organization_id, control_id, framework=framework, limit=limit
        )
    except MemoryError as exc:  # pragma: no cover - defensive
        raise _400_or_404(exc)
    return ControlHistoryOut.model_validate(payload)


@router.get(
    "/hindsight/organizations/{organization_id}/similar-findings",
    response_model=SimilarFindingsOut,
)
async def hindsight_similar_findings(
    organization_id: str,
    control_id: str = Query(...),
    framework: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    service = MemoryService(db)
    payload = await service.similar_findings(
        organization_id, control_id, framework=framework, limit=limit
    )
    return SimilarFindingsOut.model_validate(payload)


@router.get(
    "/hindsight/organizations/{organization_id}/recurring-findings",
    response_model=RecurringFindingsOut,
)
async def hindsight_recurring_findings(
    organization_id: str,
    framework: str | None = Query(default=None),
    threshold: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    service = MemoryService(db)
    payload = await service.recurring_findings(
        organization_id, framework=framework, threshold=threshold, limit=limit
    )
    return RecurringFindingsOut.model_validate(payload)


@router.get(
    "/hindsight/organizations/{organization_id}/risk-summary",
    response_model=RiskSummaryOut,
)
async def hindsight_risk_summary(
    organization_id: str,
    framework: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    service = MemoryService(db)
    payload = await service.risk_summary(organization_id, framework=framework)
    return RiskSummaryOut.model_validate(payload)


@router.get(
    "/hindsight/organizations/{organization_id}/exceptions",
    response_model=ExceptionsListOut,
)
async def hindsight_list_exceptions(
    organization_id: str,
    framework: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    service = MemoryService(db)
    payload = await service.list_exceptions(
        organization_id, framework=framework, status=status
    )
    return ExceptionsListOut.model_validate(payload)
