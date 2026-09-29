"""Closed vocabularies for the compliance model.

Every value here is persisted on an evaluation row, so treat them as a public
contract: add members rather than renaming them.
"""
from __future__ import annotations

from enum import Enum


class ComplianceStatus(str, Enum):
    """Outcome of evaluating one control against one requirement.

    Four "no claim" states keep the model honest about what has actually been
    established, and they are deliberately distinct rather than collapsed:

    * `NOT_EVALUATED` — the control is in scope and no evaluation has been run.
    * `INSUFFICIENT_EVIDENCE` — it was evaluated, and no readable evidence exists,
      so nothing can be concluded either way.
    * `REVIEW_REQUIRED` — it was evaluated, but the evidence-to-control mapping
      was below the auto-accept threshold, so a human has to confirm the
      mapping before the reading stands.
    * `NOT_APPLICABLE` — a scoping decision, not a gap.

    Only PASS / PARTIAL / FAIL are claims. Everything else says *we are not
    concluding anything here*, which is what lets the model always distinguish
    "we checked and it failed" from "we could not check" — an evaluation can
    never quietly skip a control.
    """

    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"

    @property
    def is_conclusive(self) -> bool:
        """True when the status carries a positive or negative compliance claim."""
        return self in (ComplianceStatus.PASS, ComplianceStatus.PARTIAL, ComplianceStatus.FAIL)

    @property
    def is_failure(self) -> bool:
        return self in (ComplianceStatus.FAIL, ComplianceStatus.PARTIAL)

    @property
    def is_evaluated(self) -> bool:
        """False only for `NOT_EVALUATED`.

        `INSUFFICIENT_EVIDENCE` *is* an evaluated outcome: the engine ran and
        correctly reported that it had nothing to grade. Collapsing the two would
        hide a real gap behind a blank row.
        """
        return self is not ComplianceStatus.NOT_EVALUATED

    @property
    def requires_review(self) -> bool:
        """True when a human must confirm something before the row is a claim."""
        return self is ComplianceStatus.REVIEW_REQUIRED

    @property
    def is_gap(self) -> bool:
        """True for every status that means "there is work outstanding here".

        Drives coverage denominators and the remediation backlog. `NOT_APPLICABLE`
        is excluded on purpose: an out-of-scope control is not a gap.
        """
        return self in (
            ComplianceStatus.FAIL,
            ComplianceStatus.PARTIAL,
            ComplianceStatus.INSUFFICIENT_EVIDENCE,
            ComplianceStatus.REVIEW_REQUIRED,
            ComplianceStatus.NOT_EVALUATED,
        )


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
    #: A contributor's answer to a targeted evidence-gap question. Its own kind
    #: so a report can say "this was asserted, not shown" and so the narrative
    #: evidence mix is visible: a control whose whole basis is answers is
    #: differently evidenced from one backed by artefacts, even at the same
    #: score.
    GAP_ANSWER = "gap_answer"
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

    @property
    def is_narrative(self) -> bool:
        """True for evidence a person wrote rather than a system produced."""
        return self in (EvidenceKind.GAP_ANSWER, EvidenceKind.EXTERNAL)


class EvidenceNature(str, Enum):
    """What kind of claim an artefact can support.

    The three natures are ordered by how much they can prove, and the ordering
    is the whole point: an artefact must be classified before a mapping is
    accepted, because attaching an *operational* sample to a control and
    concluding operating effectiveness from it is exactly the mistake a SOC 2
    Type 1 examination must not make.

    * `POLICY_DESIGN` — the control is documented as designed. A policy, a
      standard, a procedure, a data-flow diagram.
    * `TECHNICAL_IMPLEMENTATION` — the control is shown as implemented now. A
      configuration export, a rule set, a screenshot of the live console, a
      architecture walkthrough.
    * `OPERATIONAL_ACTIVITY` — the control is shown as operating over a period.
      Logs, ticket samples, alert history, exception reports.
    * `UNDETERMINED` — could not be classified. Never a licence to conclude:
      an artefact of unknown nature may only be attached, never relied on.
    """

    POLICY_DESIGN = "POLICY_DESIGN"
    TECHNICAL_IMPLEMENTATION = "TECHNICAL_IMPLEMENTATION"
    OPERATIONAL_ACTIVITY = "OPERATIONAL_ACTIVITY"
    UNDETERMINED = "UNDETERMINED"

    @property
    def strength(self) -> int:
        """Ordinal strength, highest is strongest. Used for sorting and for
        the "best nature on this control" roll-up."""
        return {
            EvidenceNature.UNDETERMINED: 0,
            EvidenceNature.POLICY_DESIGN: 1,
            EvidenceNature.TECHNICAL_IMPLEMENTATION: 2,
            EvidenceNature.OPERATIONAL_ACTIVITY: 3,
        }[self]

    @property
    def is_point_in_time(self) -> bool:
        """True when the artefact can support a Type 1 / point-in-time claim.

        Operational activity describes a *period*, which is a Type 2 question.
        A Type 1 examination concludes on design and implementation as of the
        assessment date, so operational artefacts are recorded and reported but
        cannot carry a PASS on their own.
        """
        return self in (
            EvidenceNature.POLICY_DESIGN,
            EvidenceNature.TECHNICAL_IMPLEMENTATION,
        )

    @property
    def label(self) -> str:
        return {
            EvidenceNature.POLICY_DESIGN: "Policy / design",
            EvidenceNature.TECHNICAL_IMPLEMENTATION: "Technical implementation",
            EvidenceNature.OPERATIONAL_ACTIVITY: "Operational activity",
            EvidenceNature.UNDETERMINED: "Undetermined",
        }[self]


class Severity(str, Enum):
    """Finding severity, reusing the vocabulary already in the `findings` table."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class MappingReviewStatus(str, Enum):
    """Human-in-the-loop state of one evidence-to-control mapping.

    A mapping is never automatically *true* just because it was scored. It is
    `AUTO_ACCEPTED` (scored above the threshold, treated as a candidate) or it is
    `PENDING_REVIEW` (below the threshold, and the control's status is held at
    REVIEW_REQUIRED until a human says yes).
    """

    AUTO_ACCEPTED = "auto_accepted"
    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"

    @property
    def is_settled(self) -> bool:
        """True once no human input is outstanding."""
        return self in (MappingReviewStatus.CONFIRMED, MappingReviewStatus.REJECTED)


#: Canonical ordering for report/sort surfaces (worst first).
#: `NOT_EVALUATED` leads because "we have not looked yet" is the state a
#: coverage report most needs to surface — it is the denominator, not a failure.
STATUS_ORDER: tuple[ComplianceStatus, ...] = (
    ComplianceStatus.FAIL,
    ComplianceStatus.NOT_EVALUATED,
    ComplianceStatus.INSUFFICIENT_EVIDENCE,
    ComplianceStatus.REVIEW_REQUIRED,
    ComplianceStatus.PARTIAL,
    ComplianceStatus.NOT_APPLICABLE,
    ComplianceStatus.PASS,
)
