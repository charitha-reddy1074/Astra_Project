"""DB orchestration for compliance evaluations.

Owns the parts that need a database; the decision logic stays in the pure
modules. Specifically it:
  * loads the assessment's in-scope controls and resolves them to `ControlSpec`;
  * gathers answer + evidence per control (`evidence.py`);
  * runs the deterministic evaluator over the whole scope;
  * persists one `ComplianceEvaluation` row per control per run;
  * raises a `Finding` for every non-conclusive result, back-linked to its
    evaluation;
  * writes an audit entry describing the run.

NO LLM anywhere in this path. The LLM is allowed to *summarise* a completed
evaluation elsewhere; it can never influence a status.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.compliance.adapters.base import FrameworkAdapter
from backend.api.compliance.adapters.registry import get_adapter
from backend.api.compliance.enums import ComplianceStatus
from backend.api.compliance.evidence import ControlEvidence, gather_control_evidence
from backend.api.compliance.evaluator import evaluate_control
from backend.api.compliance.types import ControlSpec, EvaluationResult
from backend.api.models.compliance import ComplianceEvaluation
from backend.api.models.framework import Control, Domain, Framework
from backend.api.models.assessment import Assessment, Finding

#: Statuses that raise a finding. NOT_APPLICABLE raises nothing: an out-of-scope
#: control is a scoping decision, not a gap.
_FINDING_STATUSES = frozenset({
    ComplianceStatus.FAIL,
    ComplianceStatus.PARTIAL,
    ComplianceStatus.INSUFFICIENT_EVIDENCE,
})

_FINDING_TITLES = {
    ComplianceStatus.FAIL: "Control requirement not met",
    ComplianceStatus.PARTIAL: "Control requirement only partly evidenced",
    ComplianceStatus.INSUFFICIENT_EVIDENCE: "Insufficient evidence to evaluate control",
}


class ComplianceError(RuntimeError):
    """Base error for the compliance evaluation surface."""


class FrameworkNotEvaluable(ComplianceError):
    """The requested framework has no compliance adapter in scope."""


@dataclass
class EvaluationRun:
    """The outcome of evaluating one framework across an assessment."""
    run_id: str
    assessment_id: str
    framework: str
    assurance_level: str
    results: list[EvaluationResult]
    rows: list[ComplianceEvaluation]
    findings: list[Finding]

    @property
    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {s.value: 0 for s in ComplianceStatus}
        for result in self.results:
            counts[result.status.value] += 1
        conclusive = [r for r in self.results if r.status.is_conclusive]
        return {
            "run_id": self.run_id,
            "assessment_id": self.assessment_id,
            "framework": self.framework,
            "assurance_level": self.assurance_level,
            "asserts_operating_effectiveness": False,
            "total": len(self.results),
            "counts": counts,
            "conclusive": len(conclusive),
            "conclusive_pct": (
                round(100.0 * len(conclusive) / len(self.results), 1)
                if self.results else 0.0
            ),
            "mean_confidence": (
                round(sum(r.confidence for r in self.results) / len(self.results), 3)
                if self.results else 0.0
            ),
        }


# ── loading controls from the database ──────────────────────────────────────

def control_spec_from_row(
    adapter: FrameworkAdapter,
    control: Control,
    domain_code: str = "",
    domain_name: str = "",
) -> ControlSpec:
    """Resolve a persisted `Control` into the evaluable `ControlSpec`.

    The database is the system of record, so requirements are read back from the
    row rather than re-read from the dataset file — an operator's edit to a
    control statement is then evaluated as written. The dataset's own metadata
    rode in through `Control.maturity_criteria` on import, which is where the
    SOC 2 `points_of_focus` clauses come from.
    """
    guide = control.maturity_criteria if isinstance(control.maturity_criteria, dict) else {}
    dataset_metadata = guide.get("dataset_metadata")
    if not isinstance(dataset_metadata, Mapping):
        dataset_metadata = {}

    requirement = adapter.build_requirement_from_metadata(
        control_id=control.code,
        statement=control.statement or "",
        category_code=control.category_code or "",
        category_name=control.category_name or "",
        metadata=dataset_metadata,
    )
    metadata = dict(dataset_metadata)
    for extra in ("category_objective", "item_type", "scope_cadence",
                  "preferred_tooling", "standard_referenced"):
        if guide.get(extra) is not None:
            metadata.setdefault(extra, guide.get(extra))

    return ControlSpec(
        framework=adapter.key,
        framework_name=adapter.name,
        control_id=control.code,
        requirement=requirement,
        domain_code=domain_code,
        domain_name=domain_name,
        statement=control.statement or "",
        criticality=(control.criticality or adapter.criticality(control.code)),
        expected_evidence_types=adapter.expected_evidence_types_from_metadata(
            control.code, metadata
        ),
        metadata=metadata,
    )


async def load_evaluable_framework(
    db: AsyncSession,
    framework_id: str,
) -> tuple[Framework, FrameworkAdapter]:
    """Load a framework and its adapter, or explain why it cannot be evaluated."""
    framework = (await db.execute(
        select(Framework).where(Framework.id == framework_id)
    )).scalar_one_or_none()
    if framework is None:
        raise ComplianceError(f"Framework {framework_id} not found.")

    adapter = get_adapter(framework.code, framework.name)
    if adapter is None:
        from backend.api.compliance.adapters.registry import list_adapters
        supported = ", ".join(sorted(a.key for a in list_adapters()))
        raise FrameworkNotEvaluable(
            f"Framework '{framework.name}' ({framework.code}) has no compliance "
            f"evaluation profile. Supported: {supported}."
        )
    return framework, adapter


async def load_controls_in_scope(
    db: AsyncSession,
    assessment: Assessment,
    framework: Framework,
) -> tuple[list[ControlSpec], dict[str, str], dict[str, str]]:
    """Controls in the assessment's scope, plus two lookup maps.

    Honours `Assessment.selected_domains` the same way
    `AssessmentService._build_question_map` does, so evaluating a scoped
    assessment and reading its report cover the same ground.

    Returns ``(specs, control_id_to_code, control_code_to_domain)``.
    """
    from backend.api.services.assessment_service import _domain_selected

    selected = set(assessment.selected_domains or [])
    rows = (await db.execute(
        select(Control, Domain).join(Domain, Control.domain_id == Domain.id)
        .where(Control.framework_id == framework.id)
        .order_by(Domain.order_index, Control.order_index)
    )).all()

    adapter = get_adapter(framework.code, framework.name)
    assert adapter is not None, "caller must have resolved the adapter"

    specs: list[ControlSpec] = []
    id_to_code: dict[str, str] = {}
    code_to_domain: dict[str, str] = {}
    for control, domain in rows:
        if selected and not _domain_selected(framework.id, domain, selected):
            continue
        specs.append(control_spec_from_row(adapter, control, domain.code, domain.name))
        id_to_code[control.id] = control.code
        code_to_domain[control.code] = domain.code
    return specs, id_to_code, code_to_domain


# ── persistence ─────────────────────────────────────────────────────────────

def _row_from_result(
    result: EvaluationResult,
    *,
    assessment_id: str,
    run_id: str,
    framework: Framework,
    actor: str | None,
    control_row_id: str | None = None,
) -> ComplianceEvaluation:
    return ComplianceEvaluation(
        assessment_id=assessment_id,
        run_id=run_id,
        framework_id=framework.id,
        framework=result.framework,
        framework_code=framework.code,
        #: The Control row this claim is about, so a row can be joined back to
        #: the control it evaluated. `control_code` is the portable identifier;
        #: `control_id` is this installation's primary key.
        control_id=control_row_id,
        control_code=result.control_id,
        domain_code=result.domain_code or None,
        domain_name=result.domain_name or None,
        requirement_id=result.requirement_id or None,
        requirement=result.requirement,
        requirement_clauses=list(result.requirement_clauses) or None,
        evidence_id=result.evidence_id,
        evidence_summary=result.evidence_summary or None,
        evidence_refs=[e.as_dict() for e in result.evidence_refs] or None,
        evidence_count=result.evidence_count,
        evidence_score=result.evidence_score,
        response_score=result.response_score,
        status=result.status.value,
        confidence=result.confidence,
        confidence_factors=dict(result.confidence_factors) or None,
        score=result.score,
        reasoning=result.reasoning,
        gaps=list(result.gaps) or None,
        recommendation=result.recommendation or None,
        assurance_level=result.assurance_level,
        # The platform never concludes operating effectiveness. Stated explicitly
        # so it is assertable in a query, not merely absent.
        asserts_operating_effectiveness=False,
        criticality=result.criticality,
        severity=result.severity.value,
        framework_metadata=dict(result.metadata) or None,
        actor=actor,
        evaluated_at=result.evaluated_at,
        is_current=True,
    )


def _finding_from_result(
    result: EvaluationResult,
    *,
    assessment_id: str,
    framework: Framework,
    row: ComplianceEvaluation,
) -> Finding:
    gaps = "; ".join(result.gaps) or result.reasoning
    return Finding(
        assessment_id=assessment_id,
        control_code=result.control_id,
        framework_code=framework.code,
        domain_code=result.domain_code or None,
        title=f"{result.control_id}: {_FINDING_TITLES[result.status]}",
        gap_description=gaps,
        recommendation=result.recommendation or None,
        severity=result.severity.value,
        status="open",
        evaluation_id=row.id,
    )


# ── the run ─────────────────────────────────────────────────────────────────

class ComplianceService:
    """Evaluate an assessment against a framework and persist the result."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def evaluate(
        self,
        assessment_id: str,
        framework_id: str,
        *,
        actor: str | None = None,
        control_codes: Sequence[str] | None = None,
        create_findings: bool = True,
    ) -> EvaluationRun:
        """Evaluate every in-scope control and persist one row per control.

        A control with no evidence and no answer still produces a row
        (INSUFFICIENT_EVIDENCE) — an evaluation must never silently skip a
        control, or a framework's coverage cannot be demonstrated.
        """
        from backend.api.repositories.assessment_repo import AssessmentRepository

        # The repository eager-loads responses/evidence/document_requests, which
        # gather_control_evidence needs: touching those relationships on a plain
        # `select(Assessment)` would raise MissingGreenlet under async SQLAlchemy.
        assessment = await AssessmentRepository(self.db).get_by_id(assessment_id)
        if assessment is None:
            raise ComplianceError(f"Assessment {assessment_id} not found.")

        framework, adapter = await load_evaluable_framework(self.db, framework_id)
        specs, id_to_code, code_to_domain = await load_controls_in_scope(
            self.db, assessment, framework
        )
        if control_codes is not None:
            wanted = set(control_codes)
            specs = [s for s in specs if s.control_id in wanted]

        gathered = gather_control_evidence(
            assessment,
            control_codes=[s.control_id for s in specs],
            control_id_to_code=id_to_code,
            control_domain=code_to_domain,
        )

        run_id = str(uuid.uuid4())
        results = [
            self._evaluate_one(adapter, spec, gathered.get(spec.control_id))
            for spec in specs
        ]

        # Only the newest run per control is `is_current`; earlier rows are kept
        # so what was previously claimed stays auditable.
        await self.db.execute(
            update(ComplianceEvaluation)
            .where(
                ComplianceEvaluation.assessment_id == assessment_id,
                ComplianceEvaluation.is_current.is_(True),
            )
            .values(is_current=False)
        )

        rows: list[ComplianceEvaluation] = []
        code_to_row_id = {code: row_id for row_id, code in id_to_code.items()}
        for result in results:
            row = _row_from_result(
                result, assessment_id=assessment_id, run_id=run_id,
                framework=framework, actor=actor,
                control_row_id=code_to_row_id.get(result.control_id),
            )
            self.db.add(row)
            rows.append(row)
        await self.db.flush()  # assign row.id before linking findings

        findings: list[Finding] = []
        if create_findings:
            for result, row in zip(results, rows):
                if result.status not in _FINDING_STATUSES:
                    continue
                finding = _finding_from_result(
                    result, assessment_id=assessment_id, framework=framework, row=row,
                )
                self.db.add(finding)
                findings.append(finding)

        await self._record_audit(assessment, framework, adapter, run_id, results, actor)

        return EvaluationRun(
            run_id=run_id,
            assessment_id=assessment_id,
            framework=adapter.key,
            assurance_level=adapter.spec.assurance.value,
            results=results,
            rows=rows,
            findings=findings,
        )

    def _evaluate_one(
        self,
        adapter: FrameworkAdapter,
        spec: ControlSpec,
        gathered: ControlEvidence | None,
    ) -> EvaluationResult:
        return evaluate_control(
            adapter=adapter,
            control=spec,
            evidence=gathered.evidence if gathered else (),
            response_value=gathered.response_value if gathered else None,
            response_score=gathered.response_score if gathered else None,
            metadata={
                "answer_notes": gathered.answer_notes if gathered else "",
                "evaluated_by": adapter.spec.assurance.value,
            },
        )

    async def _record_audit(
        self,
        assessment: Assessment,
        framework: Framework,
        adapter: FrameworkAdapter,
        run_id: str,
        results: Sequence[EvaluationResult],
        actor: str | None,
    ) -> None:
        from backend.api.services.activity_service import ActivityService

        counts: dict[str, int] = {}
        for result in results:
            counts[result.status.value] = counts.get(result.status.value, 0) + 1
        await ActivityService(self.db).record(
            action="compliance.evaluated",
            entity_type="assessment",
            entity_id=assessment.id,
            summary=(
                f"Evaluated {len(results)} control(s) against "
                f"{framework.name} at {adapter.spec.assurance.value} scope "
                f"({adapter.spec.pass_threshold:.0%} pass threshold). Statuses: "
                + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            ),
            actor_email=actor or assessment.assigned_to or assessment.created_by,
            actor_name=None,
            actor_role="assessor",
            changes={"run_id": run_id, "framework": adapter.key,
                     "assurance_level": adapter.spec.assurance.value},
            flush=True,
        )

    # ── read paths ──────────────────────────────────────────────────────────

    async def list_framework_profiles(self) -> list[dict[str, Any]]:
        """Every framework that can be evaluated, and the scope it may claim.

        Driven by the adapter registry, not the database: a framework that is
        not importable is not evaluable, and this is what the UI offers before
        an assessment exists.
        """
        from backend.api.compliance.adapters.registry import list_adapters

        return [
            {
                "key": adapter.key,
                "name": adapter.name,
                "version": getattr(adapter.spec, "version", None),
                "description": getattr(adapter.spec, "description", None),
                "assurance_level": adapter.spec.assurance.value,
                "pass_threshold": adapter.spec.pass_threshold,
                "asserts_operating_effectiveness": False,
                "assurance_statement": adapter.spec.assurance_statement,
                "statement_aliases": list(adapter.aliases),
            }
            for adapter in list_adapters()
        ]

    async def list_controls(
        self,
        framework_id: str,
        *,
        domain_code: str | None = None,
        search: str | None = None,
    ) -> tuple[Framework, list[ControlSpec]]:
        """The framework's controls as the evaluator will see them."""
        framework, adapter = await load_evaluable_framework(self.db, framework_id)
        rows = (await self.db.execute(
            select(Control, Domain).join(Domain, Control.domain_id == Domain.id)
            .where(Control.framework_id == framework.id)
            .order_by(Domain.order_index, Control.order_index)
        )).all()
        needle = (search or "").strip().lower()
        specs = [
            control_spec_from_row(adapter, control, domain.code, domain.name)
            for control, domain in rows
            if (not domain_code or domain.code == domain_code)
            and (
                not needle
                or needle in control.code.lower()
                or needle in (control.statement or "").lower()
            )
        ]
        return framework, specs

    async def list_evaluation_audit(
        self,
        assessment_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Evaluation events for one assessment, newest first.

        Read from the immutable activity log rather than from
        `compliance_evaluations`, so this is the same history an auditor sees in
        the activity log UI.
        """
        from backend.api.models.audit import AuditLog

        events = (await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.entity_type == "assessment",
                AuditLog.entity_id == assessment_id,
                AuditLog.action == "compliance.evaluated",
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )).scalars().all()
        return [
            {
                "id": e.id,
                "action": e.action,
                "summary": e.summary,
                "changes": e.changes,
                "user_email": e.user_email,
                "actor_name": e.actor_name,
                "user_role": e.user_role,
                "created_at": e.created_at,
            }
            for e in events
        ]

    async def list_evaluations(
        self,
        assessment_id: str,
        *,
        current_only: bool = True,
        framework: str | None = None,
        status: str | None = None,
    ) -> list[ComplianceEvaluation]:
        stmt = select(ComplianceEvaluation).where(
            ComplianceEvaluation.assessment_id == assessment_id
        )
        if current_only:
            stmt = stmt.where(ComplianceEvaluation.is_current.is_(True))
        if framework:
            stmt = stmt.where(ComplianceEvaluation.framework == framework)
        if status:
            stmt = stmt.where(ComplianceEvaluation.status == status)
        stmt = stmt.order_by(ComplianceEvaluation.domain_code, ComplianceEvaluation.control_code)
        return list((await self.db.execute(stmt)).scalars().all())

    async def summarise(
        self,
        assessment_id: str,
        *,
        current_only: bool = True,
    ) -> dict[str, Any]:
        rows = await self.list_evaluations(assessment_id, current_only=current_only)
        by_framework: dict[str, dict[str, Any]] = {}
        for row in rows:
            bucket = by_framework.setdefault(row.framework, {
                "framework": row.framework,
                "framework_code": row.framework_code,
                "assurance_level": row.assurance_level,
                "asserts_operating_effectiveness": row.asserts_operating_effectiveness,
                "total": 0,
                "counts": {s.value: 0 for s in ComplianceStatus},
                "_conf": 0.0,
            })
            bucket["total"] += 1
            bucket["counts"][row.status] += 1
            bucket["_conf"] += row.confidence

        out: list[dict[str, Any]] = []
        for bucket in by_framework.values():
            total = bucket.pop("total")
            conf = bucket.pop("_conf")
            conclusive = sum(
                bucket["counts"][s.value] for s in ComplianceStatus if s.is_conclusive
            )
            # `total` is kept in the output: a client needs the denominator to
            # render a coverage figure without a second request.
            bucket["total"] = total
            bucket["conclusive"] = conclusive
            bucket["conclusive_pct"] = round(100.0 * conclusive / total, 1) if total else 0.0
            bucket["mean_confidence"] = round(conf / total, 3) if total else 0.0
            out.append(bucket)
        return {"assessment_id": assessment_id, "frameworks": out, "total": len(rows)}


def result_from_row(row: ComplianceEvaluation, refs: Sequence[EvidenceRef]) -> EvaluationResult:
    """Rebuild an `EvaluationResult` from a stored evaluation row.

    The status, score and confidence come from the row rather than from a fresh
    evaluation, so a downstream layer (the semantic review, the assessment
    pipeline) comments on what was actually recorded at the time — a re-run
    against changed evidence must not silently rewrite the claim being reviewed.
    """
    from backend.api.compliance.enums import ComplianceStatus, Severity

    try:
        status = ComplianceStatus(row.status)
    except ValueError:
        status = ComplianceStatus.INSUFFICIENT_EVIDENCE

    return EvaluationResult(
        framework=row.framework,
        control_id=row.control_code,
        requirement=row.requirement or "",
        requirement_id=row.requirement_id or "",
        requirement_clauses=tuple(row.requirement_clauses or ()),
        status=status,
        confidence=float(row.confidence or 0.0),
        confidence_factors=dict(row.confidence_factors or {}),
        reasoning=row.reasoning or "",
        gaps=tuple(row.gaps or ()),
        recommendation=row.recommendation or "",
        evidence_id=row.evidence_id,
        evidence_summary=row.evidence_summary or "",
        evidence_refs=refs,
        evidence_count=row.evidence_count or 0,
        domain_code=row.domain_code or "",
        domain_name=row.domain_name or "",
        assurance_level=row.assurance_level or "POINT_IN_TIME",
        score=float(row.score or 0.0),
        response_score=float(row.response_score or 0.0),
        evidence_score=float(row.evidence_score or 0.0),
        criticality=row.criticality or "standard",
        severity=Severity(row.severity or "medium"),
        evaluated_at=row.evaluated_at,
        metadata=dict(row.framework_metadata or {}),
    )
