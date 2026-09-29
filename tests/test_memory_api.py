"""HTTP tests for the Hindsight memory layer, including the DEMO learning flow.

The DEMO the product committed to, made concrete and assertable:

  Interaction 1  — MFA / logical-access control fails the first time
                   → NEW_FINDING ("this is the first we see of it").
  Interaction 5  — the same control fails again, and the organisation already
                   holds an approved exception
                   → KNOWN_EXCEPTION (the agent *remembers* the exception).
  Interaction 20 — repeated failures then continue past the exception's expiry
                   → RECURRING_FINDING, then ESCALATION_REQUIRED.

What the flow must prove, per the spec:

* memory retrieval works           — matched_memories names the rows that were used
* human feedback persists          — control history returns it
* previous decisions influence context — an exception changes the label to
                                       KNOWN_EXCEPTION
* expired exceptions not active    — after expiry the label escalates
* repeated findings detected       — recurring-findings endpoint reports them
* organisation data isolated       — a second organisation sees no memory

Groq is stubbed; no key and no network are involved. (Tests are sync `def`s that
drive the app with `asyncio.run`, matching the other API test module.)
"""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.api.models  # noqa: F401  (register every table on Base.metadata)
from backend.api.database import Base, get_db
from backend.api.main import app
from backend.api.models.assessment import Assessment
from backend.api.models.memory import ComplianceException
from backend.api.services.framework_service import FrameworkService

CANONICAL = Path(__file__).resolve().parents[1] / "backend" / "api" / "data" / "canonical"
SOC2_FILE = CANONICAL / "soc2_tsc_2017.json"


def run(coro):
    return asyncio.run(coro)


class StubLLM:
    """Stands in for the Groq LLMClient; it reports the control it was asked about.

    The enrichment contract demands `control_id` match the control under review,
    so the stub answers for exactly that control rather than sending a canned
    object that parse_enrichment would reject.
    """

    def __init__(self):
        self.calls: list[str] = []

    def invoke(self, system_prompt, user_prompt=None, max_tokens: int = 4096) -> str:
        self.calls.append(user_prompt or "")
        match = re.search(r"CONTROL UNDER REVIEW: (\S+)", user_prompt or "")
        control = match.group(1) if match else "CC6.1"
        return json.dumps({
            "control_id": control,
            "relationship_to_history": "The stored memory shows this control "
                "failing before; today's finding is another instance of the same gap.",
            "root_cause_assessment": "Evidence remains unchanged since the last review.",
            "changed_since_last_review": False,
            "escalation_recommendation": False,
            "recommended_actions": ["re-run the evidence collection for this control"],
        })


@asynccontextmanager
async def _client(tmp_path: Path):
    """Drive the app over an in-process transport; one request session each time."""
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


async def _seed(db, organization: str) -> tuple[str, str]:
    """Import SOC 2 and create an assessment for one organisation.

    No evidence is attached: evaluating an in-scope control with nothing against
    it deterministically yields INSUFFICIENT_EVIDENCE, which is a finding status,
    so each run re-produces the same finding type (evidence_gap) — exactly what a
    repeating-failures story needs.
    """
    result = await FrameworkService(db).import_json(
        json.loads(SOC2_FILE.read_text(encoding="utf-8"))
    )
    await db.flush()
    assessment = Assessment(
        name=f"{organization} assessment",
        framework_ids=[result["framework_id"]],
        organization=organization,
    )
    db.add(assessment)
    await db.flush()
    await db.commit()
    return assessment.id, result["framework_id"]


