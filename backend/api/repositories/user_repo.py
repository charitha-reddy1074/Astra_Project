from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.api.models.user import User
from backend.api.schemas.user import UserCreate, UserUpdate, VALID_ROLES
from backend.api.authz import CONTRIBUTOR_ROLES


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all(self, role: str | None = None) -> list[User]:
        q = select(User).where(User.is_active == True)
        if role:
            q = q.where(User.role == role)
        result = await self.db.execute(q.order_by(User.created_at.desc()))
        return list(result.scalars().all())

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(
            select(User).where(User.email == email.lower().strip())
        )
        return result.scalar_one_or_none()

    async def create(self, data: UserCreate) -> User:
        email = data.email.lower().strip()
        if not data.name.strip():
            raise ValueError("Name is required.")
        if data.role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {data.role}")
        if data.role in CONTRIBUTOR_ROLES and not (data.organization or "").strip():
            raise ValueError("Contributors must be assigned to an organization.")
        existing = await self.get_by_email(email)
        if existing:
            raise ValueError(f"A user with email {email} already exists.")
        user = User(
            name=data.name.strip(),
            email=email,
            role=data.role,
            organization=(data.organization or "").strip() or None,
            phone=data.phone or None,
            department=data.department or None,
            job_title=data.job_title or None,
            notes=data.notes or None,
        )
        self.db.add(user)
        await self.db.flush()
        return user

    async def update(self, email: str, data: UserUpdate) -> User | None:
        user = await self.get_by_email(email)
        if not user:
            return None
        updates = data.model_dump(exclude_unset=True, exclude_none=True)
        if "role" in updates and updates["role"] not in VALID_ROLES:
            raise ValueError(f"Invalid role: {updates['role']}")
        # Validate the POST-patch state, not just an explicit role change: a patch
        # that only clears `organization` would otherwise orphan an existing
        # contributor, because the old check only fired when "role" was present.
        effective_role = updates.get("role", user.role)
        effective_org = (updates.get("organization", user.organization) or "").strip()
        if effective_role in CONTRIBUTOR_ROLES and not effective_org:
            raise ValueError("Contributors must be assigned to an organization.")
        if "organization" in updates:
            updates["organization"] = effective_org or None
        for field, value in updates.items():
            setattr(user, field, value)
        user.updated_at = datetime.utcnow()
        await self.db.flush()
        return user

    async def delete(self, email: str) -> bool:
        user = await self.get_by_email(email)
        if not user:
            return False
        await self.db.delete(user)
        await self.db.flush()
        return True

    async def count_by_role(self) -> dict[str, int]:
        all_users = await self.get_all()
        counts: dict[str, int] = {}
        for u in all_users:
            counts[u.role] = counts.get(u.role, 0) + 1
        return counts