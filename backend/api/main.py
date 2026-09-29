import csv
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.config import settings
from backend.api.database import get_db, init_db
from backend.api.authz import (
    Caller, get_caller, require_reviewer, require_provider, require_organization_owner,
    CONTRIBUTOR_ROLES,
)
from backend.api.services.framework_service import FrameworkService
from backend.api.services.assessment_service import AssessmentService
from backend.api.services.ai_service import AiService
from backend.api.schemas.assessment import (
    AssessmentCreate, ResponseCreate, AssessmentAssign,
    GenerateQuestionnaireIn,
)
from backend.api.schemas.user import UserCreate, UserUpdate, UserOut
from backend.api.repositories.user_repo import UserRepository
from backend.api.routers import catalog as catalog_router
from backend.api.routers import activity as activity_router
from backend.api.routers import compliance as compliance_router
from backend.api.routers import hindsight as hindsight_router
import backend.api.models.user   # ensure User registers with Base metadata
import backend.api.models.audit  # ensure AuditLog registers with Base metadata
import backend.api.models.memory  # ensure MemoryRecord / ComplianceException register


# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────

DEFAULT_TEMPLATE_ROWS = [
    ("Organization & Scope", "What is the name and primary business of your organization?", "", ""),
    ("Organization & Scope", "What systems, applications, or data are in scope for this assessment?", "", ""),
    ("Organization & Scope", "How many employees does your organization have?", "", ""),
    ("Governance", "Do you have a documented Information Security Policy? (Yes/No/Partial)", "", ""),
    ("Governance", "Is there a designated CISO or security lead?", "", ""),
    ("Governance", "How often is the security policy reviewed?", "", ""),
    ("Risk Management", "Do you perform formal risk assessments? (Yes/No/Partial)", "", ""),
    ("Risk Management", "Describe your risk register or risk tracking process.", "", ""),
    ("Access Control", "How do you manage privileged access (admin accounts)?", "", ""),
    ("Access Control", "Is multi-factor authentication (MFA) enforced? (Yes/No/Partial)", "", ""),
    ("Access Control", "How is user access reviewed and de-provisioned?", "", ""),
    ("Asset Management", "Do you maintain an inventory of hardware and software assets? (Yes/No)", "", ""),
    ("Asset Management", "How are assets classified (e.g., critical, sensitive)?", "", ""),
    ("Incident Response", "Do you have a documented Incident Response Plan? (Yes/No)", "", ""),
    ("Incident Response", "When was the IR plan last tested?", "", ""),
    ("Incident Response", "How are security incidents logged and tracked?", "", ""),
    ("Data Protection", "How is sensitive data identified and classified?", "", ""),
    ("Data Protection", "Is data encrypted at rest and in transit? (Yes/No/Partial)", "", ""),
    ("Data Protection", "Describe your backup and recovery procedures.", "", ""),
    ("Vulnerability Management", "Do you perform regular vulnerability scans? (Yes/No/Partial)", "", ""),
    ("Vulnerability Management", "How are critical vulnerabilities prioritized and remediated?", "", ""),
    ("Third-Party Risk", "Do you assess the security posture of key vendors/suppliers? (Yes/No)", "", ""),
    ("Third-Party Risk", "Are third-party agreements reviewed for security requirements?", "", ""),
    ("Training & Awareness", "Do employees receive annual security awareness training? (Yes/No)", "", ""),
    ("Training & Awareness", "How are phishing simulations or exercises conducted?", "", ""),
    ("Compliance", "What regulatory frameworks apply to your organization (e.g., HIPAA, PCI-DSS, SOC 2)?", "", ""),
    ("Compliance", "Are there any outstanding audit findings or regulatory actions?", "", ""),
    ("Additional Notes", "Please describe any recent security incidents or breaches.", "", ""),
    ("Additional Notes", "List any current security projects or initiatives in progress.", "", ""),
]


