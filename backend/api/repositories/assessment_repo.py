from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from sqlalchemy.orm import selectinload
from backend.api.models.assessment import (
    Assessment, Questionnaire, Response, Evidence, Finding, Score,
    FollowupQuestion, CategoryRating, DocumentRequest,
)


class AssessmentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all(self) -> list[Assessment]:
        result = await self.db.execute(
            select(Assessment)
            .order_by(Assessment.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, assessment_id: str) -> Assessment | None:
        result = await self.db.execute(
            select(Assessment)
            .options(
                selectinload(Assessment.responses).selectinload(Response.evidence),
                selectinload(Assessment.findings),
                selectinload(Assessment.scores),
                selectinload(Assessment.questionnaire),
                selectinload(Assessment.followups),
                selectinload(Assessment.category_ratings),
                selectinload(Assessment.document_requests),
            )
            .where(Assessment.id == assessment_id)
        )
        return result.scalar_one_or_none()

    async def create(self, assessment: Assessment) -> Assessment:
        self.db.add(assessment)
        await self.db.flush()
        return assessment

    async def delete(self, assessment_id: str) -> bool:
        """Delete an assessment and all of its children (questionnaire, responses,
        evidence, findings, scores) via the ORM cascade. Returns False if missing."""
        assessment = await self.get_by_id(assessment_id)
        if not assessment:
            return False
        await self.db.delete(assessment)
        await self.db.flush()
        return True

    async def update_status(self, assessment_id: str, status: str) -> None:
        await self.db.execute(
            update(Assessment)
            .where(Assessment.id == assessment_id)
            .values(status=status)
        )

    async def update_assigned_to(self, assessment_id: str, assigned_to: str | None) -> None:
        await self.db.execute(
            update(Assessment)
            .where(Assessment.id == assessment_id)
            .values(assigned_to=assigned_to)
        )

    async def update_scores(
        self, assessment_id: str, overall_score: float, maturity_level: int
    ) -> None:
        await self.db.execute(
            update(Assessment)
            .where(Assessment.id == assessment_id)
            .values(overall_score=overall_score, maturity_level=maturity_level)
        )

    async def get_questionnaire(self, assessment_id: str) -> Questionnaire | None:
        result = await self.db.execute(
            select(Questionnaire).where(Questionnaire.assessment_id == assessment_id)
        )
        return result.scalar_one_or_none()

    async def create_questionnaire(self, questionnaire: Questionnaire) -> Questionnaire:
        self.db.add(questionnaire)
        await self.db.flush()
        return questionnaire

    async def get_responses(self, assessment_id: str) -> list[Response]:
        result = await self.db.execute(
            select(Response)
            .options(selectinload(Response.evidence))
            .where(Response.assessment_id == assessment_id)
        )
        return list(result.scalars().all())

    async def get_response_by_question(
        self, assessment_id: str, question_id: str
    ) -> Response | None:
        result = await self.db.execute(
            select(Response).where(
                Response.assessment_id == assessment_id,
                Response.question_id == question_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_response(self, response: Response) -> Response:
        existing = await self.get_response_by_question(
            response.assessment_id, response.question_id
        )
        if existing:
            existing.response_value = response.response_value
            existing.notes = response.notes
            existing.score = response.score
            await self.db.flush()
            return existing
        self.db.add(response)
        await self.db.flush()
        return response

    async def add_evidence(self, evidence: Evidence) -> Evidence:
        self.db.add(evidence)
        await self.db.flush()
        return evidence

    async def get_findings(self, assessment_id: str) -> list[Finding]:
        result = await self.db.execute(
            select(Finding).where(Finding.assessment_id == assessment_id)
        )
        return list(result.scalars().all())

    async def delete_findings(self, assessment_id: str) -> None:
        await self.db.execute(
            delete(Finding).where(Finding.assessment_id == assessment_id)
        )

    async def create_findings(self, findings: list[Finding]) -> list[Finding]:
        for f in findings:
            self.db.add(f)
        await self.db.flush()
        return findings

    async def replace_scores(self, assessment_id: str, scores: list[Score]) -> None:
        await self.db.execute(
            delete(Score).where(Score.assessment_id == assessment_id)
        )
        for s in scores:
            self.db.add(s)
        await self.db.flush()

    async def get_followup_by_id(self, followup_id: str) -> FollowupQuestion | None:
        result = await self.db.execute(
            select(FollowupQuestion).where(FollowupQuestion.id == followup_id)
        )
        return result.scalar_one_or_none()
