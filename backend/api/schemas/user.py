from datetime import datetime
from pydantic import BaseModel

from backend.api.authz import ORGANIZATION_ROLES


VALID_ROLES = ORGANIZATION_ROLES


class UserCreate(BaseModel):
    name:         str
    email:        str
    role:         str
    organization: str | None = None
    phone:        str | None = None
    department:   str | None = None
    job_title:    str | None = None
    notes:        str | None = None


class UserUpdate(BaseModel):
    name:         str | None = None
    role:         str | None = None
    organization: str | None = None
    phone:        str | None = None
    department:   str | None = None
    job_title:    str | None = None
    notes:        str | None = None
    is_active:    bool | None = None


class UserOut(BaseModel):
    id:           str
    name:         str
    email:        str
    role:         str
    organization: str | None
    phone:        str | None
    department:   str | None
    job_title:    str | None
    notes:        str | None
    is_active:    bool
    created_at:   datetime
    updated_at:   datetime

    class Config:
        from_attributes = True