def _ensure_default_template(path: str) -> None:
    if os.path.exists(path):
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Section", "Question", "Response", "Evidence / Notes"])
        w.writerows(DEFAULT_TEMPLATE_ROWS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    for folder in [
        settings.RAW_FOLDER,
        settings.CANONICAL_FOLDER,
        settings.CHUNK_FOLDER,
        settings.EVIDENCE_FOLDER,
        settings.PRE_ASSESSMENT_FOLDER,
        settings.TEMPLATES_FOLDER,
    ]:
        os.makedirs(folder, exist_ok=True)
    _ensure_default_template(os.path.join(settings.TEMPLATES_FOLDER, "default_pre_assessment.csv"))
    yield


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────

app = FastAPI(title="CyberAI", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # Also allow the Next.js dev server when opened via localhost or a LAN IP on
    # any port (e.g. http://192.168.1.20:3000), so the frontend works off-machine.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────

def _splice(router: APIRouter) -> None:
    """Attach a sub-router's routes to the app the way `include_router` would.

    The direct `app.router.routes.extend(...)` below is a workaround for this
    env's broken `include_router`, but it has a side effect worth repairing:
    `include_router` is also what sets each route's `dependency_overrides_provider`
    to the app. Splicing leaves it None, which makes `app.dependency_overrides`
    silently do nothing for these routes — so nothing (including a test) can
    substitute a different database session. Restoring the provider is what
    include_router would have done, and is a no-op in production where no
    overrides are registered.

    The handler has to be rebuilt as well as the attribute set: `APIRoute.__init__`
    compiles `route.app` once via `get_route_handler()`, and that closure captured
    the old (None) provider, so assigning the attribute alone would have no effect.
    """
    from starlette.routing import request_response

    for route in router.routes:
        if getattr(route, "dependency_overrides_provider", None) is None:
            route.dependency_overrides_provider = app
            route.app = request_response(route.get_route_handler())
    app.router.routes.extend(router.routes)


# Admin-only framework catalog management (Knowledge Base → Manage). Ported from
# the knowledge_base FMS; DB-backed, gated to central_admin. See routers/catalog.py.
# NOTE: this env's pinned FastAPI/Starlette has a broken `include_router` (it drops
# the sub-router's routes), so we splice the already-prefixed routes in directly —
# which is exactly what the rest of main.py does via inline @app decorators.
_splice(catalog_router.router)

# Immutable activity log (Admin → Activity Log). Read-only by construction: the
# router defines GET routes only. Spliced in the same way, for the same reason.
_splice(activity_router.router)

# Deterministic, evidence-gated compliance evaluation. Read routes plus one POST
# (assessor-gated). Spliced in the same way, for the same reason.
_splice(compliance_router.router)

# Hindsight: persistent organisational memory. Classifies recorded evaluations
# against stored history, records human decisions and exceptions, and serves
# control history, similar/recurring findings and a risk summary. All routes
# assessor-gated. Spliced in the same way, for the same reason.
_splice(hindsight_router.router)


@app.get("/")
def health():
    return {"status": "ok", "service": "CyberAI Backend"}


# ─────────────────────────────────────────────
# FRAMEWORKS
# ─────────────────────────────────────────────

@app.get("/frameworks")
async def list_frameworks(db: AsyncSession = Depends(get_db)):
    svc = FrameworkService(db)
    frameworks = await svc.list_frameworks()
    return [
        {
            "id": f.id,
            "code": f.code,
            "name": f.name,
            "version": f.version,
            "status": f.status,
            "total_domains": f.total_domains,
            "total_categories": f.total_categories or 0,
            "total_controls": f.total_controls,
            "total_questions": f.total_questions,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in frameworks
    ]


@app.get("/frameworks/{framework_id}")
async def get_framework(framework_id: str, db: AsyncSession = Depends(get_db)):
    svc = FrameworkService(db)
    fw = await svc.get_framework(framework_id)
    if not fw:
        raise HTTPException(status_code=404, detail="Framework not found")
    return {
        "id": fw.id,
        "code": fw.code,
        "name": fw.name,
        "version": fw.version,
        "description": fw.description,
        "status": fw.status,
        "total_domains": fw.total_domains,
        "total_controls": fw.total_controls,
        "total_questions": fw.total_questions,
        "created_at": fw.created_at.isoformat() if fw.created_at else None,
        "domains": [
            {
                "id": d.id,
                "code": d.code,
                "name": d.name,
                "description": d.description,
                # framework → domain → category (sub-domain) → control → question
                "categories": [
                    {
                        "id": cat.id,
                        "code": cat.code,
                        "name": cat.name,
                        "criteria_statement": cat.criteria_statement,
                        "description": cat.description,
                        "controls": [_serialize_control(c) for c in cat.controls],
                    }
                    for cat in d.categories
                ],
                # Flat list of every control in the domain (back-compat).
                "controls": [_serialize_control(c) for c in d.controls],
            }
            for d in fw.domains
        ],
    }


def _serialize_control(c) -> dict:
    return {
        "id": c.id,
        "code": c.code,
        "name": c.name,
        "statement": c.statement,
        "category_code": c.category_code,
        "category_name": c.category_name,
        "weight": c.weight,
        "criticality": c.criticality,
        "questions": [
            {
                "id": q.id,
                "text": q.text,
                "question_type": q.question_type,
                "choices": q.choices,
                "weight": q.weight,
                "maturity_level": q.maturity_level,
                "expected_evidence_types": q.expected_evidence_types,
                "order_index": q.order_index,
            }
            for q in c.questions
        ],
    }


@app.post("/frameworks/upload")
async def upload_framework(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    svc = FrameworkService(db)
    result = await svc.process_upload(file.filename, content)
    return {"message": "Framework processed successfully", **result}


@app.post("/frameworks/import-json")
async def import_framework_json(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    data = json.loads(content)
    svc = FrameworkService(db)
    result = await svc.import_json(data)
    return {"message": "Framework imported successfully", **result}


@app.post("/frameworks/load-existing")
async def load_existing_frameworks(db: AsyncSession = Depends(get_db)):
    """Bootstrap: import all canonical JSON files from data/canonical/ into the DB."""
    svc = FrameworkService(db)
    results = await svc.load_existing_canonical_files()
    failures = getattr(svc, "last_load_failures", [])
    return {
        "imported": len(results),
        "frameworks": results,
        "failed": len(failures),
        "failures": failures,
    }


@app.delete("/frameworks/{framework_id}")
async def delete_framework(framework_id: str, db: AsyncSession = Depends(get_db)):
    svc = FrameworkService(db)
    deleted = await svc.delete_framework(framework_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Framework not found")
    return {"message": "Deleted"}


# ─────────────────────────────────────────────
# LEGACY UPLOAD (kept for backward compat)
# ─────────────────────────────────────────────

@app.post("/upload-framework")
async def upload_framework_legacy(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    svc = FrameworkService(db)
    result = await svc.process_upload(file.filename, content)
    return {
        "message": "Framework processed successfully",
        "framework": result["framework_name"],
        "domains": result["domains"],
        "chunks_generated": result.get("controls", 0),
    }


# ─────────────────────────────────────────────
# ASSESSMENTS
# ─────────────────────────────────────────────

@app.get("/assessments")
async def list_assessments(
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(get_caller),
):
    # Owners are contributors, not reviewers: they only see engagements they are
    # actually involved in. Every other role (and unidentified callers) is
    # unchanged. Enforced server-side — the previous client-side filter meant the
    # API handed every assessment to anyone who asked.
    svc = AssessmentService(db)
    assessments = await svc.list_for_caller(caller)
    return [
        {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "framework_ids": a.framework_ids,
            "selected_domains": a.selected_domains or [],
            "status": a.status,
            "organization": a.organization,
            "assigned_to": a.assigned_to,
            "overall_score": a.overall_score,
            "maturity_level": a.maturity_level,
            "pre_assessment_file_name": a.pre_assessment_file_name,
            "market_id": a.market_id,
            "market_label": a.market_label,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }
        for a in assessments
    ]


@app.post("/assessments")
async def create_assessment(
    data: AssessmentCreate,
    db: AsyncSession = Depends(get_db),
):
    svc = AssessmentService(db)
    assessment = await svc.create_assessment(data)
    return {
        "id": assessment.id,
        "name": assessment.name,
        "status": assessment.status,
        "framework_ids": assessment.framework_ids or [],
        "selected_domains": assessment.selected_domains or [],
        "assigned_to": assessment.assigned_to,
        "organization": assessment.organization,
        "overall_score": assessment.overall_score,
        "maturity_level": assessment.maturity_level,
        "market_id": assessment.market_id,
        "market_label": assessment.market_label,
        "created_at": assessment.created_at.isoformat(),
    }


@app.post("/assessments/{assessment_id}/assign")
async def assign_assessment(
    assessment_id: str,
    data: AssessmentAssign,
    db: AsyncSession = Depends(get_db),
):
    """Assign (or unassign, when assigned_to is null) an assessment to an Assessor."""
    svc = AssessmentService(db)
    try:
        a = await svc.assign_assessment(assessment_id, data.assigned_to)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": a.id, "assigned_to": a.assigned_to}


@app.post("/assessments/{assessment_id}/pre-assessment")
async def upload_pre_assessment(
    assessment_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a custom Pre-Assessment questionnaire (JSON / CSV / TXT / DOCX / PDF).
    Its questions become the assessment's 'Pre-Assessment' section. When no file is
    uploaded the standard default questionnaire is used automatically."""
    svc = AssessmentService(db)
    content = await file.read()
    try:
        res = await svc.load_pre_assessment(assessment_id, file.filename or "upload", content)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not res.get("ok"):
        raise HTTPException(status_code=422, detail=res.get("error") or "Could not parse file")
    return res


@app.get("/pre-assessment/template")
async def download_default_pre_assessment_template():
    """Download the default pre-assessment questionnaire CSV template."""
    path = os.path.join(settings.TEMPLATES_FOLDER, "default_pre_assessment.csv")
    _ensure_default_template(path)
    return FileResponse(path, filename="pre_assessment_template.csv", media_type="text/csv")


@app.get("/assessments/{assessment_id}/pre-assessment/download")
async def download_pre_assessment(assessment_id: str, db: AsyncSession = Depends(get_db)):
    """Download the pre-assessment questionnaire for this assessment.
    Returns the custom file if one was uploaded, otherwise the default template."""
    svc = AssessmentService(db)
    assessment = await svc.get_assessment(assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if assessment.pre_assessment_file_path and os.path.exists(assessment.pre_assessment_file_path):
        return FileResponse(assessment.pre_assessment_file_path, filename=assessment.pre_assessment_file_name)
    path = os.path.join(settings.TEMPLATES_FOLDER, "default_pre_assessment.csv")
    _ensure_default_template(path)
    return FileResponse(path, filename="pre_assessment_template.csv", media_type="text/csv")


@app.delete("/assessments/{assessment_id}")
async def delete_assessment(assessment_id: str, db: AsyncSession = Depends(get_db)):
    svc = AssessmentService(db)
    deleted = await svc.delete_assessment(assessment_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return {"message": "Deleted"}


@app.get("/assessments/{assessment_id}")
async def get_assessment(
    assessment_id: str,
    db: AsyncSession = Depends(get_db),
):
    svc = AssessmentService(db)
    assessment = await svc.get_assessment(assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    followups_src = assessment.followups
    return {
        "id": assessment.id,
        "name": assessment.name,
        "description": assessment.description,
        "framework_ids": assessment.framework_ids,
        "selected_domains": assessment.selected_domains or [],
        "status": assessment.status,
        "organization": assessment.organization,
        "assigned_to": assessment.assigned_to,
        "overall_score": assessment.overall_score,
        "maturity_level": assessment.maturity_level,
        "pre_assessment_file_name": assessment.pre_assessment_file_name,
        "created_at": assessment.created_at.isoformat() if assessment.created_at else None,
        "completed_at": assessment.completed_at.isoformat() if assessment.completed_at else None,
        "responses": [
            {
                "id": r.id,
                "question_id": r.question_id,
                "control_id": r.control_id,
                "response_value": r.response_value,
                "notes": r.notes,
                "score": r.score,
                "answered_at": r.answered_at.isoformat() if r.answered_at else None,
                "evidence": [
                    {"id": e.id, "file_name": e.file_name, "uploaded_at": e.uploaded_at.isoformat()}
                    for e in r.evidence
                ],
            }
            for r in assessment.responses
        ],
        "findings": [
            {
                "id": f.id,
                "control_code": f.control_code,
                "title": f.title,
                "severity": f.severity,
                "status": f.status,
                "recommendation": f.recommendation,
            }
            for f in assessment.findings
        ],
        "scores": [
            {
                "level": s.level,
                "domain_code": s.domain_code,
                "percentage": s.percentage,
                "maturity_level": s.maturity_level,
                "answered_questions": s.answered_questions,
            }
            for s in assessment.scores
        ],
        # AI-generated follow-up questions (appended at the end of their category
        # in the Conduct tab).
        "followups": [
            {
                "id": f.id,
                "framework_code": f.framework_code,
                "domain_code": f.domain_code,
                "domain_name": f.domain_name,
                "category_code": f.category_code,
                "category_name": f.category_name,
                "control_code": f.control_code,
                "text": f.text,
                "question_type": f.question_type,
                "order_index": f.order_index,
                "source": getattr(f, "source", "ai"),
            }
            for f in sorted(followups_src, key=lambda x: (x.domain_code or "", x.category_code or "", x.order_index))
        ],
    }


@app.post("/assessments/{assessment_id}/generate-questionnaire")
async def generate_questionnaire(
    assessment_id: str,
    data: GenerateQuestionnaireIn | None = None,
    db: AsyncSession = Depends(get_db),
):
    svc = AssessmentService(db)
    override = (
        data.generation_config.model_dump()
        if data and data.generation_config else None
    )
    try:
        q = await svc.generate_questionnaire(assessment_id, override)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "id": q.id,
        "assessment_id": q.assessment_id,
        "total_questions": q.total_questions,
        "answered_count": q.answered_count,
    }


@app.post("/assessments/{assessment_id}/responses")
async def save_response(
    assessment_id: str,
    data: ResponseCreate,
    db: AsyncSession = Depends(get_db),
):
    svc = AssessmentService(db)
    resp = await svc.save_response(assessment_id, data)
    return {
        "id": resp.id,
        "question_id": resp.question_id,
        "score": resp.score,
        "answered_at": resp.answered_at.isoformat(),
    }


@app.post("/assessments/{assessment_id}/evidence")
async def upload_evidence(
    assessment_id: str,
    response_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    svc = AssessmentService(db)
    ev = await svc.upload_evidence(assessment_id, response_id, file.filename, content)
    return {"id": ev.id, "file_name": ev.file_name, "uploaded_at": ev.uploaded_at.isoformat()}


def block_contributor(caller: Caller = Depends(get_caller)) -> Caller:
    """Reject contributors outright.

    Contributors (team members / evidence contributors) provide evidence; they
    never see evaluation output. Scope checks are not enough here — a
    contributor involved in an assessment must still never read its score,
    findings or report. Callers with no X-User-Role header stay permissive,
    matching authz.py.
    """
    if caller.role in CONTRIBUTOR_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Contributors cannot access scores, findings, reports or AI results",
        )
    return caller


@app.post("/assessments/{assessment_id}/score")
async def score_assessment(
    assessment_id: str,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(block_contributor),
):
    svc = AssessmentService(db)
    try:
        result = await svc.score_and_finalize(assessment_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.post("/assessments/{assessment_id}/submit")
async def submit_assessment(assessment_id: str, db: AsyncSession = Depends(get_db)):
    """Submit the assessment for review (for roles that answer but don't score)."""
    svc = AssessmentService(db)
    try:
        return await svc.submit_assessment(assessment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/assessments/{assessment_id}/findings")
async def get_findings(assessment_id: str, db: AsyncSession = Depends(get_db),
                       _: Caller = Depends(block_contributor)):
    from backend.api.repositories.assessment_repo import AssessmentRepository
    repo = AssessmentRepository(db)
    findings = await repo.get_findings(assessment_id)
    return [
        {
            "id": f.id,
            "control_code": f.control_code,
            "framework_code": f.framework_code,
            "domain_code": f.domain_code,
            "title": f.title,
            "gap_description": f.gap_description,
            "recommendation": f.recommendation,
            "severity": f.severity,
            "status": f.status,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in findings
    ]


@app.post("/assessments/{assessment_id}/prefill")
async def prefill_answers(
    assessment_id: str,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(block_contributor),
):
    """Draft answers from uploaded evidence using the root LLM prefill pipeline."""
    svc = AssessmentService(db)
    try:
        return await svc.prefill_answers(assessment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/assessments/{assessment_id}/report")
async def assessment_report(assessment_id: str, db: AsyncSession = Depends(get_db),
                            _: Caller = Depends(block_contributor)):
    """Structured report built from the scored assessment results."""
    svc = AssessmentService(db)
    try:
        return await svc.build_report(assessment_id)
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/assessments/{assessment_id}/report-view")
async def assessment_report_view(assessment_id: str, db: AsyncSession = Depends(get_db),
                                 _: Caller = Depends(block_contributor)):
    """cyber_prac-style report dashboard data for the React Reports view."""
    svc = AssessmentService(db)
    try:
        return await svc.build_report_dashboard(assessment_id)
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/assessments/{assessment_id}/evidence-requirements")
async def evidence_requirements(assessment_id: str, db: AsyncSession = Depends(get_db)):
    """Evidence required for the in-scope controls + what's already uploaded."""
    svc = AssessmentService(db)
    try:
        return await svc.evidence_requirements(assessment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/assessments/{assessment_id}/evidence-bulk")
async def upload_evidence_bulk(
    assessment_id: str,
    files: list[UploadFile] = File(...),
    doc_type: str = Form("evidence"),
    db: AsyncSession = Depends(get_db),
):
    """Upload multiple engagement documents for the whole assessment, tagged with a
    bucket (survey | transcript | evidence | policy). Feeds pre-fill and rating."""
    payload = [(f.filename, await f.read()) for f in files]
    svc = AssessmentService(db)
    try:
        return await svc.upload_evidence_bulk(assessment_id, payload, doc_type=doc_type)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/assessments/{assessment_id}/engagement-documents")
async def engagement_documents(
    assessment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Engagement documents grouped by bucket + expected evidence per category."""
    svc = AssessmentService(db)
    try:
        return await svc.list_engagement_documents(assessment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/assessments/{assessment_id}/ai/rate-controls")
async def ai_rate_controls(
    assessment_id: str,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(block_contributor),
):
    """Rate uploaded engagement documents per category against its criteria and
    generate follow-up questions per category."""
    svc = AssessmentService(db)
    try:
        return await svc.rate_and_generate(assessment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/assessments/{assessment_id}/ai/results")
async def ai_results(
    assessment_id: str,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(block_contributor),
):
    """Persisted category ratings + AI follow-up questions."""
    svc = AssessmentService(db)
    try:
        return await svc.list_ai_results(assessment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/assessments/{assessment_id}/evidence-bulk")
async def delete_evidence_bulk(
    assessment_id: str,
    file_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single uploaded evidence file (and its cached text) from the assessment."""
    svc = AssessmentService(db)
    deleted = await svc.delete_evidence_file(assessment_id, file_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return {"message": "Deleted", "file_name": file_name}


# ─────────────────────────────────────────────
# DOCUMENT REQUESTS (assessor → owner → assessor)
# ─────────────────────────────────────────────

@app.get("/document-requests")
async def all_document_requests(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(get_caller),
):
    """Document requests across every assessment - the contributor's inbox feed."""
    svc = AssessmentService(db)
    return await svc.list_document_requests_all(status=status, caller=caller)


@app.get("/assessments/{assessment_id}/document-requests")
async def list_document_requests(
    assessment_id: str,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(get_caller),
):
    svc = AssessmentService(db)
    # Closes the direct-URL hole: without this a contributor could read another
    # organization's requests by guessing the assessment id.
    if not await svc.caller_can_access(caller, assessment_id):
        raise HTTPException(status_code=403, detail="Not assigned to this assessment")
    try:
        return await svc.list_document_requests(assessment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/assessments/{assessment_id}/document-requests")
async def create_document_requests(
    assessment_id: str, body: dict,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(get_caller),
):
    """Assessor sends document requests to the owner. Body:
    {items: [{evidence_type, domain_code?, domain_name?, control_code?, note?}],
     requested_by?, note?}"""
    svc = AssessmentService(db)
    if not await svc.caller_can_access(caller, assessment_id):
        raise HTTPException(status_code=403, detail="Not assigned to this assessment")
    items = body.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="items is required")
    try:
        created = await svc.create_document_requests(
            assessment_id, items,
            requested_by=body.get("requested_by"), note=body.get("note"),
        )
        return {"ok": True, "created": created, "count": len(created)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/assessments/{assessment_id}/document-requests/{request_id}/provide")
async def provide_documents(
    assessment_id: str,
    request_id: str,
    files: list[UploadFile] = File(...),
    provided_by: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_provider),
):
    """Owner uploads the requested documents; they are auto-linked to the request
    and stored as engagement evidence for the assessor."""
    svc = AssessmentService(db)
    if not await svc.caller_can_access(caller, assessment_id):
        raise HTTPException(status_code=403, detail="Not assigned to this assessment")
    payload = [(f.filename, await f.read()) for f in files]
    try:
        return await svc.provide_documents(
            assessment_id, request_id, payload,
            provided_by=caller.email or provided_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/assessments/{assessment_id}/document-requests/{request_id}/prowler")
async def generate_prowler_report(
    assessment_id: str,
    request_id: str,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_provider),
):
    """Owner generates a (simulated) Prowler scan report for a request and gets it
    back for preview. Nothing is submitted yet — the report is staged read-only."""
    svc = AssessmentService(db)
    if not await svc.caller_can_access(caller, assessment_id):
        raise HTTPException(status_code=403, detail="Not assigned to this assessment")
    try:
        return await svc.generate_prowler_report(assessment_id, request_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/assessments/{assessment_id}/document-requests/{request_id}/prowler/submit")
async def submit_prowler_report(
    assessment_id: str,
    request_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_provider),
):
    """Owner submits a previously generated Prowler report. Body:
    {report_id}. The report is linked to the request as immutable
    engagement evidence and the request moves to 'provided'.
    provided_by is taken from the verified caller identity, not the body."""
    report_id = (body or {}).get("report_id")
    if not report_id:
        raise HTTPException(status_code=400, detail="report_id is required")
    svc = AssessmentService(db)
    if not await svc.caller_can_access(caller, assessment_id):
        raise HTTPException(status_code=403, detail="Not assigned to this assessment")
    try:
        return await svc.submit_prowler_report(
            assessment_id, request_id, report_id,
            provided_by=caller.email,
        )
    except ValueError as e:
        code = 404 if "not found" in str(e) else 400
        raise HTTPException(status_code=code, detail=str(e))


@app.post("/assessments/{assessment_id}/document-requests/{request_id}/review")
async def review_document_request(
    assessment_id: str, request_id: str, body: dict,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    """Assessor accepts or rejects the provided files. Body: {action, note?}"""
    svc = AssessmentService(db)
    if not await svc.caller_can_access(caller, assessment_id):
        raise HTTPException(status_code=403, detail="Not assigned to this assessment")
    try:
        return await svc.review_document_request(
            assessment_id, request_id,
            action=body.get("action", ""), note=body.get("note"),
        )
    except ValueError as e:
        code = 404 if "not found" in str(e) else 400
        raise HTTPException(status_code=code, detail=str(e))


@app.delete("/assessments/{assessment_id}/document-requests/{request_id}")
async def delete_document_request(
    assessment_id: str, request_id: str,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(require_reviewer),
):
    svc = AssessmentService(db)
    if not await svc.caller_can_access(caller, assessment_id):
        raise HTTPException(status_code=403, detail="Not assigned to this assessment")
    try:
        await svc.delete_document_request(assessment_id, request_id)
        return {"message": "Deleted", "id": request_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ─────────────────────────────────────────────
# AI / ASK CYBERAI
# ─────────────────────────────────────────────

@app.post("/ai/chat")
async def ai_chat(body: dict, db: AsyncSession = Depends(get_db)):
    message = body.get("message", "")
    history = body.get("history", [])
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    svc = AiService(db)
    result = await svc.chat(message, history)
    return result


# ─────────────────────────────────────────────
# USER MANAGEMENT
# User directory CRUD is administration: it is gated to the Organization Owner
# (org_owner), like the catalog router below.
# ─────────────────────────────────────────────

@app.get("/users")
async def list_users(role: str | None = None, db: AsyncSession = Depends(get_db),
                     _: Caller = Depends(require_organization_owner)):
    repo = UserRepository(db)
    users = await repo.get_all(role=role)
    return [UserOut.model_validate(u) for u in users]


@app.get("/users/by-role/{role}")
async def get_users_by_role(role: str, db: AsyncSession = Depends(get_db),
                            _: Caller = Depends(require_organization_owner)):
    repo = UserRepository(db)
    users = await repo.get_all(role=role)
    return [UserOut.model_validate(u) for u in users]


@app.get("/users/{email}")
async def get_user(email: str, db: AsyncSession = Depends(get_db),
                   _: Caller = Depends(require_organization_owner)):
    repo = UserRepository(db)
    user = await repo.get_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut.model_validate(user)


@app.post("/users", status_code=201)
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db),
                      _: Caller = Depends(require_organization_owner)):
    repo = UserRepository(db)
    try:
        user = await repo.create(data)
        return UserOut.model_validate(user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.patch("/users/{email}")
async def update_user(email: str, data: UserUpdate, db: AsyncSession = Depends(get_db),
                      _: Caller = Depends(require_organization_owner)):
    repo = UserRepository(db)
    try:
        user = await repo.update(email, data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut.model_validate(user)


@app.delete("/users/{email}", status_code=204)
async def delete_user(email: str, db: AsyncSession = Depends(get_db),
                      _: Caller = Depends(require_organization_owner)):
    repo = UserRepository(db)
    ok = await repo.delete(email)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")


@app.get("/users-stats")
async def user_stats(db: AsyncSession = Depends(get_db),
                     _: Caller = Depends(require_organization_owner)):
    repo = UserRepository(db)
    counts = await repo.count_by_role()
    total  = sum(counts.values())
    return {"total": total, "by_role": counts}

