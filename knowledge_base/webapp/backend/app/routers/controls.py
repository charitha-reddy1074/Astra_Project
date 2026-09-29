"""Write routes for controls (the core editing surface)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from ..db import get_db, transaction
from ..schemas.models import (ControlCreate, ControlDetail, ControlMove,
                             ControlUpdate, HistoryEntry)
from ..security import require_editor
from ..services import controls as svc

router = APIRouter()


@router.get("/controls/{control_pk}", response_model=ControlDetail)
def get_control(control_pk: int, db: sqlite3.Connection = Depends(get_db)):
    return svc.get_detail(db, control_pk)


@router.get("/controls/{control_pk}/history", response_model=list[HistoryEntry])
def get_history(control_pk: int, db: sqlite3.Connection = Depends(get_db)):
    return svc.history(db, control_pk)


@router.post("/frameworks/{framework_id}/controls", response_model=ControlDetail,
             status_code=201, dependencies=[Depends(require_editor)])
def create_control(framework_id: int, payload: ControlCreate,
                   db: sqlite3.Connection = Depends(get_db)):
    with transaction(db):
        return svc.create(db, framework_id, payload)


@router.patch("/controls/{control_pk}", response_model=ControlDetail,
              dependencies=[Depends(require_editor)])
def update_control(control_pk: int, payload: ControlUpdate,
                   db: sqlite3.Connection = Depends(get_db)):
    with transaction(db):
        return svc.update(db, control_pk, payload)


@router.delete("/controls/{control_pk}", status_code=204,
               dependencies=[Depends(require_editor)])
def delete_control(control_pk: int, db: sqlite3.Connection = Depends(get_db)):
    with transaction(db):
        svc.delete(db, control_pk)


@router.post("/controls/{control_pk}/duplicate", response_model=ControlDetail,
             status_code=201, dependencies=[Depends(require_editor)])
def duplicate_control(control_pk: int, db: sqlite3.Connection = Depends(get_db)):
    with transaction(db):
        return svc.duplicate(db, control_pk)


@router.post("/controls/{control_pk}/move", response_model=ControlDetail,
             dependencies=[Depends(require_editor)])
def move_control(control_pk: int, payload: ControlMove,
                 db: sqlite3.Connection = Depends(get_db)):
    with transaction(db):
        return svc.move(db, control_pk, payload.target_node_id)
