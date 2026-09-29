"""HTTP + service tests for the Part 4 assessment pipeline.

Covers the write path (`POST /assessments/{id}/assessment-pipeline`: evaluate +
normalise + semantic review + Hindsight memory, persisted) and the read path
(`GET /assessments/{id}/compliance-report`: recorded readings only, no model
calls), plus the pure normalisation helpers they build on.

The model is stubbed for the semantic layer exactly as the semantic and memory
suites do; the memory classifier is deterministic and on a first run has no
history to enrich against, so it needs no stub.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.api.models  # noqa: F401  (register every table on Base.metadata)
from backend.api.compliance.normalization import (
    budget_evidence,
    content_hash,
    dedupe_by_hash,
    detect_format,
    evidence_chunks,
    quality_from_score,
)
from backend.api.compliance.types import EvidenceRef
from backend.api.database import Base, get_db
from backend.api.main import app
from backend.api.models.assessment import Assessment, Evidence, Response
from backend.api.models.compliance import SemanticReviewRecord
from backend.api.models.framework import Control
from backend.api.services.framework_service import FrameworkService

CANONICAL = Path(__file__).resolve().parents[1] / "backend" / "api" / "data" / "canonical"
SOC2_FILE = CANONICAL / "soc2_tsc_2017.json"
NIST_FILE = CANONICAL / "nist_csf_2_0.json"

NIST_COUNT = 106
SOC2_COUNT = 61

#: Strong, operational artefact: PASS at point-in-time for a design-level control.
DESIGN_EVIDENCE = (
    "Walkthrough performed 2026-02-01. Current-state architecture diagram and "
    "code of conduct with approval record and revision history; effective date "
    "2025-11-01. The entity demonstrates a commitment to integrity and ethical values."
)
#: Declaratory only — a template/policy *about* the control, never evidence of
#: practice; the rubric grades it policy-only (40), below the evidence floor.
POLICY_ONLY = (
    "THIS IS A TEMPLATE. [Organization name] will establish an acceptable use "
    "policy over access to systems, define an approval standard, and expect all "
    "personnel to comply."
)


def run(coro):
    return asyncio.run(coro)


class StubLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def invoke(self, system_prompt, user_prompt=None, max_tokens: int = 4096) -> str:
        self.calls.append(user_prompt or "")
        return self.response if isinstance(self.response, str) else json.dumps(self.response)


VALID_READING = {
    "decision": "PARTIAL",
    "confidence": 0.6,
    "control_id": "CC6.1",
    "evidence_strength": "moderate",
    "reasoning": (
        "Fact: the artefact records a completed access review with its approval "
        "record. Interpretation: it evidences the design of the review but not "
        "its operation across a period."
    ),
    "identified_gaps": ["No artefact covering the review cadence beyond one cycle."],
    "recommended_actions": ["Attach the access review procedure and its approval record."],
}


@asynccontextmanager
async def _client(tmp_path: Path):
    """Drive the app over an in-process transport, one session per request."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with maker() as seed_db:
            async def _override():
                async with maker() as request_db:
                    try:
                        yield request_db
                    finally:
                        await request_db.close()

            app.dependency_overrides[get_db] = _override
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client, seed_db
        app.dependency_overrides.pop(get_db, None)
    finally:
        await engine.dispose()


async def _seed(db, dataset: Path, tmp_path: Path, attach: dict[str, str] | None = None):
    result = await FrameworkService(db).import_json(
        json.loads(dataset.read_text(encoding="utf-8"))
    )
    await db.flush()
    assessment = Assessment(
        name="Acme", framework_ids=[result["framework_id"]], organization="Acme",
    )
    db.add(assessment)
    await db.flush()
    for code, text in (attach or {}).items():
        await _attach(db, assessment.id, result["framework_id"], tmp_path, code, text)
    await db.commit()
    return assessment, result["framework_id"]


async def _seed_combined(db, tmp_path: Path, attach: dict[str, str] | None = None):
    """An assessment scoped to BOTH frameworks (the combined path's premise)."""
    soc2 = await FrameworkService(db).import_json(
        json.loads(SOC2_FILE.read_text(encoding="utf-8"))
    )
    nist = await FrameworkService(db).import_json(
        json.loads(NIST_FILE.read_text(encoding="utf-8"))
    )
    await db.flush()
    assessment = Assessment(
        name="Acme Combined",
        framework_ids=[soc2["framework_id"], nist["framework_id"]],
        organization="Acme",
    )
    db.add(assessment)
    await db.flush()
    for code, text in (attach or {}).items():
        await _attach(db, assessment.id, soc2["framework_id"], tmp_path, code, text)
    await db.commit()
    return assessment, {"soc2": soc2["framework_id"], "nist": nist["framework_id"]}


