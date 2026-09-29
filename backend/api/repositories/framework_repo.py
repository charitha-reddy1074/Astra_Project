from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from backend.api.models.framework import Framework, Domain, Category, Control, Question, FrameworkMapping


class FrameworkRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all(self) -> list[Framework]:
        result = await self.db.execute(select(Framework).order_by(Framework.created_at.desc()))
        return list(result.scalars().all())

    async def get_by_id(self, framework_id: str) -> Framework | None:
        result = await self.db.execute(
            select(Framework)
            .options(
                selectinload(Framework.domains)
                .selectinload(Domain.categories)
                .selectinload(Category.controls)
                .selectinload(Control.questions),
                selectinload(Framework.domains)
                .selectinload(Domain.controls)
                .selectinload(Control.questions),
            )
            .where(Framework.id == framework_id)
        )
        return result.scalar_one_or_none()

    async def get_by_code(self, code: str) -> Framework | None:
        result = await self.db.execute(select(Framework).where(Framework.code == code))
        return result.scalar_one_or_none()

    async def create(self, framework: Framework) -> Framework:
        self.db.add(framework)
        await self.db.flush()
        return framework

    async def delete(self, framework_id: str) -> bool:
        result = await self.db.execute(
            delete(Framework).where(Framework.id == framework_id)
        )
        return result.rowcount > 0

    async def get_controls_for_framework(self, framework_id: str) -> list[Control]:
        result = await self.db.execute(
            select(Control)
            .options(selectinload(Control.questions))
            .where(Control.framework_id == framework_id)
        )
        return list(result.scalars().all())

    async def get_questions_for_framework(self, framework_id: str) -> list[Question]:
        result = await self.db.execute(
            select(Question).where(Question.framework_id == framework_id)
        )
        return list(result.scalars().all())

    async def get_question_by_id(self, question_id: str) -> Question | None:
        result = await self.db.execute(
            select(Question).where(Question.id == question_id)
        )
        return result.scalar_one_or_none()

    async def get_control_by_id(self, control_id: str) -> Control | None:
        result = await self.db.execute(
            select(Control).where(Control.id == control_id)
        )
        return result.scalar_one_or_none()
