"""Integration test: ComplianceService against a real database.

Covers the orchestration that the pure evaluation tests cannot: control loading
from the DB, evidence gathering, persistence, finding back-links, the audit
trail, and supersession on re-run.

Runs on its own async engine against a throwaway SQLite file, so it cannot touch
a developer's database and does not depend on import order. Coroutines are
driven with `asyncio.run`, so no pytest plugin configuration is needed.

Run with:  python -m pytest tests/test_compliance_service.py -q
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.api.models  # noqa: F401  (register every table on Base.metadata)
from backend.api.compliance.enums import ComplianceStatus
from backend.api.compliance.service import (
    ComplianceError,
    ComplianceService,
    FrameworkNotEvaluable,
)
from backend.api.database import Base
from backend.api.models.assessment import Assessment, Evidence, Finding, Response
from backend.api.models.audit import AuditLog
from backend.api.models.compliance import ComplianceEvaluation
from backend.api.models.framework import Control, Framework
from backend.api.services.framework_service import FrameworkService

CANONICAL = Path(__file__).resolve().parents[1] / "backend" / "api" / "data" / "canonical"
SOC2_FILE = CANONICAL / "soc2_tsc_2017.json"
NIST_FILE = CANONICAL / "nist_csf_2_0.json"

DESIGN_EVIDENCE = (
    "Walkthrough performed 2026-02-01. Current-state architecture diagram and "
    "code of conduct with approval record and revision history; effective date "
    "2025-11-01. The entity demonstrates a commitment to integrity and ethical "
    "values."
)
PERIOD_EVIDENCE = (
    "Sample of 25 user access tickets drawn from the population of 4,200 "
    "tickets throughout the period, with exception report showing 3 exceptions."
)


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def _db(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            yield db
    finally:
        await engine.dispose()


def _attach(db, assessment, control, text, name=None, value="yes", score=100.0):
    """Attach a response plus one evidence file with a preview sidecar."""
    evidence_dir = evidence_root(assessment.id)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    name = name or f"{control.code}.txt"
    file_path = evidence_dir / name
    (evidence_dir / f"{name}.preview.txt").write_text(text, encoding="utf-8")

    response = Response(
        assessment_id=assessment.id,
        question_id=f"q-{control.code}",
        control_id=control.id,
        response_value=value,
        score=score,
    )
    db.add(response)
    return response, Evidence(
        response_id="",  # set after flush
        assessment_id=assessment.id,
        file_name=name,
        file_path=str(file_path),
    )


_EVIDENCE_ROOT: Path | None = None


def evidence_root(assessment_id: str) -> Path:
    assert _EVIDENCE_ROOT is not None, "evidence_root() must be set by the fixture"
    return _EVIDENCE_ROOT / assessment_id


async def _seed(db, dataset: Path, tmp_path: Path, *, attach: dict[str, str] | None = None):
    """Import a framework, create an assessment, attach evidence to some controls."""
    global _EVIDENCE_ROOT
    _EVIDENCE_ROOT = tmp_path / "evidence"

    result = await FrameworkService(db).import_json(
        json.loads(dataset.read_text(encoding="utf-8"))
    )
    await db.flush()
    assessment = Assessment(
        name="Acme Assurance", framework_ids=[result["framework_id"]], organization="Acme"
    )
    db.add(assessment)
    await db.flush()

    for code, text in (attach or {}).items():
        control = (await db.execute(
            select(Control).where(Control.framework_id == result["framework_id"], Control.code == code)
        )).scalar_one()
        response, evidence = _attach(db, assessment, control, text)
        await db.flush()
        evidence.response_id = response.id
        db.add(evidence)
    await db.commit()
    return assessment, result["framework_id"]


def test_evaluation_persists_one_row_per_control(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            run_ = await ComplianceService(db).evaluate(assessment.id, fw_id, actor="a@b.c")
            await db.commit()
            return run_, fw_id

    run_, _ = run(body())
    assert len(run_.results) == 61
    assert len(run_.rows) == 61
    assert run_.summary["assurance_level"] == "TYPE_1"
    # Nothing was attached, so every control is honestly inconclusive.
    assert run_.summary["counts"]["INSUFFICIENT_EVIDENCE"] == 61
    assert run_.summary["conclusive"] == 0


def test_every_control_is_evaluated_even_with_nothing_recorded(tmp_path):
    """A control with no answer and no evidence must still produce a row."""
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(db, NIST_FILE, tmp_path)
            result = await ComplianceService(db).evaluate(assessment.id, fw_id)
            await db.commit()
            stored = (await db.execute(
                select(func.count()).select_from(ComplianceEvaluation)
            )).scalar()
            return result, stored

    result, stored = run(body())
    assert len(result.results) == 106
    assert stored == 106
    assert all(r.status is ComplianceStatus.INSUFFICIENT_EVIDENCE for r in result.results)


def test_evidence_reaches_the_evaluation(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC1.1": DESIGN_EVIDENCE}
            )
            result = await ComplianceService(db).evaluate(assessment.id, fw_id)
            await db.commit()
            cc11 = next(r for r in result.results if r.control_id == "CC1.1")
            return result, cc11

    result, cc11 = run(body())
    assert cc11.evidence_count == 1
    assert cc11.status is ComplianceStatus.PASS, cc11.reasoning
    assert result.summary["counts"]["PASS"] == 1
    assert result.summary["counts"]["INSUFFICIENT_EVIDENCE"] == 60


def test_period_only_evidence_cannot_pass_a_type_1_evaluation(tmp_path):
    """The Type 1 guarantee has to survive the whole DB round trip."""
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC6.1": PERIOD_EVIDENCE}
            )
            result = await ComplianceService(db).evaluate(assessment.id, fw_id)
            await db.commit()
            cc61 = next(r for r in result.results if r.control_id == "CC6.1")
            row = (await db.execute(
                select(ComplianceEvaluation).where(
                    ComplianceEvaluation.assessment_id == assessment.id,
                    ComplianceEvaluation.control_code == "CC6.1",
                )
            )).scalar_one()
            return cc61, row

    cc61, row = run(body())
    assert cc61.status is not ComplianceStatus.PASS
    assert any("Type 2" in g for g in cc61.gaps)
    assert row.assurance_level == "TYPE_1"
    assert row.asserts_operating_effectiveness is False


def test_findings_are_raised_and_linked_for_non_conclusive_controls(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            result = await ComplianceService(db).evaluate(assessment.id, fw_id)
            await db.commit()
            findings = (await db.execute(
                select(Finding).where(Finding.assessment_id == assessment.id)
            )).scalars().all()
            return result, findings

    result, findings = run(body())
    assert len(findings) == 61
    assert all(f.evaluation_id for f in findings)
    # NOT_APPLICABLE would raise nothing, so every row here is a real gap.
    assert all("Insufficient evidence" in f.title for f in findings)
    assert {f.evaluation_id for f in findings} == {r.id for r in result.rows}


def test_passing_control_raises_no_finding(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC1.1": DESIGN_EVIDENCE}
            )
            await ComplianceService(db).evaluate(assessment.id, fw_id)
            await db.commit()
            findings = (await db.execute(
                select(Finding).where(
                    Finding.assessment_id == assessment.id,
                    Finding.control_code == "CC1.1",
                )
            )).scalars().all()
            return findings

    assert run(body()) == []


def test_audit_trail_records_the_run(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            result = await ComplianceService(db).evaluate(assessment.id, fw_id, actor="auditor@x.io")
            await db.commit()
            entries = (await db.execute(
                select(AuditLog).where(AuditLog.action == "compliance.evaluated")
            )).scalars().all()
            return result, entries

    result, entries = run(body())
    assert len(entries) == 1
    assert entries[0].user_email == "auditor@x.io"
    assert (entries[0].changes or {}).get("run_id") == result.run_id


def test_rerun_supersedes_but_retains_history(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            first = await ComplianceService(db).evaluate(assessment.id, fw_id)
            await db.commit()
            second = await ComplianceService(db).evaluate(assessment.id, fw_id)
            await db.commit()
            total = (await db.execute(
                select(func.count()).select_from(ComplianceEvaluation)
            )).scalar()
            current = (await db.execute(
                select(func.count()).select_from(ComplianceEvaluation)
                .where(ComplianceEvaluation.is_current.is_(True))
            )).scalar()
            return first, second, total, current

    first, second, total, current = run(body())
    assert first.run_id != second.run_id
    assert total == 122, "previous claims are retained for audit"
    assert current == 61, "only the newest run per control is current"


def test_list_and_summary_read_paths(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC1.1": DESIGN_EVIDENCE}
            )
            await ComplianceService(db).evaluate(assessment.id, fw_id)
            await db.commit()
            service = ComplianceService(db)
            return await service.list_evaluations(assessment.id), await service.summarise(assessment.id)

    rows, summary = run(body())
    assert len(rows) == 61
    assert summary["total"] == 61
    bucket = summary["frameworks"][0]
    assert bucket["framework"] == "soc2-type-1"
    assert bucket["asserts_operating_effectiveness"] is False
    assert bucket["counts"]["PASS"] == 1
    assert bucket["conclusive"] == 1


def test_selected_domains_scope_the_evaluation(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            framework = (await db.execute(
                select(Framework).where(Framework.id == fw_id)
            )).scalar_one()
            domain = framework.domains[0]
            assessment.selected_domains = [f"{framework.id}:{domain.code}"]
            await db.commit()
            result = await ComplianceService(db).evaluate(assessment.id, fw_id)
            return result, domain.code

    result, domain_code = run(body())
    assert 0 < len(result.results) < 61
    assert {r.domain_code for r in result.results} == {domain_code}


def test_unsupported_framework_is_rejected_with_a_clear_error(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            result = await FrameworkService(db).import_json({
                "code": "SOME_OTHER_FRAMEWORK",
                "name": "Some Other Framework",
                "version": "1.0",
                "domains": [{"code": "D1", "name": "Domain 1", "categories": [{
                    "code": "C1", "name": "Cat 1",
                    "controls": [{"code": "X.1", "statement": "Do a thing."}],
                }]}],
            })
            await db.commit()
            assessment = Assessment(name="A", framework_ids=[result["framework_id"]])
            db.add(assessment)
            await db.commit()
            try:
                await ComplianceService(db).evaluate(assessment.id, result["framework_id"])
            except FrameworkNotEvaluable as exc:
                return str(exc)
            return None

    message = run(body())
    assert message is not None
    assert "no compliance evaluation profile" in message
    assert "nist-csf-2-0" in message and "soc2-type-1" in message


def test_missing_assessment_raises(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            try:
                await ComplianceService(db).evaluate("does-not-exist", fw_id)
            except ComplianceError as exc:
                return str(exc)
            return None

    assert "not found" in (run(body()) or "")


def test_control_subset_can_be_evaluated(tmp_path):
    async def body():
        async with _db(tmp_path) as db:
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC1.1": DESIGN_EVIDENCE}
            )
            result = await ComplianceService(db).evaluate(
                assessment.id, fw_id, control_codes=["CC1.1", "CC1.2"]
            )
            return result

    result = run(body())
    assert {r.control_id for r in result.results} == {"CC1.1", "CC1.2"}
    assert next(r for r in result.results if r.control_id == "CC1.1").status is ComplianceStatus.PASS
