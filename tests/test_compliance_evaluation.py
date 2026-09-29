"""Unit tests for the deterministic compliance evaluation core.

These tests import no database, no vector store and no LLM client: everything
from `status.py` through `evaluator.py` is a pure function of its inputs, which
is the property that makes an evaluation result defensible.

Run with:  python -m pytest tests/test_compliance_evaluation.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.api.compliance import (
    NistCsf20Adapter,
    Soc2Type1Adapter,
    get_adapter,
    list_adapters,
)
from backend.api.compliance.confidence import is_contradictory
from backend.api.compliance.dataset import DatasetFormatError
from backend.api.compliance.enums import AssuranceLevel, ComplianceStatus
from backend.api.compliance.evaluator import evaluate_control, evaluate_controls
from backend.api.compliance.status import calculate_status, grade_evidence
from backend.api.compliance.types import EvidenceRef, RequirementSpec

CANONICAL = Path(__file__).resolve().parents[1] / "backend" / "api" / "data" / "canonical"

NIST_FILE = CANONICAL / "nist_csf_2_0.json"
SOC2_FILE = CANONICAL / "soc2_tsc_2017.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def artefact(evidence_id: str, text: str, name: str = "evidence.txt") -> EvidenceRef:
    return EvidenceRef(evidence_id=evidence_id, text=text, source_name=name, summary="artefact")


OPERATIONAL = (
    "Access review log export dated 2026-01-15: quarterly user access review "
    "completed, audit log shows 100% of entitlements reviewed by the security "
    "owner, screenshot of the SIEM dashboard attached."
)
DESIGN = (
    "Walkthrough of CC1.1 on 2026-02-01. Current-state architecture diagram and "
    "code of conduct with approval record and revision history; effective date "
    "2025-11-01. The entity demonstrates a commitment to integrity and ethical "
    "values."
)
PERIOD = (
    "Sample of 25 user access tickets drawn from the population of 4,200 tickets "
    "throughout the period, with exception report showing 3 exceptions."
)
CONTRADICTORY = (
    "Access control policy is not implemented for the data platform. Policy is "
    "not in place and no procedure has been approved."
)
OFF_TOPIC = (
    "A short note was provided by the team about this control area for reference "
    "purposes only."
)


@pytest.fixture(scope="module")
def nist() -> NistCsf20Adapter:
    return NistCsf20Adapter()


@pytest.fixture(scope="module")
def soc2() -> Soc2Type1Adapter:
    return Soc2Type1Adapter()


@pytest.fixture(scope="module")
def nist_controls(nist):
    return nist.build_controls(load(NIST_FILE))


@pytest.fixture(scope="module")
def soc2_controls(soc2):
    return soc2.build_controls(load(SOC2_FILE))


# ── dataset reading ─────────────────────────────────────────────────────────

def test_nist_dataset_reads_all_controls(nist_controls):
    assert len(nist_controls) == 106


def test_soc2_dataset_reads_all_controls(soc2_controls):
    assert len(soc2_controls) == 61


def test_every_control_resolves_a_requirement(nist_controls, soc2_controls):
    for control in [*nist_controls, *soc2_controls]:
        assert control.requirement.text.strip()
        assert control.requirement.requirement_id.strip()
        assert control.criticality in ("critical", "standard", "informational")


def test_non_dataset_file_is_rejected(nist, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"framework": {"domains": "not-a-list"}}), encoding="utf-8")
    with pytest.raises(DatasetFormatError):
        nist.read_dataset(load(bad))


# ── framework scope and assurance ───────────────────────────────────────────

def test_only_nist_and_soc2_are_supported():
    assert {a.key for a in list_adapters()} == {"nist-csf-2-0", "soc2-type-1"}


def test_unsupported_framework_has_no_adapter():
    assert get_adapter("iso-27001-2022") is None
    assert get_adapter("CIS Controls v8.1.2") is None


def test_soc2_is_pinned_to_type_1(soc2):
    assert soc2.spec.assurance is AssuranceLevel.TYPE_1
    assert "NO assertion" in soc2.spec.assurance_statement


def test_nist_is_not_an_assurance_engagement(nist):
    assert nist.spec.assurance is AssuranceLevel.POINT_IN_TIME


# ── the evidence gate: no evidence, no compliance claim ─────────────────────

@pytest.mark.parametrize("evidence", [
    [],
    [EvidenceRef(evidence_id="E1", source_name="policy.pdf")],  # filename only
    [artefact("E1", "   ")],                                   # whitespace only
])
def test_no_readable_evidence_can_never_conclude_compliance(nist, nist_controls, evidence):
    for value, score in (("yes", 1.0), ("", 0.0), (None, None)):
        decision = calculate_status(
            adapter=nist, control_id="GV.OC-01", response_value=value,
            response_score=score, evidence=evidence,
        )
        assert decision.status is ComplianceStatus.INSUFFICIENT_EVIDENCE, (
            f"evidence={[e.source_name for e in evidence]} value={value!r}"
        )
        assert not decision.status.is_conclusive


def test_filename_only_evidence_is_treated_as_ungraded(nist, nist_controls):
    decision = calculate_status(
        adapter=nist, control_id="GV.OC-01", response_value="yes",
        response_score=1.0, evidence=[EvidenceRef(evidence_id="E1", source_name="policy.pdf")],
    )
    assert decision.evidence_score == 0.0


# ── not applicable ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["N/A", "n/a", "not applicable", "NotApplicable"])
def test_not_applicable_overrides_everything(nist, nist_controls, value):
    decision = calculate_status(
        adapter=nist, control_id="GV.OC-01", response_value=value, response_score=1.0,
        evidence=[artefact("E1", OPERATIONAL)],
    )
    assert decision.status is ComplianceStatus.NOT_APPLICABLE
    assert not decision.status.is_conclusive


# ── thresholds ──────────────────────────────────────────────────────────────

def test_strong_evidence_passes(nist, nist_controls):
    result = evaluate_control(
        adapter=nist, control=nist_controls[0],
        evidence=[artefact("E1", OPERATIONAL), artefact("E2", OPERATIONAL)],
        response_value="yes", response_score=1.0,
    )
    assert result.status is ComplianceStatus.PASS
    assert result.confidence_band.value == "HIGH"


def test_evidence_without_an_answer_cannot_pass(nist, nist_controls):
    result = evaluate_control(
        adapter=nist, control=nist_controls[0], evidence=[artefact("E1", OPERATIONAL)],
        response_value=None, response_score=None,
    )
    assert result.status is ComplianceStatus.PARTIAL


def test_weak_evidence_cannot_pass_on_a_confident_answer(nist, nist_controls):
    """The 50/50 blend alone would put this exactly on the threshold."""
    result = evaluate_control(
        adapter=nist, control=nist_controls[0], evidence=[artefact("E1", OFF_TOPIC)],
        response_value="yes", response_score=1.0,
    )
    assert result.status is ComplianceStatus.PARTIAL
    assert "below the 60%" in result.reasoning or "evidence itself grades" in result.reasoning


def test_contradicting_evidence_cannot_pass(nist, nist_controls):
    result = evaluate_control(
        adapter=nist, control=nist_controls[0], evidence=[artefact("E1", CONTRADICTORY)],
        response_value="yes", response_score=1.0,
    )
    assert result.status is not ComplianceStatus.PASS
    assert result.score <= 0.35


# ── SOC 2 Type 1 enforcement ───────────────────────────────────────────────

def test_period_only_evidence_cannot_reach_pass_on_soc2(soc2, soc2_controls):
    result = evaluate_control(
        adapter=soc2, control=soc2_controls[0], evidence=[artefact("E1", PERIOD)],
        response_value="yes", response_score=1.0,
    )
    assert result.status is not ComplianceStatus.PASS
    assert result.assurance_level == AssuranceLevel.TYPE_1.value
    assert any("Type 2" in g for g in result.gaps)


def test_design_evidence_can_pass_on_soc2(soc2, soc2_controls):
    result = evaluate_control(
        adapter=soc2, control=soc2_controls[0],
        evidence=[artefact("E1", DESIGN), artefact("E2", DESIGN)],
        response_value="yes", response_score=1.0,
    )
    assert result.status is ComplianceStatus.PASS


def test_mixed_evidence_is_treated_as_point_in_time(soc2):
    assert soc2.classify_temporality(DESIGN) == "point_in_time"
    assert soc2.classify_temporality(PERIOD) == "period"
    # A policy describing a quarterly cadence is still design evidence.
    assert soc2.classify_temporality(
        "Access review policy requiring quarterly review throughout the period."
    ) == "point_in_time"


def test_soc2_recommendation_is_type_1_specific(soc2, soc2_controls):
    result = evaluate_control(
        adapter=soc2, control=soc2_controls[0], evidence=[artefact("E1", PERIOD)],
        response_value="yes", response_score=1.0,
    )
    # The Type 1 guidance, not the generic "collect an artefact" default.
    assert "Type 1" in result.recommendation
    assert "Type 2" in result.recommendation


def test_soc2_never_recommends_period_evidence_for_a_type_1_pass(soc2, soc2_controls):
    result = evaluate_control(
        adapter=soc2, control=soc2_controls[0], evidence=[artefact("E1", PERIOD)],
        response_value="yes", response_score=1.0,
    )
    assert "Type 2" in result.recommendation or result.recommendation == ""


# ── contradiction detection ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Access control policy is not implemented.",
    "The control is not configured for the data platform.",
    "There is no formal access control policy in place.",
    "The control has not been implemented or documented.",
    "MFA is disabled and logging is missing.",
])
def test_negated_evidence_is_a_contradiction(text):
    assert is_contradictory("yes", text) is True


@pytest.mark.parametrize("text", [
    "The disabled-account list is configured and reviewed by the security team.",
    "Access control policy is implemented and the procedure is in place.",
    "MFA is enabled, the procedure is documented and the control is active.",
])
def test_affirmative_evidence_is_not_a_contradiction(text):
    assert is_contradictory("yes", text) is False


def test_contradiction_requires_an_affirmative_answer():
    assert is_contradictory("no", "The control is not implemented.") is False


# ── result contract ─────────────────────────────────────────────────────────

def test_result_carries_every_required_field(nist, nist_controls):
    result = evaluate_control(
        adapter=nist, control=nist_controls[0], evidence=[artefact("E1", OPERATIONAL)],
        response_value="yes", response_score=1.0,
    )
    payload = result.as_dict()
    for field in (
        "framework", "control_id", "requirement", "evidence_id", "evidence_summary",
        "status", "confidence", "reasoning", "gaps", "recommendation", "evaluated_at",
    ):
        assert field in payload, field
    assert payload["framework"] == "nist-csf-2-0"
    assert payload["status"] in {s.value for s in ComplianceStatus}


def test_evaluation_is_deterministic(nist, nist_controls):
    args = dict(adapter=nist, control=nist_controls[0], evidence=[artefact("E1", OPERATIONAL)],
                response_value="yes", response_score=1.0)
    first = evaluate_control(**args).as_dict()
    second = evaluate_control(**args).as_dict()
    first.pop("evaluated_at")
    second.pop("evaluated_at")
    assert first == second


# ── batch evaluation covers the whole framework ─────────────────────────────

def test_batch_evaluation_never_skips_a_control(nist, nist_controls):
    results = evaluate_controls(adapter=nist, controls=nist_controls)
    assert len(results) == len(nist_controls) == 106
    assert all(r.status is not None for r in results)
    assert sum(1 for r in results if r.status is ComplianceStatus.INSUFFICIENT_EVIDENCE) == 106


def test_batch_evaluation_with_evidence_is_conclusive(nist, nist_controls):
    results = evaluate_controls(
        adapter=nist, controls=nist_controls,
        evidence_by_control={c.control_id: [artefact("E1", OPERATIONAL)] for c in nist_controls},
        response_by_control={c.control_id: ("yes", 1.0) for c in nist_controls},
    )
    assert all(r.status is ComplianceStatus.PASS for r in results)


def test_requirement_spec_round_trips():
    spec = RequirementSpec(requirement_id="CC1", text="objective", clauses=("a", "b"))
    assert spec.as_dict() == {
        "requirement_id": "CC1", "text": "objective", "clauses": ["a", "b"],
    }


# ── integration with the existing framework importer ────────────────────────
# Without this branch the bundled files import 0 controls: `_is_root_canonical`
# rejects the dataset shape and the API shape needs a top-level `code`.

@pytest.mark.parametrize("path,code,controls", [
    (NIST_FILE, "NIST_CYBERSECURITY_FRAMEWORK_CSF", 106),
    (SOC2_FILE, "SOC2_TSC_2017", 61),
])
def test_bundled_datasets_import_through_the_existing_service(path, code, controls):
    from backend.api.services.framework_service import _normalise_canonical

    canonical = _normalise_canonical(load(path))
    assert canonical["code"] == code
    assert code.replace("_", "").isalnum(), "framework code is used in a URL path"
    got = sum(
        len(cat["controls"])
        for dom in canonical["domains"]
        for cat in dom["categories"]
    )
    assert got == controls


def test_framework_code_resolves_to_its_adapter():
    """The stored Framework.code is what the service looks an adapter up by."""
    from backend.api.services.framework_service import _normalise_canonical

    for path, expected in ((NIST_FILE, "nist-csf-2-0"), (SOC2_FILE, "soc2-type-1")):
        canonical = _normalise_canonical(load(path))
        adapter = get_adapter(canonical["code"], canonical["name"])
        assert adapter is not None, canonical["code"]
        assert adapter.key == expected


def test_dataset_metadata_survives_normalisation(soc2_controls):
    """points_of_focus is what the SOC 2 adapter resolves requirement clauses from."""
    cc11 = next(c for c in soc2_controls if c.control_id == "CC1.1")
    assert cc11.requirement.clauses
    assert "commitment" in cc11.requirement.clauses[0].lower()


def test_nist_criticality_matches_the_existing_engine(nist, nist_controls):
    """The compliance layer must not disagree with the published report."""
    from backend.core.evaluation_metrics import _nist_criticality

    for control in nist_controls:
        assert control.criticality == _nist_criticality(control.control_id)
