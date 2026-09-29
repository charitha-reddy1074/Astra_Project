"""Admin framework-catalog management service.

Ports the knowledge_base FMS services (controls / catalog / validation / audit)
onto the main app's async SQLAlchemy store. A "control" to the UI is the control
row + its questions, edited as one unit. Every mutation is audit-logged and guarded
by a soft optimistic-concurrency token (row_hash).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select, delete, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.config import HIDDEN_FRAMEWORK_CODES
from backend.api.models.framework import Framework, Domain, Category, Control, Question
from backend.api.models.audit import AuditLog
from backend.api.schemas.catalog import (
    ControlCreate, ControlUpdate, ControlDetail, QuestionOut, QuestionIn,
    ControlListItem, ValidationIssue, ValidationReport, SearchHit,
    CatalogStats, FrameworkStat, ActivityEntry, HistoryEntry,
)


def _uuid() -> str:
    return str(uuid.uuid4())


class CatalogService:
    def __init__(self, db: AsyncSession, caller_email: str | None = None,
                 caller_role: str | None = None):
        self.db = db
        self.caller_email = caller_email
        self.caller_role = caller_role

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _row_hash(control: Control, questions: list[Question]) -> str:
        """Stable hash of the editable state for soft optimistic concurrency."""
        q_basis = [
            {
                "text": q.text or "",
                "question_type": q.question_type or "",
                "weight": q.weight,
                "help_text": q.help_text or "",
                "order_index": q.order_index,
            }
            for q in sorted(questions, key=lambda x: (x.order_index, x.id or ""))
        ]
        basis = "|".join([
            control.statement or "",
            control.name or "",
            f"{control.weight}",
            control.criticality or "",
            json.dumps(q_basis, sort_keys=True, ensure_ascii=False),
        ])
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    async def _framework_code(self, framework_id: str) -> str:
        code = await self.db.scalar(
            select(Framework.code).where(Framework.id == framework_id))
        return code or ""

    async def _assert_market_framework(self, framework_id: str) -> None:
        """Raise 403 if the framework is not a Market Assessment framework."""
        code = await self._framework_code(framework_id)
        if "MARKET" not in code.upper():
            raise HTTPException(
                403, "Only Market Assessment frameworks can be modified via this interface."
            )

    async def _load_control(self, control_pk: str) -> Control:
        res = await self.db.execute(
            select(Control)
            .options(
                selectinload(Control.questions),
                selectinload(Control.domain),
                selectinload(Control.category),
            )
            .where(Control.id == control_pk)
        )
        ctrl = res.scalar_one_or_none()
        if ctrl is None:
            raise HTTPException(404, f"Control {control_pk} not found")
        return ctrl

    async def _detail_from(self, ctrl: Control) -> ControlDetail:
        fw_code = await self._framework_code(ctrl.framework_id)
        questions = sorted(ctrl.questions, key=lambda q: (q.order_index, q.id))
        return ControlDetail(
            id=ctrl.id,
            framework_id=ctrl.framework_id,
            framework_code=fw_code,
            domain_id=ctrl.domain_id,
            domain_code=ctrl.domain.code if ctrl.domain else "",
            domain_name=ctrl.domain.name if ctrl.domain else "",
            category_id=ctrl.category_id,
            category_code=ctrl.category.code if ctrl.category else ctrl.category_code,
            category_name=(ctrl.category.name if ctrl.category else ctrl.category_name),
            code=ctrl.code,
            name=ctrl.name,
            statement=ctrl.statement,
            weight=ctrl.weight,
            criticality=ctrl.criticality,
            order_index=ctrl.order_index,
            questions=[
                QuestionOut(
                    id=q.id, text=q.text, help_text=q.help_text,
                    question_type=q.question_type, choices=q.choices,
                    weight=q.weight, maturity_level=q.maturity_level,
                    expected_evidence_types=q.expected_evidence_types,
                    order_index=q.order_index,
                ) for q in questions
            ],
            row_hash=self._row_hash(ctrl, questions),
        )

    async def _audit(self, *, entity_type: str, entity_id: str, action: str,
                     before=None, after=None) -> None:
        self.db.add(AuditLog(
            id=_uuid(), entity_type=entity_type, entity_id=entity_id, action=action,
            changes={"before": before, "after": after},
            user_email=self.caller_email, user_role=self.caller_role,
            created_at=datetime.utcnow(),
        ))

    async def _recount_framework(self, framework_id: str) -> None:
        n_controls = await self.db.scalar(
            select(func.count(Control.id)).where(Control.framework_id == framework_id)) or 0
        n_questions = await self.db.scalar(
            select(func.count(Question.id)).where(Question.framework_id == framework_id)) or 0
        await self.db.execute(
            text("UPDATE frameworks SET total_controls=:c, total_questions=:q WHERE id=:fid"),
            {"c": int(n_controls), "q": int(n_questions), "fid": framework_id},
        )

    async def _write_questions(self, framework_id: str, control_pk: str,
                               questions: list[QuestionIn]) -> None:
        await self.db.execute(delete(Question).where(Question.control_id == control_pk))
        for i, q in enumerate(questions):
            self.db.add(Question(
                id=_uuid(), control_id=control_pk, framework_id=framework_id,
                text=q.text, help_text=q.help_text,
                question_type=q.question_type, choices=q.choices,
                weight=float(q.weight), maturity_level=q.maturity_level,
                expected_evidence_types=q.expected_evidence_types,
                order_index=q.order_index if q.order_index else i,
            ))

    # ── read ─────────────────────────────────────────────────────────────────
    async def get_control_detail(self, control_pk: str) -> ControlDetail:
        return await self._detail_from(await self._load_control(control_pk))

    # ── create / update / delete / duplicate / move ──────────────────────────
    async def create_control(self, framework_id: str, payload: ControlCreate) -> ControlDetail:
        await self._assert_market_framework(framework_id)
        category = await self.db.get(Category, payload.category_id)
        if category is None or category.framework_id != framework_id:
            raise HTTPException(400, "category_id does not belong to this framework")
        dup = await self.db.scalar(
            select(Control.id).where(Control.framework_id == framework_id,
                                     Control.code == payload.code))
        if dup:
            raise HTTPException(409, f"control code '{payload.code}' already exists")

        max_order = await self.db.scalar(
            select(func.coalesce(func.max(Control.order_index), -1))
            .where(Control.framework_id == framework_id)) or -1
        pk = _uuid()
        self.db.add(Control(
            id=pk, domain_id=category.domain_id, category_id=category.id,
            framework_id=framework_id, code=payload.code, name=payload.name,
            statement=payload.statement, category_code=category.code,
            category_name=category.name, weight=float(payload.weight),
            criticality=payload.criticality, order_index=max_order + 1,
        ))
        await self.db.flush()
        await self._write_questions(framework_id, pk, payload.questions)
        await self._audit(entity_type="control", entity_id=pk, action="create",
                          after={"code": payload.code})
        await self._recount_framework(framework_id)
        await self.db.flush()
        return await self._detail_from(await self._load_control(pk))

    async def update_control(self, control_pk: str, payload: ControlUpdate) -> ControlDetail:
        before = await self._detail_from(await self._load_control(control_pk))
        await self._assert_market_framework(before.framework_id)
        if payload.row_hash and payload.row_hash != before.row_hash:
            raise HTTPException(
                409, "This control changed since you opened it. Reload to see the latest.")

        ctrl = await self.db.get(Control, control_pk)
        if payload.name is not None:
            ctrl.name = payload.name
        if payload.statement is not None:
            ctrl.statement = payload.statement
        if payload.weight is not None:
            ctrl.weight = float(payload.weight)
        if payload.criticality is not None:
            ctrl.criticality = payload.criticality
        if payload.questions is not None:
            await self._write_questions(ctrl.framework_id, control_pk, payload.questions)
        await self.db.flush()

        after = await self._detail_from(await self._load_control(control_pk))
        await self._audit(
            entity_type="control", entity_id=control_pk, action="update",
            before=before.model_dump(include={"name", "statement", "weight", "criticality"}),
            after=after.model_dump(include={"name", "statement", "weight", "criticality"}),
        )
        await self._recount_framework(ctrl.framework_id)
        return after

    async def delete_control(self, control_pk: str) -> None:
        ctrl = await self._load_control(control_pk)
        await self._assert_market_framework(ctrl.framework_id)
        framework_id = ctrl.framework_id
        code = ctrl.code
        await self.db.execute(delete(Question).where(Question.control_id == control_pk))
        await self.db.execute(delete(Control).where(Control.id == control_pk))
        await self._audit(entity_type="control", entity_id=control_pk, action="delete",
                          before={"code": code})
        await self._recount_framework(framework_id)

    async def duplicate_control(self, control_pk: str) -> ControlDetail:
        src = await self._detail_from(await self._load_control(control_pk))
        base = f"{src.code}-COPY"
        new_code, n = base, 1
        while await self.db.scalar(
            select(Control.id).where(Control.framework_id == src.framework_id,
                                     Control.code == new_code)):
            n += 1
            new_code = f"{base}{n}"
        payload = ControlCreate(
            category_id=src.category_id, code=new_code, name=src.name,
            statement=src.statement, weight=src.weight, criticality=src.criticality,
            questions=[QuestionIn(**{**q.model_dump(), "id": None}) for q in src.questions],
        )
        return await self.create_control(src.framework_id, payload)

    async def move_control(self, control_pk: str, target_category_id: str) -> ControlDetail:
        ctrl = await self.db.get(Control, control_pk)
        if ctrl is None:
            raise HTTPException(404, "Control not found")
        await self._assert_market_framework(ctrl.framework_id)
        category = await self.db.get(Category, target_category_id)
        if category is None:
            raise HTTPException(404, "Target category not found")
        if category.framework_id != ctrl.framework_id:
            raise HTTPException(400, "Cannot move a control across frameworks")
        ctrl.category_id = category.id
        ctrl.domain_id = category.domain_id
        ctrl.category_code = category.code
        ctrl.category_name = category.name
        await self.db.flush()
        await self._audit(entity_type="control", entity_id=control_pk, action="move",
                          after={"category_id": target_category_id})
        return await self._detail_from(await self._load_control(control_pk))

    async def history(self, control_pk: str) -> list[HistoryEntry]:
        res = await self.db.execute(
            select(AuditLog).where(AuditLog.entity_type == "control",
                                   AuditLog.entity_id == control_pk)
            .order_by(AuditLog.created_at.desc()).limit(100))
        return [
            HistoryEntry(id=r.id, action=r.action, changes=r.changes,
                         user_email=r.user_email, user_role=r.user_role,
                         created_at=r.created_at)
            for r in res.scalars().all()
        ]

    # ── validation ───────────────────────────────────────────────────────────
    async def validate_framework(self, framework_id: str) -> ValidationReport:
        issues: list[ValidationIssue] = []

        # Duplicate control codes within the framework.
        rows = await self.db.execute(text(
            "SELECT code, COUNT(*) c FROM controls WHERE framework_id=:fid "
            "GROUP BY code HAVING c > 1"), {"fid": framework_id})
        for code, c in rows:
            issues.append(ValidationIssue(
                severity="error", rule="duplicate_control",
                message=f"Control code '{code}' appears {c} times",
                entity_type="control", entity_ref=code))

        # Controls with no questions.
        rows = await self.db.execute(text(
            "SELECT id, code FROM controls c WHERE framework_id=:fid AND NOT EXISTS "
            "(SELECT 1 FROM questions q WHERE q.control_id=c.id)"), {"fid": framework_id})
        for cid, code in rows:
            issues.append(ValidationIssue(
                severity="warning", rule="missing_questions",
                message=f"Control '{code}' has no questions",
                entity_type="control", entity_id=cid, entity_ref=code))

        # Empty statements.
        rows = await self.db.execute(text(
            "SELECT id, code FROM controls WHERE framework_id=:fid AND "
            "(statement IS NULL OR TRIM(statement)='')"), {"fid": framework_id})
        for cid, code in rows:
            issues.append(ValidationIssue(
                severity="error", rule="empty_statement",
                message=f"Control '{code}' has an empty statement",
                entity_type="control", entity_id=cid, entity_ref=code))

        # Orphan controls: category_id missing / dangling.
        rows = await self.db.execute(text(
            "SELECT id, code FROM controls c WHERE framework_id=:fid AND "
            "(category_id IS NULL OR NOT EXISTS "
            "(SELECT 1 FROM categories cat WHERE cat.id=c.category_id))"),
            {"fid": framework_id})
        for cid, code in rows:
            issues.append(ValidationIssue(
                severity="warning", rule="orphan_control",
                message=f"Control '{code}' is not attached to a category",
                entity_type="control", entity_id=cid, entity_ref=code))

        # Empty named categories (real sub-domains with no controls).
        rows = await self.db.execute(text(
            "SELECT id, code FROM categories cat WHERE framework_id=:fid AND "
            "name IS NOT NULL AND TRIM(name)<>'' AND NOT EXISTS "
            "(SELECT 1 FROM controls c WHERE c.category_id=cat.id)"),
            {"fid": framework_id})
        for cid, code in rows:
            issues.append(ValidationIssue(
                severity="warning", rule="empty_category",
                message=f"Category '{code}' has no controls",
                entity_type="category", entity_id=cid, entity_ref=code))

        errors = sum(1 for i in issues if i.severity == "error")
        warnings = sum(1 for i in issues if i.severity == "warning")
        return ValidationReport(
            framework_id=framework_id, ok=errors == 0,
            error_count=errors, warning_count=warnings, issues=issues)

    # ── search ───────────────────────────────────────────────────────────────
    async def search(self, term: str, limit: int = 50) -> list[SearchHit]:
        like = f"%{term}%"
        rows = await self.db.execute(text("""
            SELECT c.id control_id, c.framework_id, f.code framework_code,
                   c.code, c.name, c.statement, d.code domain_code,
                   CASE
                     WHEN c.code LIKE :like THEN 'code'
                     WHEN c.name LIKE :like THEN 'name'
                     WHEN c.statement LIKE :like THEN 'statement'
                     ELSE 'question'
                   END match_field
            FROM controls c
            JOIN frameworks f ON f.id = c.framework_id
            JOIN domains d ON d.id = c.domain_id
            WHERE (c.code LIKE :like OR c.name LIKE :like OR c.statement LIKE :like
               OR EXISTS (SELECT 1 FROM questions q
                          WHERE q.control_id=c.id AND q.text LIKE :like))
            ORDER BY CASE WHEN c.code LIKE :like THEN 0 ELSE 1 END, f.code, c.order_index
            LIMIT :limit
        """), {"like": like, "limit": limit})
        return [
            SearchHit(control_id=r.control_id, framework_id=r.framework_id,
                      framework_code=r.framework_code, code=r.code, name=r.name,
                      statement=r.statement, domain_code=r.domain_code,
                      match_field=r.match_field)
            for r in rows
        ]

    # ── dashboard / stats ────────────────────────────────────────────────────
    async def catalog_stats(self) -> CatalogStats:
        fw_res = await self.db.execute(
            select(Framework).order_by(Framework.name))
        frameworks = [f for f in fw_res.scalars().all()
                      if f.code not in HIDDEN_FRAMEWORK_CODES]
        fw_ids = [f.id for f in frameworks]

        detail = [
            FrameworkStat(
                id=f.id, code=f.code, name=f.name, version=f.version,
                total_domains=f.total_domains, total_categories=f.total_categories,
                total_controls=f.total_controls, total_questions=f.total_questions)
            for f in frameworks
        ]

        controls_without_q = 0
        n_domains = 0
        n_categories = 0
        n_controls = 0
        n_questions = 0

        if fw_ids:
            placeholders = ", ".join(f"'{fid}'" for fid in fw_ids)
            controls_without_q = int(await self.db.scalar(text(
                f"SELECT COUNT(*) FROM controls c WHERE c.framework_id IN ({placeholders}) "
                "AND NOT EXISTS (SELECT 1 FROM questions q WHERE q.control_id=c.id)")) or 0)
            n_domains = int(await self.db.scalar(text(
                f"SELECT COUNT(*) FROM domains WHERE framework_id IN ({placeholders})")) or 0)
            n_categories = int(await self.db.scalar(text(
                f"SELECT COUNT(*) FROM categories c JOIN domains d ON d.id=c.domain_id "
                f"WHERE d.framework_id IN ({placeholders})")) or 0)
            n_controls = int(await self.db.scalar(text(
                f"SELECT COUNT(*) FROM controls WHERE framework_id IN ({placeholders})")) or 0)
            n_questions = int(await self.db.scalar(text(
                f"SELECT COUNT(*) FROM questions WHERE framework_id IN ({placeholders})")) or 0)

        act_res = await self.db.execute(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10))
        recent = [
            ActivityEntry(id=a.id, entity_type=a.entity_type, entity_id=a.entity_id,
                          action=a.action, user_email=a.user_email, created_at=a.created_at)
            for a in act_res.scalars().all()
        ]

        bad = 0
        for f in frameworks:
            report = await self.validate_framework(f.id)
            if report.error_count > 0:
                bad += 1

        return CatalogStats(
            frameworks=len(frameworks),
            domains=n_domains,
            categories=n_categories,
            controls=n_controls,
            questions=n_questions,
            controls_without_questions=controls_without_q,
            validation_error_frameworks=bad,
            frameworks_detail=detail,
            recent_activity=recent,
        )

    # ── export ───────────────────────────────────────────────────────────────
    async def export_framework(self, framework_id: str) -> dict:
        res = await self.db.execute(
            select(Framework)
            .options(
                selectinload(Framework.domains)
                .selectinload(Domain.categories)
                .selectinload(Category.controls)
                .selectinload(Control.questions))
            .where(Framework.id == framework_id))
        fw = res.scalar_one_or_none()
        if fw is None:
            raise HTTPException(404, f"Framework {framework_id} not found")
        return {
            "code": fw.code, "name": fw.name, "version": fw.version,
            "description": fw.description,
            "domains": [
                {
                    "code": d.code, "name": d.name, "description": d.description,
                    "categories": [
                        {
                            "code": cat.code, "name": cat.name,
                            "criteria_statement": cat.criteria_statement,
                            "controls": [
                                {
                                    "code": c.code, "name": c.name,
                                    "statement": c.statement, "weight": c.weight,
                                    "criticality": c.criticality,
                                    "questions": [
                                        {"text": q.text, "question_type": q.question_type,
                                         "weight": q.weight, "choices": q.choices,
                                         "expected_evidence_types": q.expected_evidence_types}
                                        for q in sorted(c.questions, key=lambda x: x.order_index)
                                    ],
                                } for c in cat.controls
                            ],
                        } for cat in d.categories
                    ],
                } for d in fw.domains
            ],
        }
