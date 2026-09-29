"""Closed vocabularies for the Hindsight memory layer.

Like `compliance/enums.py`, every value here is persisted on a row, so treat
the values as a public contract: add members rather than renaming them.
"""
from __future__ import annotations

from enum import Enum


class FindingType(str, Enum):
    """What kind of event a memory record holds.

    The three finding types map 1:1 to the deterministic evaluation statuses
    that raise a `Finding`, so memory and compliance speak the same language.
    The rest are organisational events that are not themselves findings.
    """

    CONTROL_FAILURE = "control_failure"          # evaluation FAIL
    PARTIAL_COVERAGE = "partial"                 # evaluation PARTIAL
    EVIDENCE_GAP = "evidence_gap"                # evaluation INSUFFICIENT_EVIDENCE
    EXCEPTION = "exception"
    REMEDIATION = "remediation"
    RISK_PATTERN = "risk_pattern"
    EVIDENCE_INTERPRETATION = "evidence_interpretation"
    HUMAN_DECISION = "human_decision"

    @classmethod
    def from_status(cls, status: str) -> "FindingType":
        """The finding type a persisted evaluation status produces."""
        mapping = {
            "FAIL": cls.CONTROL_FAILURE,
            "PARTIAL": cls.PARTIAL_COVERAGE,
            "INSUFFICIENT_EVIDENCE": cls.EVIDENCE_GAP,
        }
        try:
            return mapping[status.strip().upper()]
        except KeyError:
            raise ValueError(f"'{status}' does not map to a finding type") from None


class MemoryClassification(str, Enum):
    """The memory-aware label assigned to a finding at review time.

    Decided by the deterministic rules in `rules.py` — never by the model — so
    two reviewers and two runs agree on what a finding *is*. The model may then
    enrich *why*, but it can never change the label.
    """

    NEW_FINDING = "NEW_FINDING"
    KNOWN_EXCEPTION = "KNOWN_EXCEPTION"
    RECURRING_FINDING = "RECURRING_FINDING"
    RESOLVED_RECURRING_FINDING = "RESOLVED_RECURRING_FINDING"
    ESCALATION_REQUIRED = "ESCALATION_REQUIRED"
    PATTERN_DETECTED = "PATTERN_DETECTED"


class MemoryEvent(str, Enum):
    """The verbs a memory record may carry."""

    FINDING_RAISED = "finding_raised"
    FINDING_REVIEWED = "finding_reviewed"
    EXCEPTION_GRANTED = "exception_granted"
    EXCEPTION_EXPIRED = "exception_expired"
    EXCEPTION_REVOKED = "exception_revoked"
    ESCALATED = "escalated"
    REMEDIATION_STARTED = "remediation_started"
    REMEDIATION_COMPLETED = "remediation_completed"
    PATTERN_DETECTED = "pattern_detected"


class RemediationStatus(str, Enum):
    """Where a finding is in its remediation lifecycle.

    Mirrors the vocabulary already used on the `findings` table so the two
    surfaces never disagree about what a status means.
    """

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    REMEDIATED = "remediated"
    ACCEPTED = "accepted"


class ExceptionStatus(str, Enum):
    """Lifecycle of an approved exception."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


class HumanDecision(str, Enum):
    """The decisions a reviewer can record against a finding."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXCEPTION = "exception"
    ESCALATED = "escalated"
    REMEDIATED = "remediated"


__all__ = [
    "ExceptionStatus",
    "FindingType",
    "HumanDecision",
    "MemoryClassification",
    "MemoryEvent",
    "RemediationStatus",
]