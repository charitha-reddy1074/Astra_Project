"""Admin framework-catalog management routes (Knowledge Base → Manage).

DB-backed port of the knowledge_base FMS. The whole router is gated to the Organization Owner via `require_organization_owner`. Reads reuse the existing
`/frameworks` + `/frameworks/{id}` endpoints for the tree; these routes add the
control-level editing, validation, audit history, search, and stats.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.authz import Caller, require_organization_owner
from backend.api.database import get_db
from backend.api.services.catalog_service import CatalogService
from backend.api.schemas.catalog import (
    ControlCreate, ControlUpdate, ControlMove, ControlDetail,
    ValidationReport, SearchHit, CatalogStats, HistoryEntry,
)

router = APIRouter(prefix="/catalog", tags=["catalog"],
                   dependencies=[Depends(require_organization_owner)])


def _svc(db: AsyncSession, caller: Caller) -> CatalogService:
    return CatalogService(db, caller_email=caller.email, caller_role=caller.role)


# ── dashboard / search ───────────────────────────────────────────────────────
@router.get("/stats", response_model=CatalogStats)
async def catalog_stats(db: AsyncSession = Depends(get_db),
                        caller: Caller = Depends(require_organization_owner)):
    return await _svc(db, caller).catalog_stats()


@router.get("/search", response_model=list[SearchHit])
async def catalog_search(q: str = Query(min_length=1), limit: int = 50,
                         db: AsyncSession = Depends(get_db),
                         caller: Caller = Depends(require_organization_owner)):
    return await _svc(db, caller).search(q, limit)


# ── framework-scoped ─────────────────────────────────────────────────────────
@router.get("/frameworks/{framework_id}/validate", response_model=ValidationReport)
async def validate_framework(framework_id: str, db: AsyncSession = Depends(get_db),
                             caller: Caller = Depends(require_organization_owner)):
    return await _svc(db, caller).validate_framework(framework_id)


@router.get("/frameworks/{framework_id}/export")
async def export_framework(framework_id: str, db: AsyncSession = Depends(get_db),
                           caller: Caller = Depends(require_organization_owner)):
    return await _svc(db, caller).export_framework(framework_id)


@router.post("/frameworks/{framework_id}/controls", response_model=ControlDetail,
             status_code=201)
async def create_control(framework_id: str, payload: ControlCreate,
                         db: AsyncSession = Depends(get_db),
                         caller: Caller = Depends(require_organization_owner)):
    return await _svc(db, caller).create_control(framework_id, payload)


# ── control-scoped ───────────────────────────────────────────────────────────
@router.get("/controls/{control_pk}", response_model=ControlDetail)
async def get_control(control_pk: str, db: AsyncSession = Depends(get_db),
                      caller: Caller = Depends(require_organization_owner)):
    return await _svc(db, caller).get_control_detail(control_pk)


@router.get("/controls/{control_pk}/history", response_model=list[HistoryEntry])
async def control_history(control_pk: str, db: AsyncSession = Depends(get_db),
                          caller: Caller = Depends(require_organization_owner)):
    return await _svc(db, caller).history(control_pk)


@router.patch("/controls/{control_pk}", response_model=ControlDetail)
async def update_control(control_pk: str, payload: ControlUpdate,
                         db: AsyncSession = Depends(get_db),
                         caller: Caller = Depends(require_organization_owner)):
    return await _svc(db, caller).update_control(control_pk, payload)


@router.delete("/controls/{control_pk}", status_code=204)
async def delete_control(control_pk: str, db: AsyncSession = Depends(get_db),
                         caller: Caller = Depends(require_organization_owner)):
    await _svc(db, caller).delete_control(control_pk)
    return Response(status_code=204)


@router.post("/controls/{control_pk}/duplicate", response_model=ControlDetail,
             status_code=201)
async def duplicate_control(control_pk: str, db: AsyncSession = Depends(get_db),
                            caller: Caller = Depends(require_organization_owner)):
    return await _svc(db, caller).duplicate_control(control_pk)


@router.post("/controls/{control_pk}/move", response_model=ControlDetail)
async def move_control(control_pk: str, payload: ControlMove,
                       db: AsyncSession = Depends(get_db),
                       caller: Caller = Depends(require_organization_owner)):
    return await _svc(db, caller).move_control(control_pk, payload.target_category_id)

