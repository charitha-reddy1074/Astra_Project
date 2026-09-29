"""Compliance evaluation routes.

Read routes are open to any identified user; the POSTs that create evaluations
are gated to reviewers, matching `authz.require_reviewer`.

Access follows the authz.py convention used by the rest of the app: a caller with
no `X-User-Role` header (SSR, tests, curl) stays permissive, and a caller that
*is* identified must hold the required role. Every assessment-scoped route also
verifies the caller may access that assessment (`AssessmentService.caller_can_access`),
so a contributor cannot read or write another engagement's evaluation data even
with a guessed assessment id.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.authz import Caller, get_caller, require_reviewer
from backend.api.compliance.dataset import DATASET_FORMAT
from backend.api.compliance.service import (
    ComplianceError,
    ComplianceService,
    EvaluationRun,
    FrameworkNotEvaluable,
    result_from_row,
)
from backend.api.compliance.types import ControlSpec, EvaluationResult, EvidenceRef
from backend.api.database import get_db
from backend.api.schemas.compliance import (
    ComplianceEvaluationOut,
    ComplianceSummaryOut,
    ControlListOut,
    ControlSpecOut,
    EvaluationAuditListOut,
    EvaluationListOut,
    EvaluationRequest,
    EvaluationRunOut,
    FindingOut,
    FrameworkListOut,
    FrameworkProfileOut,
    AssessmentPipelineOut,
    AssessmentPipelineRequest,
    AssessmentReportOut,
    SemanticRequest,
    SemanticReviewOut,
)

router = APIRouter(tags=["compliance"])


def _run_payload(run: EvaluationRun) -> EvaluationRunOut:
    payload = run.summary
    return EvaluationRunOut(
        run_id=payload["run_id"],
        assessment_id=payload["assessment_id"],
        framework=payload["framework"],
        assurance_level=payload["assurance_level"],
        asserts_operating_effectiveness=payload["asserts_operating_effectiveness"],
        total=payload["total"],
        counts=payload["counts"],
        conclusive=payload["conclusive"],
        conclusive_pct=payload["conclusive_pct"],
        mean_confidence=payload["mean_confidence"],
        evaluations=[ComplianceEvaluationOut.model_validate(r) for r in run.rows],
        findings_raised=len(run.findings),
        findings=[FindingOut.model_validate(f) for f in run.findings],
    )


async def _ensure_assessment_access(
    db: AsyncSession, caller: Caller, assessment_id: str, what: str
) -> None:
    """Reject callers who are identified but not allowed near an assessment.

    Contributors (team members / evidence contributors) may only touch
    assessments they are involved in. Combined with `require_reviewer` on the
    write routes this means: unidentified callers and reviewers stay permissive
    (matching authz.py), contributors are scoped to their own engagements, and
    the Organization Owner can always proceed.
    """
    from backend.api.services.assessment_service import AssessmentService

    if not await AssessmentService(db).caller_can_access(caller, assessment_id):
        raise HTTPException(
            status_code=403,
            detail=f"Not assigned to this assessment ({what}).",
        )


@router.get("/compliance/frameworks", response_model=FrameworkListOut)
async def list_compliance_frameworks(
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(get_caller),
):
    """Frameworks that can be evaluated, and the dataset format a new one follows.

    Lists what is *evaluable* (the adapter registry), not what is imported, so a
    client cannot offer an evaluation it has no profile to back.
    """
    profiles = await ComplianceService(db).list_framework_profiles()
    return FrameworkListOut(
        supported=[FrameworkProfileOut(**p) for p in profiles],
        dataset_format=DATASET_FORMAT,
    )


@router.get("/compliance/frameworks/{framework_id}/controls", response_model=ControlListOut)
async def list_compliance_controls(
    framework_id: str,
    domain_code: str | None = Query(default=None, description="Filter to one domain"),
    search: str | None = Query(default=None, description="Match code or statement text"),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(get_caller),
):
    """A framework's controls, resolved exactly as an evaluation will resolve them.

    Useful for a scoping UI: it shows the requirement the evaluator uses, not the
    raw dataset wording, so what a client previews is what gets assessed.
    """
    service = ComplianceService(db)
    try:
        framework, specs = await service.list_controls(
            framework_id, domain_code=domain_code, search=search
        )
    except FrameworkNotEvaluable as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ComplianceError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # The assurance level is a property of the adapter, and must come from it
    # rather than being inferred from the data: it is the thing a client must not
    # get wrong.
    from backend.api.compliance.adapters.registry import get_adapter

    adapter = get_adapter(framework.code, framework.name)
    return ControlListOut(
        framework=adapter.key if adapter else "",
        framework_code=framework.code,
        assurance_level=adapter.spec.assurance.value if adapter else "POINT_IN_TIME",
        total=len(specs),
        controls=[ControlSpecOut.model_validate(s) for s in specs],
    )


@router.post("/assessments/{assessment_id}/evaluations", response_model=EvaluationRunOut)
async def evaluate_assessment(
    assessment_id: str,
    payload: EvaluationRequest,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    """Run a deterministic compliance evaluation and persist every result.

    The response includes the assurance level of each claim. A SOC 2 result is
    always `TYPE_1` and never asserts operating effectiveness.
    """
    await _ensure_assessment_access(db, caller, assessment_id, what="evaluation")
    try:
        run = await ComplianceService(db).evaluate(
            assessment_id,
            payload.framework_id,
            actor=caller.email,
            control_codes=payload.control_codes,
            create_findings=payload.create_findings,
        )
        await db.commit()
    except FrameworkNotEvaluable as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except ComplianceError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    return _run_payload(run)


@router.get("/assessments/{assessment_id}/evaluations", response_model=EvaluationListOut)
async def list_evaluations(
    assessment_id: str,
    current_only: bool = Query(default=True, description="Only the newest run per control"),
    framework: str | None = Query(default=None, description="Adapter key filter"),
    status: str | None = Query(default=None, description="One status, e.g. FAIL"),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(get_caller),
):
    rows = await ComplianceService(db).list_evaluations(
        assessment_id, current_only=current_only, framework=framework, status=status
    )
    await _ensure_assessment_access(db, caller, assessment_id, what="evaluation")
    return EvaluationListOut(
        assessment_id=assessment_id,
        total=len(rows),
        evaluations=[ComplianceEvaluationOut.model_validate(r) for r in rows],
    )


@router.get("/assessments/{assessment_id}/evaluations/summary", response_model=ComplianceSummaryOut)
async def summarise_evaluations(
    assessment_id: str,
    current_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(get_caller),
):
    await _ensure_assessment_access(db, caller, assessment_id, what="summary")
    return ComplianceSummaryOut.model_validate(
        await ComplianceService(db).summarise(assessment_id, current_only=current_only)
    )


@router.get("/assessments/{assessment_id}/evaluation-audit", response_model=EvaluationAuditListOut)
async def evaluation_audit(
    assessment_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(get_caller),
):
    await _ensure_assessment_access(db, caller, assessment_id, what="audit")
    events = await ComplianceService(db).list_evaluation_audit(assessment_id, limit=limit)
    return EvaluationAuditListOut(assessment_id=assessment_id, total=len(events), events=events)


@router.post(
    "/assessments/{assessment_id}/semantic-review",
    response_model=SemanticReviewOut,
)
async def semantic_review(
    assessment_id: str,
    payload: SemanticRequest,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    """Run the LLM review over already-recorded evaluations. Advisory.

    Nothing here is written. The deterministic evaluation rows are the
    platform's claim and are left exactly as they are; this endpoint reads them,
    re-gathers the evidence that was recorded, and returns the model's reading
    beside the recorded status. A client that wants the reading stored must do
    so itself, which keeps the persisted record of what was claimed under the
    control of whoever ran the evaluation.

    A control with no readable evidence is never sent to the model, so a review
    over a large framework costs nothing for the controls that could not be
    checked anyway.
    """
    from backend.api.compliance.adapters.registry import get_adapter
    from backend.api.compliance.evidence import gather_control_evidence
    from backend.api.compliance.semantic import ControlRetriever, SemanticEvaluator, SemanticService
    from backend.api.compliance.service import (
        load_controls_in_scope,
        load_evaluable_framework,
    )
    from backend.api.repositories.assessment_repo import AssessmentRepository

    try:
        framework, _adapter = await load_evaluable_framework(db, payload.framework_id)
    except FrameworkNotEvaluable as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ComplianceError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    adapter = get_adapter(framework.code, framework.name)
    if adapter is None:  # pragma: no cover - load_evaluable_framework guards this
        raise HTTPException(
            status_code=422,
            detail=f"Framework '{framework.name}' has no semantic review profile.",
        )

    rows = await ComplianceService(db).list_evaluations(
        assessment_id, current_only=payload.run_id is None, framework=adapter.key,
    )
    if payload.run_id:
        rows = [r for r in rows if r.run_id == payload.run_id]
    if payload.control_codes:
        wanted = {c.strip().upper() for c in payload.control_codes}
        rows = [r for r in rows if (r.control_code or "").strip().upper() in wanted]

    # Rebuild the evaluable controls so retrieval is scoped to what this
    # assessment actually covers, and cannot widen it.
    # The repository eager-loads responses/evidence/document_requests, which
    # `gather_control_evidence` needs: touching those relationships on a plain
    # `select(Assessment)` raises MissingGreenlet under async SQLAlchemy.
    assessment = await AssessmentRepository(db).get_by_id(assessment_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail=f"Assessment {assessment_id} not found.")
    await _ensure_assessment_access(db, caller, assessment_id, what="semantic review")

    specs, id_to_code, code_to_domain = await load_controls_in_scope(db, assessment, framework)
    specs_by_code = {s.control_id.strip().upper(): s for s in specs}
    allowed_codes = {c.strip().upper() for c in payload.control_codes} if payload.control_codes else None

    scoped_codes = [
        (r.control_code or "").strip().upper() for r in rows
        if (r.control_code or "").strip().upper() in specs_by_code
        and (allowed_codes is None or (r.control_code or "").strip().upper() in allowed_codes)
    ]
    if not scoped_codes:
        return SemanticReviewOut(
            assessment_id=assessment_id, framework=adapter.key,
            requested=len(rows), reviewed=0, model_invocations=0, cache_hits=0,
            rejected=0, results=[],
        )

    evidence_by_code = gather_control_evidence(
        assessment,
        control_codes=scoped_codes,
        control_id_to_code=id_to_code,
        control_domain=code_to_domain,
    )

    rows_by_code = {(r.control_code or "").strip().upper(): r for r in rows}
    results: list[EvaluationResult] = []
    evidence_map: dict[str, list[EvidenceRef]] = {}
    controls_by_code: dict[str, ControlSpec] = {}
    for code in dict.fromkeys(scoped_codes):
        row = rows_by_code.get(code)
        entry = evidence_by_code.get(code)
        if row is None or entry is None:
            continue
        refs = tuple(entry.evidence)
        evidence_map[row.control_code] = list(refs)
        results.append(result_from_row(row, refs))
        controls_by_code[row.control_code] = specs_by_code[code]

    evaluator = SemanticEvaluator()
    service = SemanticService(
        evaluator=evaluator,
        retriever=ControlRetriever(specs),
        top_k=payload.top_k,
    )
    semantic = service.evaluate_run(
        results=results,
        controls_by_id=controls_by_code,
        evidence_by_control=evidence_map,
    )

    warnings: list[str] = []
    if service.last_assurance_warning:
        warnings.append(service.last_assurance_warning)
    untrusted = [s for s in semantic if s.assessment is None]
    if semantic and len(untrusted) == len(semantic):
        # Every control fell back. Naming the shared reason is more useful than
        # making a caller read 60 identical fallback strings to find out whether
        # this is a missing key, a provider outage or a scope problem.
        reasons = sorted({s.fallback_reason for s in untrusted if s.fallback_reason})
        detail = "; ".join(reasons[:3])
        warnings.append(
            "No model's reading was accepted for any reviewed control "
            f"({detail}). The deterministic results are unchanged."
        )
    elif untrusted:
        warnings.append(
            f"{len(untrusted)} of {len(semantic)} controls had their model's "
            "reading rejected as untrustworthy; the deterministic results are "
            "unchanged."
        )

    return SemanticReviewOut(
        assessment_id=assessment_id,
        framework=adapter.key,
        run_id=payload.run_id,
        asserts_operating_effectiveness=False,
        requested=len(rows),
        reviewed=len(semantic),
        model_invocations=sum(1 for s in semantic if s.model_invoked and not s.cache_hit),
        cache_hits=sum(1 for s in semantic if s.cache_hit),
        rejected=sum(1 for s in semantic if s.assessment is None and s.model_invoked),
        results=semantic,
        warnings=warnings,
    )


@router.post(
    "/assessments/{assessment_id}/assessment-pipeline",
    response_model=AssessmentPipelineOut,
)
async def run_assessment_pipeline(
    assessment_id: str,
    payload: AssessmentPipelineRequest,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    """The combined assessment path: evaluate, normalise, review, persist.

    Runs the deterministic engine, then normalises the evidence it judged,
    applies the semantic (Groq) review and the Hindsight memory review, and
    persists their records — so the report a dashboard renders afterwards is the
    recorded reading, not a re-computed one. Everything here is advisory except
    the deterministic evaluation rows, which are exactly what the existing
    `POST /evaluations` creates.

    When `framework_id` is omitted every framework the assessment has selected
    is pipelined in one call (a combined NIST + SOC 2 assessment runs in one
    request).
    """
    await _ensure_assessment_access(db, caller, assessment_id, what="pipeline")
    from backend.api.compliance.pipeline import (
        AssessmentPipelineError,
        AssessmentPipelineService,
    )

    service = AssessmentPipelineService(db, actor=caller.email, llm_client=None)
    try:
        report = await service.run(
            assessment_id,
            payload.framework_id,
            control_codes=payload.control_codes,
            create_findings=payload.create_findings,
            include_memory_review=payload.include_memory_review,
            top_k=payload.top_k,
        )
        await db.commit()
    except AssessmentPipelineError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except ComplianceError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:  # noqa: BLE001  - never leave the session mid-transaction
        await db.rollback()
        raise
    return AssessmentPipelineOut.model_validate(report)


@router.get(
    "/assessments/{assessment_id}/compliance-report",
    response_model=AssessmentReportOut,
)
async def compliance_report(
    assessment_id: str,
    framework_id: str | None = Query(
        default=None, description="Restrict the report to one framework."
    ),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(get_caller),
):
    """The read-only combined report: recorded readings, no model calls.

    Renders whatever has been recorded: evaluations alone (pre-pipeline), or
    evaluations plus the semantic and memory reviews a pipeline run persisted.
    Calling this endpoint never costs a model invocation, so a dashboard can
    refresh freely.
    """
    await _ensure_assessment_access(db, caller, assessment_id, what="report")
    from backend.api.compliance.pipeline import AssessmentPipelineService

    service = AssessmentPipelineService(db, actor=caller.email)
    try:
        report = await service.report(assessment_id, framework_id=framework_id)
    except ComplianceError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return AssessmentReportOut.model_validate(report)
