"""HTTP-level tests for the compliance routes.

Drives the real FastAPI app through an in-process ASGI transport against a
throwaway SQLite database, so routing, dependency overrides, authz and response
serialisation are all covered. Uses `asyncio.run` per test rather than a pytest
async plugin, matching the style of tests/test_compliance_service.py.

Run with:  python -m pytest tests/test_compliance_api.py -q
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.api.models  # noqa: F401  (register every table on Base.metadata)
from backend.api.database import Base, get_db
from backend.api.main import app
from backend.api.models.assessment import Assessment, Evidence, Response
from backend.api.models.framework import Control
from backend.api.services.framework_service import FrameworkService

CANONICAL = Path(__file__).resolve().parents[1] / "backend" / "api" / "data" / "canonical"
SOC2_FILE = CANONICAL / "soc2_tsc_2017.json"
NIST_FILE = CANONICAL / "nist_csf_2_0.json"

DESIGN_EVIDENCE = (
    "Walkthrough performed 2026-02-01. Current-state architecture diagram and "
    "code of conduct with approval record and revision history; effective date "
    "2025-11-01. The entity demonstrates a commitment to integrity and ethical values."
)
PERIOD_EVIDENCE = (
    "Sample of 25 user access tickets drawn from the population of 4,200 tickets "
    "throughout the period, with exception report showing 3 exceptions."
)


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def _client(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with maker() as db:
            async def _override():
                yield db

            app.dependency_overrides[get_db] = _override
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                yield client, db
        app.dependency_overrides.pop(get_db, None)
    finally:
        await engine.dispose()


async def _seed(db, dataset: Path, tmp_path: Path, attach: dict[str, str] | None = None):
    result = await FrameworkService(db).import_json(
        json.loads(dataset.read_text(encoding="utf-8"))
    )
    await db.flush()
    assessment = Assessment(
        name="Acme", framework_ids=[result["framework_id"]], organization="Acme"
    )
    db.add(assessment)
    await db.flush()

    for code, text in (attach or {}).items():
        control = (await db.execute(
            select(Control).where(
                Control.framework_id == result["framework_id"], Control.code == code
            )
        )).scalar_one()
        d = tmp_path / "evidence" / assessment.id
        d.mkdir(parents=True, exist_ok=True)
        name = f"{code}.txt"
        (d / f"{name}.preview.txt").write_text(text, encoding="utf-8")
        response = Response(
            assessment_id=assessment.id, question_id=f"q-{code}",
            control_id=control.id, response_value="yes", score=100.0,
        )
        db.add(response)
        await db.flush()
        db.add(Evidence(
            response_id=response.id, assessment_id=assessment.id,
            file_name=name, file_path=str(d / name),
        ))
    await db.commit()
    return assessment, result["framework_id"]


# ── catalogue ───────────────────────────────────────────────────────────────

def test_frameworks_endpoint_lists_only_evaluable_profiles(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, _):
            r = await client.get("/compliance/frameworks")
            return r.status_code, r.json()

    status, body = run(body())
    assert status == 200
    keys = {f["key"] for f in body["supported"]}
    assert keys == {"nist-csf-2-0", "soc2-type-1"}
    for profile in body["supported"]:
        assert profile["asserts_operating_effectiveness"] is False
        assert 0 < profile["pass_threshold"] <= 1
    # A framework with no adapter must not be offered.
    assert "iso-27001" not in keys
    # The drop-in format is documented in the response, for the next framework.
    assert "sub_domains" in body["dataset_format"]["domain"]


def test_controls_endpoint_resolves_requirements(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            _, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            r = await client.get(f"/compliance/frameworks/{fw_id}/controls")
            return r.status_code, r.json()

    status, body = run(body())
    assert status == 200
    assert body["total"] == 61
    assert body["assurance_level"] == "TYPE_1"
    assert body["framework"] == "soc2-type-1"
    first = body["controls"][0]
    # Controls come back as the evaluator sees them, not raw dataset wording.
    assert first["requirement"]
    assert isinstance(first["expected_evidence_types"], list)


def test_controls_endpoint_filters(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            _, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            domain = await client.get(f"/compliance/frameworks/{fw_id}/controls",
                                       params={"domain_code": "SECURITY"})
            search = await client.get(f"/compliance/frameworks/{fw_id}/controls",
                                      params={"search": "CC1.1"})
            return domain.json(), search.json()

    domain, search = run(body())
    assert 0 < domain["total"] < 61
    assert {c["domain_code"] for c in domain["controls"]} == {"SECURITY"}
    assert search["total"] == 1
    assert search["controls"][0]["control_id"] == "CC1.1"


def test_controls_for_unknown_framework_is_404(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, _):
            r = await client.get("/compliance/frameworks/nope/controls")
            return r.status_code

    assert run(body()) == 404


# ── evaluation ──────────────────────────────────────────────────────────────

def test_evaluate_endpoint_persists_and_returns_results(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC1.1": DESIGN_EVIDENCE}
            )
            r = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Email": "auditor@x.io", "X-User-Role": "auditor"},
            )
            listed = await client.get(f"/assessments/{assessment.id}/evaluations")
            return r.status_code, r.json(), listed.json()

    status, body, listed = run(body())
    assert status == 200
    assert body["total"] == 61
    assert body["counts"]["PASS"] == 1
    assert body["counts"]["INSUFFICIENT_EVIDENCE"] == 60
    assert body["findings_raised"] == 60
    assert body["assurance_level"] == "TYPE_1"
    assert body["asserts_operating_effectiveness"] is False
    # Persisted, not just returned.
    assert listed["total"] == 61
    assert {e["status"] for e in listed["evaluations"]} <= {
        "PASS", "PARTIAL", "FAIL", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE",
    }
    assert all(e["is_current"] for e in listed["evaluations"])


def test_evaluate_endpoint_pins_operating_effectiveness_to_false(tmp_path):
    """The guarantee must be visible in the wire format, not just the database."""
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC6.1": PERIOD_EVIDENCE}
            )
            r = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Role": "auditor"},
            )
            return r.json()

    body = run(body())
    assert body["asserts_operating_effectiveness"] is False
    assert all(e["asserts_operating_effectiveness"] is False for e in body["evaluations"])
    assert all(e["assurance_level"] == "TYPE_1" for e in body["evaluations"])


def test_evaluate_endpoint_accepts_a_control_subset(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC1.1": DESIGN_EVIDENCE}
            )
            r = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "control_codes": ["CC1.1", "CC1.2"]},
                headers={"X-User-Role": "auditor"},
            )
            return r.json()

    body = run(body())
    assert body["total"] == 2
    assert {e["control_code"] for e in body["evaluations"]} == {"CC1.1", "CC1.2"}


def test_evaluate_endpoint_can_skip_findings(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(db, NIST_FILE, tmp_path)
            r = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "create_findings": False},
                headers={"X-User-Role": "auditor"},
            )
            return r.json()

    assert run(body())["findings_raised"] == 0


def test_nist_evaluation_runs_end_to_end(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(db, NIST_FILE, tmp_path)
            r = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Role": "auditor"},
            )
            return r.json()

    body = run(body())
    assert body["total"] == 106
    assert body["assurance_level"] == "POINT_IN_TIME"
    assert body["counts"]["INSUFFICIENT_EVIDENCE"] == 106


def test_unsupported_framework_returns_422_with_supported_list(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            result = await FrameworkService(db).import_json({
                "code": "MYSTERY_FRAMEWORK", "name": "Mystery", "version": "1",
                "domains": [{"code": "D1", "name": "D1", "categories": [{
                    "code": "C1", "name": "C1",
                    "controls": [{"code": "X.1", "statement": "Do a thing."}],
                }]}],
            })
            await db.commit()
            assessment = Assessment(name="A", framework_ids=[result["framework_id"]])
            db.add(assessment)
            await db.commit()
            r = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": result["framework_id"]},
                headers={"X-User-Role": "auditor"},
            )
            return r.status_code, r.json()

    status, body = run(body())
    assert status == 422
    assert "nist-csf-2-0" in body["detail"] and "soc2-type-1" in body["detail"]


def test_missing_assessment_returns_404(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            _, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            r = await client.post(
                "/assessments/nope/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Role": "auditor"},
            )
            return r.status_code, r.json()

    status, body = run(body())
    assert status == 404
    assert "not found" in body["detail"]


def test_request_body_rejects_unknown_fields(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            r = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "typo_field": True},
                headers={"X-User-Role": "auditor"},
            )
            return r.status_code

    assert run(body()) == 422


def test_contributor_cannot_run_an_evaluation(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            r = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Role": "evidence_contributor"},
            )
            return r.status_code

    assert run(body()) == 403


# ── summary and audit ───────────────────────────────────────────────────────

def test_summary_endpoint_aggregates_by_framework(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC1.1": DESIGN_EVIDENCE}
            )
            await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Role": "auditor"},
            )
            r = await client.get(f"/assessments/{assessment.id}/evaluations/summary")
            return r.status_code, r.json()

    status, body = run(body())
    assert status == 200
    assert body["total"] == 61
    bucket = body["frameworks"][0]
    assert bucket["framework"] == "soc2-type-1"
    assert bucket["assurance_level"] == "TYPE_1"
    assert bucket["asserts_operating_effectiveness"] is False
    assert bucket["counts"]["PASS"] == 1
    assert bucket["conclusive"] == 1
    assert 0.0 < bucket["conclusive_pct"] < 100.0


def test_summary_on_unevaluated_assessment_is_empty_not_an_error(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, _ = await _seed(db, SOC2_FILE, tmp_path)
            r = await client.get(f"/assessments/{assessment.id}/evaluations/summary")
            return r.status_code, r.json()

    status, body = run(body())
    assert status == 200
    assert body == {"assessment_id": body["assessment_id"], "total": 0, "frameworks": []}


def test_audit_endpoint_lists_evaluation_events(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Role": "auditor", "X-User-Email": "auditor@x.io"},
            )
            r = await client.get(f"/assessments/{assessment.id}/evaluation-audit")
            return r.status_code, r.json()

    status, body = run(body())
    assert status == 200
    assert body["total"] == 1
    event = body["events"][0]
    assert event["action"] == "compliance.evaluated"
    assert event["user_email"] == "auditor@x.io"
    assert event["changes"]["framework"] == "soc2-type-1"
    assert "61 control(s)" in event["summary"]


def test_audit_endpoint_is_scoped_to_one_assessment(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            first, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            second = Assessment(name="Other", framework_ids=[fw_id])
            db.add(second)
            await db.commit()
            await client.post(
                f"/assessments/{first.id}/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Role": "auditor"},
            )
            mine = await client.get(f"/assessments/{first.id}/evaluation-audit")
            theirs = await client.get(f"/assessments/{second.id}/evaluation-audit")
            return mine.json(), theirs.json()

    mine, theirs = run(body())
    assert mine["total"] == 1
    assert theirs["total"] == 0


def test_rerun_supersedes_in_the_api(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            first = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Role": "auditor"},
            )
            second = await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Role": "auditor"},
            )
            current = await client.get(f"/assessments/{assessment.id}/evaluations")
            history = await client.get(
                f"/assessments/{assessment.id}/evaluations",
                params={"current_only": "false"},
            )
            return first.json(), second.json(), current.json(), history.json()

    first, second, current, history = run(body())
    assert first["run_id"] != second["run_id"]
    assert current["total"] == 61
    assert history["total"] == 122
    assert all(e["is_current"] for e in current["evaluations"])
    assert sum(1 for e in history["evaluations"] if e["is_current"]) == 61


def test_evaluation_list_filters_by_status(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC1.1": DESIGN_EVIDENCE}
            )
            await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id},
                headers={"X-User-Role": "auditor"},
            )
            r = await client.get(
                f"/assessments/{assessment.id}/evaluations",
                params={"status": "INSUFFICIENT_EVIDENCE"},
            )
            return r.json()

    body = run(body())
    assert body["total"] == 60
    assert all(e["status"] == "INSUFFICIENT_EVIDENCE" for e in body["evaluations"])


def test_evaluations_for_unknown_assessment_is_an_empty_list(tmp_path):
    async def body():
        async with _client(tmp_path) as (client, _):
            r = await client.get("/assessments/nope/evaluations")
            return r.status_code, r.json()

    status, body = run(body())
    assert status == 200
    assert body == {"assessment_id": "nope", "total": 0, "evaluations": []}

