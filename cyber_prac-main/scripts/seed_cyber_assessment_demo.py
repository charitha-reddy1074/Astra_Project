"""Seed a deterministic, interconnected Cyber Assessment Agent demo.

Run from the project root:
    python scripts/seed_cyber_assessment_demo.py --reset

The script deliberately uses the installed framework catalog and the real
ComplianceService. It does not call Groq, Hindsight, or any external API.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

# Make `python scripts/seed_cyber_assessment_demo.py` work without requiring
# callers to set PYTHONPATH first.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import backend.api.models  # noqa: F401
from backend.api.compliance.service import ComplianceService
from backend.api.database import AsyncSessionLocal, init_db
from backend.api.models.assessment import (
    Assessment, CategoryRating, DocumentRequest, Evidence, Finding,
    FollowupQuestion, Questionnaire, Response, Score,
)
from backend.api.models.audit import AuditLog
from backend.api.models.compliance import ComplianceEvaluation, EvidenceControlMapping, SemanticReviewRecord
from backend.api.models.framework import Control, Framework
from backend.api.models.memory import ComplianceException, MemoryRecord
from backend.api.models.user import User


ORG = "NovaStack Technologies"
OWNER = "alex.morgan@novastack.example"
ASSESSMENT_NAMES = {
    "nist": "NovaStack 2026 NIST CSF 2.0 Readiness Assessment",
    "soc2": "NovaStack 2026 SOC 2 Type 1 Readiness Assessment",
}
SEED_ROOT = PROJECT_ROOT / "data" / "evidence" / "novastack_demo"
NOW = datetime(2026, 9, 15, 10, 0, 0)

USERS = (
    ("Alex Morgan", OWNER, "org_owner", "Compliance", "Chief Compliance Officer"),
    ("Jordan Lee", "jordan.lee@novastack.example", "compliance_manager", "Security Assurance", "Compliance Manager"),
    ("Maya Patel", "maya.patel@novastack.example", "security_manager", "Security Engineering", "Director of Security"),
    ("Ethan Brooks", "ethan.brooks@novastack.example", "team_member", "IT Operations", "IAM Platform Owner"),
    ("Sofia Reyes", "sofia.reyes@novastack.example", "auditor", "Internal Audit", "Senior Internal Auditor"),
)

DOCUMENTS = (
    ("Access Control Policy.pdf", "policy", "Privileged access requires MFA, quarterly review, and approval by a system owner."),
    ("Joiner Mover Leaver Procedure.docx", "procedure", "HR tickets trigger account creation, role changes, and termination within one business day."),
    ("Security Awareness Standard.pdf", "policy", "All workforce members complete security awareness training annually; completion is tracked in LearnHub."),
    ("Incident Response Plan.pdf", "policy", "Security incidents are triaged by the SOC, escalated by severity, and reviewed after closure."),
    ("Vulnerability Management Standard.pdf", "policy", "Critical vulnerabilities are remediated within 15 days and tracked to closure."),
    ("AWS MFA Configuration Export.json", "configuration", "MFA is enforced for the cloud root account and privileged production roles."),
    ("Quarterly Access Review Q2 2026.xlsx", "records", "Review evidence covers 92 percent of privileged accounts; three legacy service accounts remain open."),
    ("Incident Tabletop Report Q1 2026.pdf", "report", "The tabletop exercise recorded owners, escalation times, and two follow-up actions."),
    ("Vendor Risk Register.xlsx", "records", "Critical vendors are risk-rated annually; two new SaaS vendors await completed reviews."),
    ("Backup Restore Test March 2026.pdf", "report", "A restore test recovered the customer database within the stated recovery objective."),
)


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha1(value.encode()).hexdigest()[:30]}"


def framework_matches(framework: Framework, kind: str) -> bool:
    haystack = f"{framework.code} {framework.name}".lower()
    return ("nist" in haystack and "csf" in haystack) if kind == "nist" else "soc" in haystack and "2" in haystack


async def framework_for(db, kind: str) -> Framework:
    frameworks = (await db.execute(select(Framework))).scalars().all()
    match = next((item for item in frameworks if framework_matches(item, kind)), None)
    if not match:
        raise RuntimeError(f"Installed framework catalog has no {kind} framework")
    return match


async def reset_demo(db) -> None:
    assessments = (await db.execute(select(Assessment).where(Assessment.organization == ORG))).scalars().all()
    assessment_ids = [item.id for item in assessments]
    assessment_ids.extend(stable_id("assessment", kind) for kind in ASSESSMENT_NAMES)
    assessment_ids = list(set(assessment_ids))
    if assessment_ids:
        for model in (
            SemanticReviewRecord, ComplianceEvaluation, EvidenceControlMapping,
            CategoryRating, DocumentRequest, FollowupQuestion, Finding, Score,
            Evidence, Response, Questionnaire,
        ):
            await db.execute(delete(model).where(model.assessment_id.in_(assessment_ids)))
        await db.execute(delete(Assessment).where(Assessment.id.in_(assessment_ids)))
    await db.execute(delete(User).where(User.email.like("%@novastack.example")))
    await db.execute(delete(ComplianceException).where(ComplianceException.organization_id == ORG))
    await db.execute(delete(MemoryRecord).where(MemoryRecord.organization_id == ORG))
    await db.execute(delete(AuditLog).where(AuditLog.market == ORG))
    if SEED_ROOT.exists():
        shutil.rmtree(SEED_ROOT)


async def seed_users(db) -> dict[str, User]:
    users = {}
    for name, email, role, department, title in USERS:
        user = User(
            id=stable_id("user", email), name=name, email=email, role=role,
            organization=ORG, department=department, job_title=title,
            notes=f"NovaStack demo identity; owns the {department} workflow.",
            phone="+1-555-0100",
        )
        db.add(user)
        users[email] = user
    await db.flush()
    return users


async def make_assessment(db, framework: Framework, kind: str) -> Assessment:
    assessment = Assessment(
        id=stable_id("assessment", kind), name=ASSESSMENT_NAMES[kind],
        description=("Point-in-time readiness review for NovaStack's customer-facing "
                     "platform and corporate security programme."),
        framework_ids=[framework.id], organization=ORG, created_by=OWNER,
        assigned_to="jordan.lee@novastack.example", status="in_progress",
        market_id="novastack", market_label=ORG,
        generation_config={"strategy": "framework_only", "evidence_policy": "strict"},
    )
    db.add(assessment)
    await db.flush()
    controls = (await db.execute(
        select(Control).options(selectinload(Control.questions), selectinload(Control.domain)).where(
            Control.framework_id == framework.id
        ).order_by(Control.order_index).limit(20)
    )).scalars().all()
    question_ids = [control.questions[0].id for control in controls if control.questions]
    db.add(Questionnaire(
        id=stable_id("questionnaire", kind), assessment_id=assessment.id,
        question_ids=question_ids, total_questions=len(question_ids), answered_count=len(question_ids),
    ))
    for control in controls:
        if not control.questions:
            continue
        question = control.questions[0]
        db.add(Response(
            id=stable_id("response", f"{kind}:{control.code}"), assessment_id=assessment.id,
            question_id=question.id, control_id=control.id,
            response_value="PARTIAL" if kind == "nist" and control == controls[0] else "YES",
            notes="Seeded from NovaStack's 2026 readiness workshop.", score=50.0 if kind == "nist" and control == controls[0] else 100.0,
        ))
    await db.flush()
    return assessment


async def add_evidence(db, assessments: dict[str, Assessment], controls: dict[str, list[Control]]) -> list[EvidenceControlMapping]:
    SEED_ROOT.mkdir(parents=True, exist_ok=True)
    mappings = []
    for index, (filename, evidence_kind, text) in enumerate(DOCUMENTS):
        path = SEED_ROOT / filename
        content = f"{filename}\nNovaStack Technologies\nApproved 2026-01-15\n\n{text}\n"
        path.write_text(content, encoding="utf-8")
        # The compliance evidence reader consumes the standard preview sidecar
        # used by uploaded documents, so seeded files follow that same path.
        path.with_name(f"{path.name}.preview.txt").write_text(content, encoding="utf-8")
        kind = "nist" if index < 7 else "soc2"
        assessment = assessments[kind]
        target = controls[kind][index % min(8, len(controls[kind]))]
        response = Response(
            id=stable_id("doc-response", filename), assessment_id=assessment.id,
            question_id=f"document:{index}", control_id=target.id,
            response_value="evidence attached", notes=text, score=75.0,
        )
        db.add(response)
        await db.flush()
        evidence = Evidence(
            id=stable_id("evidence", filename), response_id=response.id, assessment_id=assessment.id,
            file_name=filename, file_path=str(path), file_type=evidence_kind,
            file_size=path.stat().st_size, description=text, uploaded_at=NOW,
        )
        db.add(evidence)
        await db.flush()
        cross_control = controls["soc2"][index % min(8, len(controls["soc2"]))] if kind == "nist" else controls["nist"][index % min(8, len(controls["nist"]))]
        for framework_kind, control in ((kind, target), ("soc2" if kind == "nist" else "nist", cross_control)):
            other_assessment = assessments[framework_kind]
            framework = (await db.execute(select(Framework).where(Framework.id == control.framework_id))).scalar_one()
            mappings.append(EvidenceControlMapping(
                id=stable_id("mapping", f"{filename}:{framework_kind}"), assessment_id=other_assessment.id,
                run_id=stable_id("run", framework_kind), evidence_id=evidence.id,
                evidence_source_name=filename, evidence_kind=evidence_kind,
                evidence_nature="POLICY_DESIGN" if evidence_kind == "policy" else "TECHNICAL_IMPLEMENTATION",
                evidence_format=path.suffix.lstrip(".").upper(), framework="nist-csf-2-0" if framework_kind == "nist" else "soc2-type-1",
                framework_code=framework.code, control_id=control.id, control_code=control.code,
                domain_code=control.domain.code if control.domain else None, domain_name=control.domain.name if control.domain else None,
                relevance_score=0.96 if framework_kind == kind else 0.78,
                matched_sections=[control.code], mapping_reason="Seeded document intelligence candidate; shared evidence demonstrates cross-framework reuse.",
                mapping_method="requested" if framework_kind == kind else "lexical",
                review_status="confirmed" if framework_kind == kind else "pending_review",
                requires_review=framework_kind != kind, reviewed_by=OWNER if framework_kind == kind else None,
                reviewed_at=NOW if framework_kind == kind else None,
            ))
    db.add_all(mappings)
    await db.flush()
    return mappings


async def seed_history(db, assessments: dict[str, Assessment], evaluations: list[ComplianceEvaluation]) -> None:
    nist_evals = [item for item in evaluations if item.framework == "nist-csf-2-0"]
    for index, evaluation in enumerate(nist_evals[:6]):
        classification = "RECURRING_FINDING" if index < 3 else "NEW_FINDING"
        db.add(MemoryRecord(
            id=stable_id("memory", evaluation.control_code), organization_id=ORG,
            assessment_id=assessments["nist"].id, evaluation_id=evaluation.id,
            framework=evaluation.framework, framework_code=evaluation.framework_code,
            control_id=evaluation.control_code, domain_code=evaluation.domain_code,
            domain_name=evaluation.domain_name, finding_type="evidence_gap",
            event="finding_raised", classification=classification, decision="open",
            occurrence=2 if classification == "RECURRING_FINDING" else 1,
            severity=evaluation.severity, status="open", remediation_status="in_progress",
            remediation_actions=["Assign control owner", "Upload implementation evidence", "Re-run evaluation"],
            actor=OWNER, meta={"source": "prior 2025 assessment", "demo": True}, created_at=NOW - timedelta(days=180),
        ))
    db.add(MemoryRecord(
        id=stable_id("memory", "mfa-remediated"), organization_id=ORG,
        assessment_id=assessments["nist"].id, framework="nist-csf-2-0", framework_code="NIST_CSF_2.0",
        control_id=nist_evals[0].control_code if nist_evals else "GV.OC-01", finding_type="remediation",
        event="remediation_completed", classification="RESOLVED_RECURRING_FINDING", decision="remediated",
        occurrence=2, severity="medium", status="remediated", remediation_status="remediated",
        remediation_actions=["MFA rollout completed for privileged roles", "Evidence retained in IAM export"],
        actor="maya.patel@novastack.example", meta={"completed_at": "2026-07-30", "demo": True}, created_at=NOW - timedelta(days=45),
    ))
    db.add(ComplianceException(
        id=stable_id("exception", "legacy-service-accounts"), organization_id=ORG,
        assessment_id=assessments["nist"].id, framework="nist-csf-2-0", framework_code="NIST_CSF_2.0",
        control_id=nist_evals[0].control_code if nist_evals else "PR.AA-03",
        reason="Legacy service accounts require a planned migration window.", creator=OWNER,
        created_by=OWNER, created_at=NOW - timedelta(days=240), expires_at=NOW - timedelta(days=30),
        status="expired", severity="medium", meta={"demo": True},
    ))


async def validate(db, assessments: dict[str, Assessment]) -> dict[str, int]:
    ids = [assessment.id for assessment in assessments.values()]
    counts = {"users": len((await db.execute(select(User).where(User.organization == ORG))).scalars().all())}
    counts["assessments"] = len((await db.execute(select(Assessment).where(Assessment.organization == ORG))).scalars().all())
    counts["evidence"] = len((await db.execute(select(Evidence).where(Evidence.assessment_id.in_(ids)))).scalars().all())
    counts["mappings"] = len((await db.execute(select(EvidenceControlMapping).where(EvidenceControlMapping.assessment_id.in_(ids)))).scalars().all())
    counts["evaluations"] = len((await db.execute(select(ComplianceEvaluation).where(ComplianceEvaluation.assessment_id.in_(ids)))).scalars().all())
    counts["findings"] = len((await db.execute(select(Finding).where(Finding.assessment_id.in_(ids)))).scalars().all())
    counts["memory"] = len((await db.execute(select(MemoryRecord).where(MemoryRecord.organization_id == ORG))).scalars().all())
    assert counts["users"] == len(USERS)
    assert counts["assessments"] == 2
    assert counts["evidence"] == len(DOCUMENTS)
    assert counts["mappings"] == len(DOCUMENTS) * 2
    assert counts["evaluations"] > 0
    assert counts["findings"] > 0
    assert counts["memory"] >= 7
    return counts


async def main(reset: bool) -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        if reset:
            await reset_demo(db)
            await db.commit()
        else:
            existing = await db.scalar(select(Assessment.id).where(Assessment.organization == ORG))
            if existing:
                raise SystemExit("NovaStack demo already exists; rerun with --reset for a clean seed.")
        users = await seed_users(db)
        frameworks = {kind: await framework_for(db, kind) for kind in ("nist", "soc2")}
        assessments = {kind: await make_assessment(db, frameworks[kind], kind) for kind in frameworks}
        controls = {
            kind: (await db.execute(
                select(Control).options(selectinload(Control.questions), selectinload(Control.domain)).where(
                    Control.framework_id == frameworks[kind].id
                ).order_by(Control.order_index).limit(20)
            )).scalars().all()
            for kind in frameworks
        }
        await add_evidence(db, assessments, controls)
        await db.commit()

        evaluations = []
        for kind, assessment in assessments.items():
            result = await ComplianceService(db).evaluate(assessment.id, frameworks[kind].id, actor=OWNER)
            evaluations.extend(result.rows)
            assessment.status = "in_review"
        await seed_history(db, assessments, evaluations)
        for kind, assessment in assessments.items():
            db.add(AuditLog(
                id=stable_id("audit", kind), entity_type="assessment", entity_id=assessment.id,
                action="assessment.seeded", user_email=OWNER, user_role="org_owner",
                actor_name=users[OWNER].name, market=ORG,
                summary=f"Seeded {ASSESSMENT_NAMES[kind]} with evidence, mappings, evaluation, findings, and history.",
                changes={"demo": True, "framework": frameworks[kind].code}, created_at=NOW,
            ))
        await db.commit()
        counts = await validate(db, assessments)
        print(f"Seeded {ORG}")
        print(f"Frameworks: {frameworks['nist'].name} ({frameworks['nist'].code}); {frameworks['soc2'].name} ({frameworks['soc2'].code})")
        print(f"Assessments: {ASSESSMENT_NAMES['nist']}; {ASSESSMENT_NAMES['soc2']}")
        print("Records:", ", ".join(f"{key}={value}" for key, value in counts.items()))
        print(f"Owner identity: {OWNER}")
        print("Scenario: MFA policy evidence is reused across NIST and SOC 2; legacy service-account evidence remains a recurring, actionable gap.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="replace the NovaStack demo rows and artifacts")
    args = parser.parse_args()
    asyncio.run(main(args.reset))