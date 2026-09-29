"""HTTP-level tests for the semantic review route.

The route is the seam between the stored deterministic evaluations and the LLM
layer, so what matters here is that it is genuinely advisory: it must return the
model's reading, repeat the recorded status beside it, and change nothing in the
database. The model is stubbed, so no test needs an API key or a network.

Run with:  python -m pytest tests/test_compliance_semantic_api.py -q
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
from backend.api.models.compliance import ComplianceEvaluation
from backend.api.models.framework import Control
from backend.api.services.framework_service import FrameworkService

CANONICAL = Path(__file__).resolve().parents[1] / "backend" / "api" / "data" / "canonical"
SOC2_FILE = CANONICAL / "soc2_tsc_2017.json"
NIST_FILE = CANONICAL / "nist_csf_2_0.json"

ACCESS_REVIEW = (
    "Quarterly user access review, signed 2026-01-15 by the security owner. "
    "Scope: 1,240 active accounts across production and corporate SSO. Method: "
    "entitlement export compared to the approved role catalogue. Result: 1,240 "
    "reviewed, 14 entitlements removed, 6 exceptions escalated. Evidence: "
    "entitlement export, review worksheet, and the approval record."
)


def run(coro):
    return asyncio.run(coro)


class StubLLM:
    """Stands in for the Groq client, so no key or network is needed."""

    def __init__(self, response: dict | str):
        self.response = response
        self.calls: list[str] = []

    def invoke(self, system_prompt, user_prompt=None, max_tokens: int = 4096) -> str:
        self.calls.append(user_prompt or "")
        return self.response if isinstance(self.response, str) else json.dumps(self.response)


@asynccontextmanager
async def _client(tmp_path: Path):
    """Drive the app over an in-process transport, one session per request.

    Every HTTP request gets its own session, exactly as a real server does, and
    the test holds a separate session for seeding. That split is deliberate:
    sharing one session keeps the Assessment in the identity map, which silently
    satisfies a relationship access that would otherwise raise MissingGreenlet in
    production.
    """
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
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                yield client, seed_db
        app.dependency_overrides.pop(get_db, None)
    finally:
        await engine.dispose()


async def _seed(db, dataset: Path, tmp_path: Path, attach: dict[str, str] | None = None):
    """Import a framework, create an assessment, attach evidence to controls."""
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


def _install_stub(monkeypatch, response) -> StubLLM:
    """Swap the SemanticEvaluator's client for a stub, for the whole request."""
    from backend.api.compliance.semantic import client as client_module

    stub = StubLLM(response)
    monkeypatch.setattr(
        client_module.SemanticEvaluator, "_resolve_client", lambda self, model: stub
    )
    return stub


VALID_READING = {
    "decision": "PARTIAL",
    "confidence": 0.6,
    "control_id": "CC6.1",
    "evidence_strength": "moderate",
    "reasoning": (
        "Fact: the submitted artefact records one completed access review with "
        "its approval record. Interpretation: it evidences the design of the "
        "review but not its operation across a period."
    ),
    "identified_gaps": ["No artefact covering the review cadence beyond one cycle."],
    "recommended_actions": ["Attach the access review procedure and its approval record."],
}


# ── the route exists and is advisory ────────────────────────────────────────

def test_semantic_review_returns_the_model_reading_beside_the_recorded_status(tmp_path, monkeypatch):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC6.1": ACCESS_REVIEW},
            )
            run_id = (await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )).json()["run_id"]

            _install_stub(monkeypatch, VALID_READING)
            r = await client.post(
                f"/assessments/{assessment.id}/semantic-review",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )
            return r.status_code, r.json(), run_id

    status, payload, _ = run(body())

    assert status == 200
    assert payload["advisory"] is True
    assert payload["asserts_operating_effectiveness"] is False
    assert payload["reviewed"] == 1
    assert payload["framework"] == "soc2-type-1"

    item = payload["results"][0]
    # The recorded claim and the model's opinion are separate fields, and the
    # recorded claim is the one the platform stands behind.
    assert item["control_id"] == "CC6.1"
    assert item["authoritative_status"] in {
        "PASS", "PARTIAL", "FAIL", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE",
    }
    assert item["assessment"]["control_id"] == "CC6.1"
    assert item["assessment"]["identified_gaps"]
    assert item["model_invoked"] is True


def test_semantic_review_writes_nothing(tmp_path, monkeypatch):
    """The route is read-only: the stored evaluations must be untouched."""
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC6.1": ACCESS_REVIEW},
            )
            await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )

            before = (await db.execute(
                select(ComplianceEvaluation).where(
                    ComplianceEvaluation.assessment_id == assessment.id
                )
            )).scalars().all()
            snapshot = [
                (e.id, e.status, e.score, e.confidence, e.reasoning, e.evaluated_at)
                for e in before
            ]

            _install_stub(monkeypatch, VALID_READING)
            r = await client.post(
                f"/assessments/{assessment.id}/semantic-review",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )

            after = (await db.execute(
                select(ComplianceEvaluation).where(
                    ComplianceEvaluation.assessment_id == assessment.id
                )
            )).scalars().all()
            return r.status_code, snapshot, [
                (e.id, e.status, e.score, e.confidence, e.reasoning, e.evaluated_at)
                for e in after
            ]

    status, before, after = run(body())

    assert status == 200
    assert before == after, "semantic review must not mutate a stored evaluation"
    assert before, "the evaluation run should have produced rows to review"