async def _attach(db, assessment_id: str, framework_id: str, tmp_path: Path,
                  code: str, text: str) -> None:
    control = (await db.execute(
        select(Control).where(Control.framework_id == framework_id, Control.code == code)
    )).scalar_one()
    d = tmp_path / "evidence" / assessment_id
    d.mkdir(parents=True, exist_ok=True)
    name = f"{code}.txt"
    (d / f"{name}.preview.txt").write_text(text, encoding="utf-8")
    response = Response(
        assessment_id=assessment_id, question_id=f"q-{code}",
        control_id=control.id, response_value="yes", score=100.0,
    )
    db.add(response)
    await db.flush()
    db.add(Evidence(
        response_id=response.id, assessment_id=assessment_id,
        file_name=name, file_path=str(d / name),
    ))


def _install_stub(monkeypatch, response=None):
    from backend.api.compliance.semantic import client as client_module

    stub = StubLLM(response or VALID_READING)
    monkeypatch.setattr(
        client_module.SemanticEvaluator, "_resolve_client", lambda self, model: stub
    )
    return stub


def _assessor_headers():
    return {"X-User-Email": "auditor@x.io", "X-User-Role": "auditor"}


# ── pure normalisation helpers ─────────────────────────────────────────────

def test_quality_band_mapping_matches_the_rubric():
    assert quality_from_score(0.80).value == "STRONG"
    assert quality_from_score(0.75).value == "STRONG"
    assert quality_from_score(0.60).value == "MEDIUM"
    assert quality_from_score(0.40).value == "WEAK"
    assert quality_from_score(0.0).value == "MISSING"
    assert quality_from_score(None).value == "MISSING"


def test_duplicate_evidence_collapses_and_budget_is_enforced():
    refs = [
        EvidenceRef(f"e{i}", text="Identical access review record." * 6)
        for i in range(6)
    ]
    kept, dropped, _ = dedupe_by_hash(refs)
    assert dropped == 5
    assert len(kept) == 1

    distinct = [
        EvidenceRef(f"e{i}", text=f"Access review record number {i}." * 10)
        for i in range(10)
    ]
    budgeted, dropped_budget, truncated = budget_evidence(distinct)
    assert len(budgeted) == 8
    assert dropped_budget == 2
    assert truncated == 0

    oversized, dropped_oversized, truncated_oversized = budget_evidence(
        [EvidenceRef("b0", text="x" * 3000)]
    )
    assert dropped_oversized == 0
    assert truncated_oversized == 1
    assert len(oversized[0].text) == 1200


def test_format_detection_prefers_extensions_then_content_markers():
    assert detect_format("access_review_Q1.json", "", "", "provided").value == "structured_json"
    assert detect_format("securitypolicy.pdf", "", "", "provided").value == "unknown"
    assert detect_format("main.tf", "", "", "provided").value == "configuration"
    assert detect_format("screenshot.png", "", "", "provided").value == "screenshot"
    assert detect_format("", "user access entitlements by role", "", "provided").value == "access_record"
    assert detect_format("", "audit log with timestamp entries", "", "provided").value == "log_file"
    assert detect_format("", "policy: acceptable use of systems and data", "", "provided").value == "policy_document"


def test_content_hash_is_case_and_whitespace_stable():
    assert content_hash("Access Review\n2026") == content_hash("access   review 2026")


def test_evidence_chunks_split_long_artefacts():
    assert evidence_chunks("p" * 100) == ["p" * 100]
    assert len(evidence_chunks("q" * 3000)) > 1


# ── the combined write path ────────────────────────────────────────────────