def _install_stub(monkeypatch) -> StubLLM:
    """Give the MemoryEnricher a working (stubbed) Groq transport."""
    import backend.api.compliance.memory.enrich as enrich_module
    import backend.api.config as config_module

    stub = StubLLM()
    monkeypatch.setattr(config_module.settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(enrich_module, "LLMClient", lambda **kwargs: stub)
    return stub


# ── helpers ──────────────────────────────────────────────────────────────────

async def _evaluate(client, assessment_id, framework_id, codes):
    return await client.post(
        f"/assessments/{assessment_id}/evaluations",
        json={"framework_id": framework_id, "control_codes": codes},
    )


async def _review(client, assessment_id, framework_id, codes, model_inference=True):
    return await client.post(
        f"/assessments/{assessment_id}/hindsight/review",
        json={
            "framework_id": framework_id,
            "control_codes": codes,
            "model_inference": model_inference,
        },
    )


async def _classify(client, assessment_id, framework_id, code, model_inference=True):
    resp = await _review(client, assessment_id, framework_id, [code],
                         model_inference=model_inference)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["recorded"] == 1, payload
    return payload["items"][0]


# ── the DEMO ─────────────────────────────────────────────────────────────────

async def _flow_interaction_1(client, db, tmp_path, monkeypatch):
    _install_stub(monkeypatch)
    aid, fid = await _seed(db, "acme")
    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200

    item = await _classify(client, aid, fid, "CC6.1")
    assert item["classification"] == "NEW_FINDING"
    assert item["occurrence"] == 1
    assert item["finding_type"] == "evidence_gap"
    # No stored memory exists yet, so there is nothing to enrich against — the
    # model is deliberately not consulted (cost control), and the deterministic
    # label stands on its own.
    assert item["enrichment"] is None
    assert item["basis"]  # the why is explicit


def test_interaction_1_new_finding(tmp_path, monkeypatch):
    async def _go():
        async with _client(tmp_path) as (client, db):
            await _flow_interaction_1(client, db, tmp_path, monkeypatch)
    run(_go())


async def _flow_interaction_5(client, db):
    aid, fid = await _seed(db, "acme")
    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
    assert (await _classify(client, aid, fid, "CC6.1"))["classification"] == "NEW_FINDING"

    # The assessor grants an exception (future expiry → active).
    resp = await client.post(
        f"/assessments/{aid}/hindsight/decisions",
        json={
            "control_code": "CC6.1",
            "framework": "soc2-type-1",
            "decision": "exception",
            "reason": "Migration to the new IdP completes this quarter.",
            "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat(),
            "human_feedback": "Owner owns the evidence by end of quarter.",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["exception_id"]

    # Same control fails again (new evaluation run → new evaluation row).
    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
    item = await _classify(client, aid, fid, "CC6.1")
    assert item["classification"] == "KNOWN_EXCEPTION"
    assert item["occurrence"] == 2
    # Memory exists now, so the model is asked to narrate the relationship.
    assert item["enrichment"] is not None
    assert item["enrichment"]["control_id"] == "CC6.1"
    # The exception (and the prior finding) are named as matched memory.
    assert any("prior evidence_gap on CC6.1" in line for line in item["matched_memories"])
    assert any("active exception" in line for line in item["matched_memories"])
    assert "1 prior finding" in item["memory_summary"]


def test_interaction_5_known_exception_after_grant(tmp_path, monkeypatch):
    def _go():
        async def _inner():
            async with _client(tmp_path) as (client, db):
                _install_stub(monkeypatch)
                await _flow_interaction_5(client, db)
        run(_inner())
    _go()


async def _flow_recurring(client, db):
    aid, fid = await _seed(db, "acme")
    assert (await _evaluate(client, aid, fid, ["CC1.1"])).status_code == 200
    assert (await _classify(client, aid, fid, "CC1.1", model_inference=False))[
        "classification"] == "NEW_FINDING"

    assert (await _evaluate(client, aid, fid, ["CC1.1"])).status_code == 200
    item = await _classify(client, aid, fid, "CC1.1", model_inference=False)
    assert item["classification"] == "RECURRING_FINDING"
    assert item["occurrence"] == 2
    # No inference was requested and none happened.
    assert item["enrichment"] is None
    assert item["enrichment_error"] is None


def test_recurring_finding_without_exception(tmp_path):
    async def _go():
        async with _client(tmp_path) as (client, db):
            await _flow_recurring(client, db)
    run(_go())


async def _flow_interaction_20(client, db):
    aid, fid = await _seed(db, "acme")
    for _ in range(3):
        assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
        await _classify(client, aid, fid, "CC6.1")
    await client.post(
        f"/assessments/{aid}/hindsight/decisions",
        json={"control_code": "CC6.1", "framework": "soc2-type-1",
              "decision": "exception", "reason": "transitional",
              "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat()},
    )

    # Expire the exception by pushing its date into the past, then re-review.
    exc = (await db.execute(
        select(ComplianceException)
        .where(ComplianceException.control_id == "CC6.1")
        .order_by(ComplianceException.created_at.desc())
    )).scalars().first()
    exc.expires_at = datetime.utcnow() - timedelta(days=1)
    await db.commit()

    # A fresh run on the same control — the 4th occurrence — now that nothing
    # shields it.
    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
    item = await _classify(client, aid, fid, "CC6.1")
    # No active exception shields it; this is the Nth repeat.
    assert item["classification"] == "ESCALATION_REQUIRED"
    assert item["occurrence"] == 4
    assert any("occurred" in b for b in item["basis"])

    # The exception is now honestly reported as expired.
    listed = await client.get(
        f"/hindsight/organizations/acme/exceptions"
    )
    assert listed.status_code == 200
    excs = listed.json()["exceptions"]
    assert any(e["id"] == exc.id and e["status"] == "expired" for e in excs)


def test_interaction_20_escalation_after_expiry(tmp_path, monkeypatch):
    async def _go():
        async with _client(tmp_path) as (client, db):
            _install_stub(monkeypatch)
            await _flow_interaction_20(client, db)
    run(_go())


# ── the required properties ──────────────────────────────────────────────────

async def _flow_feedback(client, db):
    aid, fid = await _seed(db, "acme")
    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
    await _classify(client, aid, fid, "CC6.1")
    resp = await client.post(
        f"/assessments/{aid}/hindsight/decisions",
        json={"control_code": "CC6.1", "framework": "soc2-type-1",
              "decision": "accepted",
              "human_feedback": "Risk accepted by the CISO on Mar 1."},
    )
    assert resp.status_code == 200

    history = await client.get(
        f"/hindsight/organizations/acme/controls/CC6.1/history"
    )
    assert history.status_code == 200
    memory = history.json()["memory"]
    assert any(m["human_feedback"] == "Risk accepted by the CISO on Mar 1."
               for m in memory)


def test_human_feedback_persists_and_affects_later_context(tmp_path, monkeypatch):
    async def _go():
        async with _client(tmp_path) as (client, db):
            _install_stub(monkeypatch)
            await _flow_feedback(client, db)
    run(_go())


async def _flow_idempotent(client, db):
    aid, fid = await _seed(db, "acme")
    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
    first = (await _review(client, aid, fid, ["CC6.1"], model_inference=False)).json()
    assert first["recorded"] == 1 and first["skipped"] == 0

    second = (await _review(client, aid, fid, ["CC6.1"], model_inference=False)).json()
    assert second["recorded"] == 0 and second["skipped"] == 1


def test_review_is_idempotent_per_evaluation_row(tmp_path):
    async def _go():
        async with _client(tmp_path) as (client, db):
            await _flow_idempotent(client, db)
    run(_go())


async def _flow_org_isolation(client, db):
    acme_aid, fid = await _seed(db, "acme")
    malory_aid, _ = await _seed(db, "malory")

    assert (await _evaluate(client, acme_aid, fid, ["CC6.1"])).status_code == 200
    acme_item = await _classify(client, acme_aid, fid, "CC6.1")
    assert acme_item["classification"] == "NEW_FINDING"

    # Acme's history exists…
    acme_history = await client.get(
        f"/hindsight/organizations/acme/controls/CC6.1/history"
    )
    assert acme_history.json()["memory"]

    # …but Malory's first review sees nothing from Acme.
    assert (await _evaluate(client, malory_aid, fid, ["CC6.1"])).status_code == 200
    malory_item = await _classify(client, malory_aid, fid, "CC6.1")
    assert malory_item["classification"] == "NEW_FINDING"
    assert not malory_item["matched_memories"]

    malory_history = await client.get(
        f"/hindsight/organizations/malory/controls/CC6.1/history"
    )
    assert malory_history.json()["memory"]  # its own, after its own review


def test_organization_data_isolated(tmp_path, monkeypatch):
    async def _go():
        async with _client(tmp_path) as (client, db):
            _install_stub(monkeypatch)
            await _flow_org_isolation(client, db)
    run(_go())


async def _flow_recurring_and_summary(client, db):
    aid, fid = await _seed(db, "acme")
    for code in ("CC6.1", "CC1.1"):
        for _ in range(3):
            assert (await _evaluate(client, aid, fid, [code])).status_code == 200
            await _review(client, aid, fid, [code], model_inference=False)

    recurring = await client.get(
        f"/hindsight/organizations/acme/recurring-findings?threshold=2"
    )
    assert recurring.status_code == 200
    body = recurring.json()
    assert body["total"] >= 2
    by_control = {i["control_id"]: i for i in body["items"]}
    assert by_control["CC6.1"]["occurrences"] == 3
    assert by_control["CC1.1"]["remediated"] is False

    summary = await client.get(
        f"/hindsight/organizations/acme/risk-summary"
    )
    assert summary.status_code == 200
    s = summary.json()
    assert s["by_finding_type"]["evidence_gap"] >= 6
    assert any(c["control_id"] == "CC6.1" for c in s["recurrence_leaderboard"])
    assert s["open_findings"] >= 6


def test_recurring_findings_and_risk_summary(tmp_path):
    async def _go():
        async with _client(tmp_path) as (client, db):
            await _flow_recurring_and_summary(client, db)
    run(_go())


async def _flow_similar(client, db):
    aid, fid = await _seed(db, "acme")
    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
    await _classify(client, aid, fid, "CC6.1", model_inference=False)

    # A separate control with the same finding type: CC8.1 evidence gap.
    assert (await _evaluate(client, aid, fid, ["CC8.1"])).status_code == 200
    await _classify(client, aid, fid, "CC8.1", model_inference=False)

    similar = await client.get(
        f"/hindsight/organizations/acme/similar-findings?control_id=CC6.1"
    )
    assert similar.status_code == 200
    controls = {m["control_id"] for m in similar.json()["similar"]}
    assert "CC8.1" in controls
    assert "CC6.1" not in controls


def test_similar_findings_finds_same_type_on_other_controls(tmp_path):
    async def _go():
        async with _client(tmp_path) as (client, db):
            await _flow_similar(client, db)
    run(_go())


async def _flow_enrichment_contained(client, db):
    aid, fid = await _seed(db, "acme")
    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
    item = await _classify(client, aid, fid, "CC6.1")
    assert item["classification"] == "NEW_FINDING"
    assert item["status"] == "INSUFFICIENT_EVIDENCE"
    # With no memory there is nothing to enrich, so no key is even consulted.
    assert item["enrichment"] is None


def test_enrichment_failure_is_contained(tmp_path):
    """No key → enrichment errors are recorded, never the classification."""
    async def _go():
        async with _client(tmp_path) as (client, db):
            await _flow_enrichment_contained(client, db)
    run(_go())


# ── exception lifecycle surface ──────────────────────────────────────────────

async def _flow_exception_routes(client, db):
    aid, fid = await _seed(db, "acme")
    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
    await _classify(client, aid, fid, "CC6.1", model_inference=False)

    granted = await client.post(
        f"/assessments/{aid}/hindsight/exceptions",
        json={"control_code": "CC6.1", "framework": "soc2-type-1",
              "reason": "scheduled sso migration"},
    )
    assert granted.status_code == 200, granted.text
    exc_id = granted.json()["exception_id"]

    # While active it shields.
    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
    item = await _classify(client, aid, fid, "CC6.1", model_inference=False)
    assert item["classification"] == "KNOWN_EXCEPTION"

    # Revoke it; the control becomes actionable again.
    revoked = await client.post(
        f"/hindsight/exceptions/{exc_id}/revoke",
        json={"reason": "owner supplied remediation evidence"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"

    assert (await _evaluate(client, aid, fid, ["CC6.1"])).status_code == 200
    item = await _classify(client, aid, fid, "CC6.1", model_inference=False)
    # Occurrence 3 and nothing shields it → escalation (revoked exceptions do not).
    assert item["classification"] == "ESCALATION_REQUIRED"


def test_exception_grant_and_revoke(tmp_path):
    async def _go():
        async with _client(tmp_path) as (client, db):
            await _flow_exception_routes(client, db)
    run(_go())