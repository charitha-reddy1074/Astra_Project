"""Integration test: the bundled framework datasets import into the real schema.

Uses its own async engine against a throwaway SQLite file rather than the
application's global engine, so it is independent of import order and cannot
touch a developer's database.

This is the regression guard for the original failure mode: `_is_root_canonical`
rejects the dataset shape and the API shape wants a top-level `code`, so before
`FrameworkService` learned about the drop-in dataset format both bundled files
imported zero controls.

The coroutines are driven with `asyncio.run` rather than `pytest-asyncio` so the
file needs no pytest configuration and runs under a bare `pytest` install.

Run with:  python -m pytest tests/test_compliance_framework_import.py -q
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.compliance import get_adapter
from backend.api.compliance.dataset import (
    STATEMENT_KEYS,
    DatasetFormatError,
    dataset_to_api_canonical,
)
from backend.api.database import Base
from backend.api.models.framework import Category, Control, Domain, Framework, Question
from backend.api.services.framework_service import FrameworkService

CANONICAL = Path(__file__).resolve().parents[1] / "backend" / "api" / "data" / "canonical"

NIST_FILE = CANONICAL / "nist_csf_2_0.json"
SOC2_FILE = CANONICAL / "soc2_tsc_2017.json"


def run(coro):
    """Drive one coroutine to completion on a fresh event loop."""
    return asyncio.run(coro)


@asynccontextmanager
async def _session(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            yield db
    finally:
        await engine.dispose()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path,name,domains,categories,controls", [
    (NIST_FILE, "NIST Cybersecurity Framework (CSF)", 6, 22, 106),
    (SOC2_FILE, "AICPA SOC 2 - Trust Services Criteria (TSC 2017)", 5, 20, 61),
])
def test_dataset_imports_full_hierarchy(tmp_path, path, name, domains, categories, controls):
    async def body():
        async with _session(tmp_path) as db:
            result = await FrameworkService(db).import_json(_load(path))
            await db.commit()
            fid = result["framework_id"]

            async def count(model):
                stmt = select(func.count()).select_from(model).where(model.framework_id == fid)
                return (await db.execute(stmt)).scalar()

            return (
                result["framework_name"],
                await count(Domain),
                await count(Category),
                await count(Control),
                # One auto-generated question per control, as always.
                await count(Question),
            )

    got_name, got_domains, got_categories, got_controls, got_questions = run(body())
    assert got_name == name
    assert (got_domains, got_categories, got_controls) == (domains, categories, controls)
    assert got_questions == controls


def test_imported_framework_resolves_to_an_adapter(tmp_path):
    """The stored Framework.code must be enough to find the compliance adapter."""
    async def body():
        async with _session(tmp_path) as db:
            await FrameworkService(db).import_json(_load(SOC2_FILE))
            await db.commit()
            framework = (await db.execute(select(Framework))).scalar_one()
            return framework.code, framework.name

    code, fw_name = run(body())
    adapter = get_adapter(code, fw_name)
    assert adapter is not None, code
    assert adapter.spec.assurance.value == "TYPE_1"


def test_reimport_is_idempotent(tmp_path):
    """Re-importing replaces in place rather than duplicating the framework."""
    async def body():
        async with _session(tmp_path) as db:
            service = FrameworkService(db)
            first = await service.import_json(_load(SOC2_FILE))
            await db.commit()
            second = await service.import_json(_load(SOC2_FILE))
            await db.commit()
            frameworks = (await db.execute(select(Framework))).scalars().all()
            controls = (
                await db.execute(
                    select(func.count()).select_from(Control)
                    .where(Control.framework_id == second["framework_id"])
                )
            ).scalar()
            return first["framework_id"], second["framework_id"], len(frameworks), controls

    first_id, second_id, framework_count, controls = run(body())
    assert first_id == second_id
    assert framework_count == 1
    assert controls == 61


def test_control_row_keeps_its_hierarchy_and_cross_refs(tmp_path):
    async def body():
        async with _session(tmp_path) as db:
            await FrameworkService(db).import_json(_load(SOC2_FILE))
            await db.commit()
            control = (await db.execute(
                select(Control).where(Control.code == "CC1.1")
            )).scalar_one()
            return control.statement, control.cross_refs or []

    statement, cross_refs = run(body())
    assert "commitment to integrity" in statement
    # Sub-domain and domain both land in cross_refs, so the report can walk up.
    assert "CC1" in cross_refs
    assert "SECURITY" in cross_refs


def test_both_frameworks_coexist(tmp_path):
    async def body():
        async with _session(tmp_path) as db:
            service = FrameworkService(db)
            for path in (NIST_FILE, SOC2_FILE):
                await service.import_json(_load(path))
            await db.commit()
            return {
                f.code for f in (await db.execute(select(Framework))).scalars().all()
            }

    assert run(body()) == {"NIST_CYBERSECURITY_FRAMEWORK_CSF", "SOC2_TSC_2017"}


def test_both_frameworks_resolve_to_their_own_adapter(tmp_path):
    """Guards against a code that imports but cannot be evaluated."""
    async def body():
        async with _session(tmp_path) as db:
            service = FrameworkService(db)
            for path in (NIST_FILE, SOC2_FILE):
                await service.import_json(_load(path))
            await db.commit()
            rows = (await db.execute(select(Framework))).scalars().all()
            return {f.code: get_adapter(f.code, f.name) for f in rows}

    resolved = run(body())
    keys = {code: (a.key if a else None) for code, a in resolved.items()}
    assert keys == {
        "NIST_CYBERSECURITY_FRAMEWORK_CSF": "nist-csf-2-0",
        "SOC2_TSC_2017": "soc2-type-1",
    }


# ── the drop-in reader's own contract ──────────────────────────────────────

def _flat(control: dict) -> dict:
    """A minimal drop-in dataset: one domain carrying one control directly."""
    return {"framework": {"name": "S", "domains": [
        {"domain_id": "D", "domain_name": "D", "controls": [control]},
    ]}}


def test_every_documented_statement_alias_resolves():
    """The README promises nine aliases; this is what keeps that promise honest."""
    for key in STATEMENT_KEYS:
        canonical = dataset_to_api_canonical(
            _flat({"control_id": "1.1", key: f"text via {key}"}))
        got = canonical["domains"][0]["categories"][0]["controls"][0]["statement"]
        assert got == f"text via {key}", key


def test_control_without_an_identifier_is_rejected_not_dropped():
    """Silently skipping a control would under-report a framework's coverage."""
    with pytest.raises(DatasetFormatError, match="no identifier"):
        dataset_to_api_canonical(_flat({"statement": "text with no id"}))


def test_control_without_a_statement_is_rejected():
    with pytest.raises(DatasetFormatError, match="no statement text"):
        dataset_to_api_canonical(_flat({"control_id": "1.1"}))


def test_bundled_datasets_have_no_control_the_reader_would_reject():
    """The new guard must not reject the files that ship with the platform."""
    expected = {NIST_FILE.name: 106, SOC2_FILE.name: 61}
    for path in (NIST_FILE, SOC2_FILE):
        doc = json.loads(path.read_text(encoding="utf-8"))
        canonical = dataset_to_api_canonical(doc)
        found = sum(
            len(cat["controls"]) for dom in canonical["domains"]
            for cat in dom["categories"]
        )
        assert found == expected[path.name], (path.name, found)