def test_assessment_pipeline_combined_run_returns_report_and_persists(tmp_path, monkeypatch):
    _install_stub(monkeypatch)

    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fws = await _seed_combined(
                db, tmp_path, attach={"CC6.1": DESIGN_EVIDENCE, "CC1.1": POLICY_ONLY},
            )
            r = await client.post(
                f"/assessments/{assessment.id}/assessment-pipeline",
                json={},
                headers=_assessor_headers(),
            )
            assert r.status_code == 200, r.text

            # Persisted semantic rows were written for the evidence-bearing control.
            raised = (await db.execute(
                select(func.count()).select_from(SemanticReviewRecord).where(
                    SemanticReviewRecord.assessment_id == assessment.id
                )
            )).scalar_one()
            return r.json(), raised, assessment.id

    payload, semantic_rows, aid = run(body())
    assert payload["mode"] == "pipeline"
    assert payload["advisory"] is True
    assert len(payload["frameworks"]) == 2
    codes = {f["framework"] for f in payload["frameworks"]}
    assert codes == {"nist-csf-2-0", "soc2-type-1"}

    by_code = {f["framework"]: f for f in payload["frameworks"]}
    assert by_code["soc2-type-1"]["summary"]["total"] == SOC2_COUNT
    assert by_code["nist-csf-2-0"]["summary"]["total"] == NIST_COUNT
    assert payload["summary"]["total"] == SOC2_COUNT + NIST_COUNT

    controls = by_code["soc2-type-1"]["controls"]
    cc61 = next(c for c in controls if c["control"]["control_id"] == "CC6.1")
    cc11 = next(c for c in controls if c["control"]["control_id"] == "CC1.1")

    # The strong design artefact passes; the policy-only one never does.
    assert cc61["status"] == "PASS"
    assert cc61["evidence_quality"]["is_implementation_grade"] is True
    assert cc11["status"] == "PARTIAL"
    assert cc11["evidence_quality"]["band"] == "WEAK"
    assert cc11["evidence_quality"]["is_implementation_grade"] is False

    # The 12 report fields are all present on a control row.
    for key in (
        "framework", "control", "requirement", "status", "evidence", "evidence_quality",
        "confidence", "historical_context", "finding", "risk", "recommendation",
        "semantic_advisory",
    ):
        assert key in cc61

    # Cost accounting is real and present.
    metrics = payload["metrics"]
    assert metrics["frameworks"] == 2
    assert metrics["controls_reviewed"] == SOC2_COUNT + NIST_COUNT
    assert metrics["evidence_files_seen"] == 2
    assert metrics["deduplicated"] == 0
    assert metrics["semantic_evaluations"] >= 0
    assert metrics["avoided_llm_calls"] == (
        metrics["controls_skipped_semantic"] + metrics["semantic_cache_hits"]
    )
    assert metrics["memory_records_written"] > 0
    assert semantic_rows >= 1

    # The per-framework summary aggregates into the report header.
    assert payload["summary"]["conclusive"] >= 1


def test_policy_document_alone_cannot_pass_even_on_a_critical_technical_control(tmp_path, monkeypatch):
    _install_stub(monkeypatch)

    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path, attach={"CC6.1": POLICY_ONLY})
            r = await client.post(
                f"/assessments/{assessment.id}/assessment-pipeline",
                json={"framework_id": fw_id},
                headers=_assessor_headers(),
            )
            assert r.status_code == 200, r.text
            control = next(
                c for c in r.json()["frameworks"][0]["controls"]
                if c["control"]["control_id"] == "CC6.1"
            )
            return control

    control = run(body())
    assert control["status"] == "PARTIAL"
    assert control["evidence_quality"]["band"] == "WEAK"
    assert control["evidence"]["count"] == 1
    assert control["evidence"]["items"][0]["format"] == "policy_document"


def test_pipeline_restricts_to_requested_controls(tmp_path, monkeypatch):
    _install_stub(monkeypatch)

    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC1.1": DESIGN_EVIDENCE}
            )
            r = await client.post(
                f"/assessments/{assessment.id}/assessment-pipeline",
                json={"framework_id": fw_id, "control_codes": ["CC1.1"]},
                headers=_assessor_headers(),
            )
            assert r.status_code == 200, r.text
            payload = r.json()
            return payload, assessment.id

    payload, aid = run(body())
    assert len(payload["frameworks"]) == 1
    group = payload["frameworks"][0]
    assert group["summary"]["total"] == 1
    assert [c["control"]["control_id"] for c in group["controls"]] == ["CC1.1"]
    assert payload["metrics"]["controls_reviewed"] == 1


def test_pipeline_requires_assessor_role(tmp_path, monkeypatch):
    _install_stub(monkeypatch)

    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            blocked = await client.post(
                f"/assessments/{assessment.id}/assessment-pipeline",
                json={"framework_id": fw_id},
                headers={"X-User-Email": "owner@x.io", "X-User-Role": "evidence_contributor"},
            )
            return blocked.status_code

    assert run(body()) == 403