def test_semantic_review_never_promotes_insufficient_evidence(tmp_path, monkeypatch):
    """A confident model cannot turn a non-claim into a claim."""
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(db, SOC2_FILE, tmp_path)  # no evidence
            await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )
            _install_stub(monkeypatch, {
                **VALID_READING,
                "decision": "PASS",
                "confidence": 0.99,
                "evidence_strength": "strong",
            })
            r = await client.post(
                f"/assessments/{assessment.id}/semantic-review",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )
            return r.status_code, r.json()

    status, payload = run(body())

    assert status == 200
    item = payload["results"][0]
    assert item["authoritative_status"] == "INSUFFICIENT_EVIDENCE"
    assert item["model_invoked"] is False
    assert item["assessment"] is None


def test_semantic_review_rejects_a_hallucinated_control_id(tmp_path, monkeypatch):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC6.1": ACCESS_REVIEW},
            )
            await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )
            _install_stub(monkeypatch, {**VALID_READING, "control_id": "GV.OC-99"})
            r = await client.post(
                f"/assessments/{assessment.id}/semantic-review",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )
            return r.status_code, r.json()

    status, payload = run(body())

    assert status == 200
    item = payload["results"][0]
    assert item["assessment"] is None
    assert "GV.OC-99" in item["fallback_reason"]
    assert payload["rejected"] == 1


def test_type_2_overclaim_is_rejected_over_http(tmp_path, monkeypatch):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC6.1": ACCESS_REVIEW},
            )
            await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )
            _install_stub(monkeypatch, {
                **VALID_READING,
                "reasoning": (
                    "Fact: one artefact was submitted. Interpretation: this "
                    "demonstrates operating effectiveness over the period."
                ),
            })
            r = await client.post(
                f"/assessments/{assessment.id}/semantic-review",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )
            return r.status_code, r.json()

    status, payload = run(body())

    assert status == 200
    item = payload["results"][0]
    assert item["assessment"] is None
    assert "assurance overclaim" in item["fallback_reason"]
    assert payload["asserts_operating_effectiveness"] is False


def test_malformed_model_output_does_not_fail_the_request(tmp_path, monkeypatch):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC6.1": ACCESS_REVIEW},
            )
            await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )
            _install_stub(monkeypatch, "I think the control looks fine, honestly.")
            r = await client.post(
                f"/assessments/{assessment.id}/semantic-review",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )
            return r.status_code, r.json()

    status, payload = run(body())

    assert status == 200, "a bad model answer must not become a 5xx"
    item = payload["results"][0]
    assert item["assessment"] is None
    assert item["fallback_reason"]
    assert item["fallback_reasoning"]


# ── scope and error handling ────────────────────────────────────────────────

def test_semantic_review_rejects_an_unevaluable_framework(tmp_path, monkeypatch):
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, _fw_id = await _seed(db, SOC2_FILE, tmp_path)
            r = await client.post(
                f"/assessments/{assessment.id}/semantic-review",
                json={"framework_id": "00000000-0000-0000-0000-000000000000"},
            )
            return r.status_code

    assert run(body()) == 404


def test_semantic_review_of_an_unknown_assessment_is_404(tmp_path, monkeypatch):
    async def body():
        async with _client(tmp_path) as (client, db):
            _, fw_id = await _seed(db, SOC2_FILE, tmp_path)
            r = await client.post(
                "/assessments/00000000-0000-0000-0000-000000000000/semantic-review",
                json={"framework_id": fw_id},
            )
            return r.status_code

    assert run(body()) == 404


def test_nist_review_stays_point_in_time(tmp_path, monkeypatch):
    """A NIST review must not inherit SOC 2's Type 1 handling or claim more."""
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, NIST_FILE, tmp_path, attach={"GV.OC-01": ACCESS_REVIEW},
            )
            await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "control_codes": ["GV.OC-01"]},
            )
            _install_stub(monkeypatch, {**VALID_READING, "control_id": "GV.OC-01"})
            r = await client.post(
                f"/assessments/{assessment.id}/semantic-review",
                json={"framework_id": fw_id, "control_codes": ["GV.OC-01"]},
            )
            return r.status_code, r.json()

    status, payload = run(body())

    assert status == 200
    assert payload["framework"] == "nist-csf-2-0"
    assert payload["asserts_operating_effectiveness"] is False
    item = payload["results"][0]
    assert item["assessment"] is not None
    assert item["assessment"]["control_id"] == "GV.OC-01"


def test_semantic_review_skips_controls_outside_the_assessment_scope(tmp_path, monkeypatch):
    """Asking for a control the run never evaluated returns nothing, not a guess."""
    async def body():
        async with _client(tmp_path) as (client, db):
            assessment, fw_id = await _seed(
                db, SOC2_FILE, tmp_path, attach={"CC6.1": ACCESS_REVIEW},
            )
            await client.post(
                f"/assessments/{assessment.id}/evaluations",
                json={"framework_id": fw_id, "control_codes": ["CC6.1"]},
            )
            stub = _install_stub(monkeypatch, VALID_READING)
            r = await client.post(
                f"/assessments/{assessment.id}/semantic-review",
                json={"framework_id": fw_id, "control_codes": ["CC1.1"]},
            )
            return r.status_code, r.json(), len(stub.calls)

    status, payload, calls = run(body())

    assert status == 200
    assert payload["reviewed"] == 0
    assert calls == 0
