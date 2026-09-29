"""Unit tests for the Hindsight memory classifier and context assembly.

These are pure-function tests: no database, no network, no Groq. They pin down
the deterministic rules that, run twice over the same stored history, must
produce the same labels — the property that lets an auditor replay a decision —
and the exception semantics that are the whole point of "expired exceptions do
not shield a control".

Run with:  python -m pytest tests/test_memory.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.api.compliance.memory import (
    ExceptionView,
    MemoryClassification,
    PriorFinding,
    assemble_context,
    classify_finding,
    matched_memories,
    organization_key,
)

NOW = datetime.utcnow()


def prior(*, record_id="r1", control="CC6.1", finding_type="control_failure",
          classification="", remediation_status=None, created_at=NOW):
    return PriorFinding(
        record_id=record_id,
        control_id=control,
        finding_type=finding_type,
        classification=classification,
        remediation_status=remediation_status,
        created_at=created_at,
    )


def exception(*, exception_id="exc-1", control="CC6.1", expires_at=None,
              status="active"):
    return ExceptionView(
        exception_id=exception_id,
        control_id=control,
        reason="transitional supplier migration",
        creator="assessor@x.io",
        expires_at=expires_at,
        status=status,
    )


# ── organisation key ─────────────────────────────────────────────────────────

def test_organization_key_is_case_and_whitespace_normalised():
    assert organization_key("  Acme Corp  ") == "acme corp"
    assert organization_key(None) == ""
    assert organization_key("ACME") == "acme"


# ── first appearance is NEW_FINDING ──────────────────────────────────────────

def test_first_finding_is_new():
    label = classify_finding(finding_type="control_failure")
    assert label.classification is MemoryClassification.NEW_FINDING
    assert label.occurrence == 1
    assert label.repeats == 0
    assert label.basis  # a reason is always given
    assert isinstance(label.basis, tuple)
    assert len(label.basis) == 1  # one phrase, not a char-split string
    assert "No stored memory" in label.basis[0]


def test_finding_type_vocabulary_maps_1_to_1_from_statuses():
    """The finding-type vocabulary is fed by the persisted statuses, so a memory
    record can never drift from the evaluation it was built from."""
    from backend.api.compliance.memory import FindingType

    assert FindingType.from_status("FAIL") is FindingType.CONTROL_FAILURE
    assert FindingType.from_status("PARTIAL") is FindingType.PARTIAL_COVERAGE
    assert FindingType.from_status("INSUFFICIENT_EVIDENCE") is FindingType.EVIDENCE_GAP
    assert FindingType.CONTROL_FAILURE.value == "control_failure"


def test_recurring_at_threshold():
    prev = [prior(), prior()]
    label = classify_finding(
        finding_type="control_failure",
        prior_findings=prev,
        recurrence_threshold=2,
        escalation_threshold=10,  # recurrence observed before escalation here
    )
    assert label.classification is MemoryClassification.RECURRING_FINDING
    assert label.occurrence == 3
    assert label.repeats == 2


def test_not_recurring_below_threshold():
    label = classify_finding(
        finding_type="control_failure",
        prior_findings=[prior()],
        recurrence_threshold=3,
    )
    assert label.classification is MemoryClassification.NEW_FINDING


# ── escalation outranks recurrence ───────────────────────────────────────────

def test_escalation_required_at_threshold_with_remedied_prior_outranked():
    """At/above the escalation threshold the reason must be ESCALATION_REQUIRED,
    even when recurrence 'r' tripped first."""
    prev = [prior(), prior()]
    label = classify_finding(
        finding_type="control_failure",
        prior_findings=prev,
        recurrence_threshold=2,
        escalation_threshold=3,
    )
    assert label.classification is MemoryClassification.ESCALATION_REQUIRED
    assert label.occurrence == 3


# ── regression after remediation ─────────────────────────────────────────────

def test_resolved_recurring_when_prior_remediated():
    prev = [prior(record_id="r1", remediation_status="remediated")]
    label = classify_finding(
        finding_type="control_failure",
        prior_findings=prev,
        recurrence_threshold=2,
    )
    assert label.classification is MemoryClassification.RESOLVED_RECURRING_FINDING


# ── active exception shields while it is active ──────────────────────────────

def test_known_exception_on_active_exception():
    label = classify_finding(
        finding_type="control_failure",
        prior_findings=[prior(), prior()],
        active_exceptions=[exception(expires_at=NOW + timedelta(days=30))],
    )
    assert label.classification is MemoryClassification.KNOWN_EXCEPTION
    # The occurrence is still counted — an exception explains, never hides.
    assert label.occurrence == 3


def test_known_exception_wins_over_escalation():
    """Even at a high occurrence, an ACTIVE exception is the explanation."""
    label = classify_finding(
        finding_type="control_failure",
        prior_findings=[prior() for _ in range(4)],
        active_exceptions=[exception(expires_at=None)],
        escalation_threshold=3,
    )
    assert label.classification is MemoryClassification.KNOWN_EXCEPTION


# ── expired/revoked exceptions do not shield ─────────────────────────────────

def test_expired_exception_not_active():
    expired = exception(expires_at=NOW - timedelta(days=1))
    assert not expired.is_active
    # A status column of 'expired' is never active even with a future date.
    assert not exception(status="expired", expires_at=NOW + timedelta(days=9)).is_active


def test_expired_exception_falls_through_to_escalation():
    exp_past = exception(expires_at=NOW - timedelta(days=1))
    label = classify_finding(
        finding_type="control_failure",
        prior_findings=[prior() for _ in range(3)],
        active_exceptions=[exp_past],
        escalation_threshold=3,
    )
    assert label.classification is MemoryClassification.ESCALATION_REQUIRED


def test_expired_exception_not_shield_when_below_threshold():
    exp_past = exception(expires_at=NOW - timedelta(days=1))
    label = classify_finding(
        finding_type="control_failure",
        prior_findings=[prior()],
        active_exceptions=[exp_past],
        recurrence_threshold=2,
    )
    assert label.classification is MemoryClassification.RECURRING_FINDING


# ── pattern detection ────────────────────────────────────────────────────────

def test_pattern_detected_across_distinct_controls_same_domain():
    related = [
        prior(record_id="a", control="CC6.2", finding_type="control_failure"),
        prior(record_id="b", control="CC6.3", finding_type="control_failure"),
        prior(record_id="c", control="CC6.4", finding_type="control_failure"),
    ]
    label = classify_finding(
        finding_type="control_failure",
        prior_findings=[],   # this control's first occurrence
        related_same_type_controls=len({f.control_id for f in related}),
        pattern_threshold=3,
    )
    assert label.classification is MemoryClassification.PATTERN_DETECTED


def test_no_pattern_below_threshold():
    label = classify_finding(
        finding_type="control_failure",
        related_same_type_controls=1,
        pattern_threshold=3,
    )
    assert label.classification is MemoryClassification.NEW_FINDING


# ── context assembly ─────────────────────────────────────────────────────────

def test_context_splits_active_and_expired_exceptions():
    ctx = assemble_context(
        previous_findings=[prior(record_id="r9")],
        exceptions=[exception(expires_at=None), exception(
            exception_id="exc-old",
            expires_at=NOW - timedelta(days=1),
        )],
    )
    assert len(ctx.active_exceptions) == 1
    assert len(ctx.expired_exceptions) == 1
    assert ctx.has_memory
    assert "1 prior finding(s) on this control" in ctx.summary()


def test_empty_context_has_no_memory():
    ctx = assemble_context()
    assert not ctx.has_memory
    assert ctx.summary() == "no stored memory for this control"
    assert matched_memories(ctx, control_id="CC6.1") == []


def test_matched_memories_lists_what_was_used():
    ctx = assemble_context(
        previous_findings=[prior(record_id="r1", classification="NEW_FINDING")],
        exceptions=[exception(expires_at=None)],
    )
    matched = matched_memories(ctx, control_id="CC6.1")
    assert any("prior control_failure on CC6.1" in line for line in matched)
    assert any("active exception exc-1 on CC6.1: transitional supplier migration" in line
               for line in matched)


def test_prompt_context_renders_memory_as_data():
    ctx = assemble_context(
        previous_findings=[prior(remediation_status="remediated")],
        exceptions=[exception(expires_at=None)],
    )
    text = ctx.as_prompt_context()
    assert "PREVIOUS FINDINGS ON THIS CONTROL" in text
    assert "remediated" in text
    assert "ACTIVE EXCEPTIONS ON THIS CONTROL" in text
    assert "exception exc-1" in text