def test_pipeline_unknown_assessment_is_404(tmp_path, monkeypatch):
    _install_stub(monkeypatch)

    async def body():
        async with _client(tmp_path) as (client, _):
            r = await client.post(
                "/assessments/nope/assessment-pipeline", json={}, headers=_assessor_headers()
            )
            return r.status_code

    assert run(body()) == 404


# ── the read-only report ───────────────────────────────────────────────────

def test_compliance_report_reads_persisted_readings_without_model_calls(tmp_path, monkeypatch):
    _install_stub(monkeypatch)

    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fws = await _seed_combined(
                db, tmp_path, attach={"CC6.1": DESIGN_EVIDENCE},
            )
            ran = await client.post(
                f"/assessments/{assessment.id}/assessment-pipeline",
                json={}, headers=_assessor_headers(),
            )
            assert ran.status_code == 200, ran.text

            # The read path is open to identified (non-owner) callers with no headers.
            r = await client.get(f"/assessments/{assessment.id}/compliance-report")
            assert r.status_code == 200, r.text
            again = await client.get(f"/assessments/{assessment.id}/compliance-report")
            return r.json(), again.json(), assessment.id

    payload, again, aid = run(body())
    assert payload["advisory"] is True
    assert payload["mode"] == "report"
    by_code = {f["framework"]: f for f in payload["frameworks"]}
    assert {"nist-csf-2-0", "soc2-type-1"} == set(by_code)
    assert by_code["soc2-type-1"]["summary"]["total"] == SOC2_COUNT
    assert by_code["nist-csf-2-0"]["summary"]["total"] == NIST_COUNT

    controls = by_code["soc2-type-1"]["controls"]
    cc61 = next(c for c in controls if c["control"]["control_id"] == "CC6.1")
    # The semantic reading was persisted by the pipeline, not recomputed.
    assert cc61["semantic_advisory"] is not None
    assert cc61["semantic_advisory"]["model_invoked"] is True

    # The read path costs no model invocations.
    assert payload["metrics"]["semantic_evaluations"] == 0
    assert payload["metrics"]["semantic_cache_hits"] == 0

    # Deterministic across refreshes.
    assert again["summary"] == payload["summary"]
    assert {f["run_id"] for f in again["frameworks"]} == {f["run_id"] for f in payload["frameworks"]}


def test_compliance_report_renders_deterministic_rows_before_any_pipeline(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC1.1": DESIGN_EVIDENCE},
            )
            ev = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id}, headers=_assessor_headers(),
            )
            assert ev.status_code == 200, ev.text
            r = await client.get(f"/assessments/{assessment.id}/compliance-report")
            assert r.status_code == 200, r.text
            return r.json(), assessment.id

    payload, aid = run(body())
    assert payload["mode"] == "report"
    assert len(payload["frameworks"]) == 1
    group = payload["frameworks"][0]
    assert group["framework"] == "soc2-type-1"
    assert group["summary"]["total"] == SOC2_COUNT
    assert all(c["semantic_advisory"] is None for c in group["controls"])
    assert payload["metrics"]["semantic_evaluations"] == 0
    assert payload["metrics"]["memory_records_written"] == 0


def test_compliance_report_for_unknown_assessment_is_404(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, _):
            r = await client.get("/assessments/nope/compliance-report")
            return r.status_code

    assert run(body()) == 404


def test_combined_assessment_presents_both_frameworks_as_current(tmp_path, monkeypatch):
    """After a combined run both frameworks' rows are `is_current` — the second
    framework's evaluation must not unset the first's."""

    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fws = await _seed_combined(
                db, tmp_path, attach={"CC6.1": DESIGN_EVIDENCE},
            )
            r = await client.post(
                f"/assessments/{assessment.id}/assessment-pipeline",
                json={}, headers=_assessor_headers(),
            )
            assert r.status_code == 200, r.text
            listed = await client.get(f"/assessments/{assessment.id}/evaluations")
            assert listed.status_code == 200, listed.text
            totals = {}
            for fw in listed.json()["evaluations"]:
                totals[fw["framework"]] = totals.get(fw["framework"], 0) + 1
            return totals, assessment.id

    totals, aid = run(body())
    assert totals == {"nist-csf-2-0": NIST_COUNT, "soc2-type-1": SOC2_COUNT}
