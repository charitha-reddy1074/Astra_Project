"""Authorization guards.

Stub for now: reads the configured role from settings. Structured so that
swapping in real auth (JWT/session -> user -> role) touches only this file.
"""
from __future__ import annotations

from fastapi import HTTPException

from .settings import CURRENT_USER_ROLE

_EDITOR_ROLES = {"engineer", "admin"}


def require_editor() -> None:
    if CURRENT_USER_ROLE not in _EDITOR_ROLES:
        raise HTTPException(403, "Your role does not permit editing the catalog")
