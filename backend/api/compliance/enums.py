"""Closed vocabularies for the compliance model.

Every value here is persisted on an evaluation row, so treat them as a public
contract: add members rather than renaming them.
"""
from __future__ import annotations

from enum import Enum


class ComplianceStatus(str, Enum):
    """Outcome of evaluating one control against one requirement.

    `INSUFFICIENT_EVIDENCE` and `NOT_APPLICABLE` are the two "no claim" states.
    They exist so the model can always distinguish "we checked and it failed"
    from "we could not check" — an evaluation can never quietly skip a control.
    """

    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"

    @property
    def is_conclusive(self) -> bool:
        """True when the status carries a positive or negative compliance claim."""
        return self in (ComplianceStatus.PASS, ComplianceStatus.PARTIAL, ComplianceStatus.FAIL)

    @property
    def is_failure(self) -> bool:
        return self in (ComplianceStatus.FAIL, ComplianceStatus.PARTIAL)


class ConfidenceBand(str, Enum):
    """Coarse label over the numeric `confidence` on an evaluation."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    VERY_LOW = "VERY_LOW"

    @classmethod
    def from_score(cls, score: float) -> "ConfidenceBand":
        s = max(0.0, min(1.0, float(score)))
        if s >= 0.75:
            return cls.HIGH
        if s >= 0.50:
            return cls.MEDIUM
        if s >= 0.25:
            return cls.LOW
        return cls.VERY_LOW


class AssuranceLevel(str, Enum):
    """Assurance an evaluation run is permitted to make.

    SOC 2 is deliberately pinned to TYPE_1: design and implementation as of the
    assessment date. TYPE_2 would additionally assert operating effectiveness
    over a period, which this platform does not evaluate and must never imply.
    """

    TYPE_1 = "TYPE_1"
    POINT_IN_TIME = "POINT_IN_TIME"  # NIST CSF is not an assurance engagement


class EvidenceKind(str, Enum):
    """Where an evidence item came from.

    The `source` values match how evidence is already stored in this platform —
    `Evidence` rows attached to a Response, files an owner supplied against a
    DocumentRequest, and anything an operator references explicitly.
    """

    RESPONSE_ATTACHMENT = "response_attachment"
    DOCUMENT_REQUEST = "document_request"
    ENGAGEMENT_DOCUMENT = "engagement_document"
    EXTERNAL = "external"

    @property
    def is_point_in_time(self) -> bool:
        """Design/implementation-grade evidence vs period/operation-grade.

        A policy, a configuration export, an architecture diagram, a walkthrough
        or a screenshot of the current state describe the control *as designed and
        implemented*. Logs, ticket samples, exception reports and other artefacts
        spanning a period describe *operation* — which a Type 1 examination cannot
        conclude on.
        """
        return self in (EvidenceKind.RESPONSE_ATTACHMENT, EvidenceKind.ENGAGEMENT_DOCUMENT)


class Severity(str, Enum):
    """Finding severity, reusing the vocabulary already in the `findings` table."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


#: Canonical ordering for report/sort surfaces (worst first).
STATUS_ORDER: tuple[ComplianceStatus, ...] = (
    ComplianceStatus.FAIL,
    ComplianceStatus.INSUFFICIENT_EVIDENCE,
    ComplianceStatus.PARTIAL,
    ComplianceStatus.NOT_APPLICABLE,
    ComplianceStatus.PASS,
)
