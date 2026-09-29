"""Deterministic memory-aware classification — the rules, never the model.

The memory workflow has two halves with two distinct owners:

* **Classification** (this module) is a pure function of stored memory. It runs
  first, is reproducible, and is what a test or an auditor can check: given the
  same prior findings and exceptions, the same finding always gets the same
  label. The model never sees a chance to change it.

* **Enrichment** (`enrich.py`) is advisory narration *after* classification:
  how this finding relates to the retrieved history, what changed, whether it
  should be escalated. A failed or unproven enrichment never touches the label.

The policy, stated once (relevant findings are for one organisation, one
framework, one control):

1. An **active** approved exception controls this finding → KNOWN_EXCEPTION.
   The finding is still *recorded*; the exception explains it, it does not
   hide it.
2. The same control+type has now occurred `ESCALATION_THRESHOLD` or more times
   total → ESCALATION_REQUIRED. Repeated failures beyond tolerance are a
   decision for a human, so they are surfaced rather than merely counted.
3. It previously reached a remediated state and is failing again →
   RESOLVED_RECURRING_FINDING.
4. The same control+type has occurred `RECURRING_THRESHOLD` times or more →
   RECURRING_FINDING.
5. At least `PATTERN_THRESHOLD` *distinct* controls in the same domain share
   this finding type → PATTERN_DETECTED. An isolated first finding on one
   control can be part of a wider cluster.
6. Otherwise → NEW_FINDING.

Expired exceptions are deliberately absent from the active set, so an expired
exception drops straight through to the recurrence/escalation rules: the
control is actionable again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from backend.api.compliance.memory.enums import MemoryClassification

#: Defaults mirror config.py; services pass the configured values explicitly.
DEFAULT_RECURRING_THRESHOLD = 2
DEFAULT_ESCALATION_THRESHOLD = 3
DEFAULT_PATTERN_THRESHOLD = 3


@dataclass(frozen=True)
class PriorFinding:
    """One stored memory record of a previous finding on this control."""

    record_id: str
    control_id: str
    finding_type: str
    classification: str
    decision: str | None = None
    status: str | None = None
    remediation_status: str | None = None
    human_feedback: str = ""
    domain_code: str = ""
    domain_name: str = ""
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> "PriorFinding":
        return cls(
            record_id=str(getattr(row, "id", "")),
            control_id=str(getattr(row, "control_id", "")),
            finding_type=str(getattr(row, "finding_type", "")),
            classification=str(getattr(row, "classification", "")),
            decision=getattr(row, "decision", None),
            status=getattr(row, "status", None),
            remediation_status=getattr(row, "remediation_status", None),
            human_feedback=str(getattr(row, "human_feedback", "") or ""),
            domain_code=str(getattr(row, "domain_code", "") or ""),
            domain_name=str(getattr(row, "domain_name", "") or ""),
            created_at=getattr(row, "created_at", None),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "control_id": self.control_id,
            "finding_type": self.finding_type,
            "classification": self.classification,
            "decision": self.decision,
            "status": self.status,
            "remediation_status": self.remediation_status,
            "human_feedback": self.human_feedback,
            "domain_code": self.domain_code,
            "domain_name": self.domain_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class ExceptionView:
    """One stored exception, with whether it currently shields the control."""

    exception_id: str
    control_id: str
    reason: str
    creator: str
    expires_at: datetime | None = None
    status: str = "active"

    @property
    def is_active(self) -> bool:
        if self.status != "active":
            return False
        if self.expires_at is not None and self.expires_at < datetime.utcnow():
            return False
        return True

    @classmethod
    def from_row(cls, row: Any) -> "ExceptionView":
        return cls(
            exception_id=str(getattr(row, "id", "")),
            control_id=str(getattr(row, "control_id", "")),
            reason=str(getattr(row, "reason", "") or ""),
            creator=str(getattr(row, "creator", "") or ""),
            expires_at=getattr(row, "expires_at", None),
            status=str(getattr(row, "status", "") or "active"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "exception_id": self.exception_id,
            "control_id": self.control_id,
            "reason": self.reason,
            "creator": self.creator,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "status": self.status,
            "is_active": self.is_active,
        }


@dataclass(frozen=True)
class Classification:
    """The label plus the evidence trail for why it was chosen.

    `basis` is the list of reasons in priority order — exactly what an auditor
    needs to replay the decision, and what surfaces in the API output under
    `basis`.
    """

    classification: MemoryClassification
    basis: tuple[str, ...] = ()
    occurrence: int = 1
    repeats: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "basis": list(self.basis),
            "occurrence": self.occurrence,
            "repeats": self.repeats,
        }


def classify_finding(
    *,
    finding_type: str,
    prior_findings: Sequence[PriorFinding] = (),
    active_exceptions: Sequence[ExceptionView] = (),
    related_same_type_controls: int = 0,
    recurrence_threshold: int = DEFAULT_RECURRING_THRESHOLD,
    escalation_threshold: int = DEFAULT_ESCALATION_THRESHOLD,
    pattern_threshold: int = DEFAULT_PATTERN_THRESHOLD,
) -> Classification:
    """Classify a current finding against stored memory.

    `prior_findings` is the same (organisation, control, type) history; it is
    never mixed with other controls' findings because a CC6.1 history says
    nothing about CC8.1. `related_same_type_controls` is the number of *other*
    controls in scope whose memory already carries the same finding type.
    """
    repeats = len(prior_findings)
    occurrence = repeats + 1

    active = [e for e in active_exceptions if e.is_active]
    if active:
        seen = ", ".join(e.exception_id for e in active[:2])
        more = f" (and {len(active) - 1} more)" if len(active) > 2 else ""
        return Classification(
            classification=MemoryClassification.KNOWN_EXCEPTION,
            basis=(
                f"An active exception for this control ({seen}{more}) explains "
                "the finding; it is recorded but not escalated.",
            ),
            occurrence=occurrence,
            repeats=repeats,
        )

    if occurrence >= escalation_threshold:
        return Classification(
            classification=MemoryClassification.ESCALATION_REQUIRED,
            basis=(
                f"This control has now occurred {occurrence} times with finding "
                f"type '{finding_type}' (threshold: {escalation_threshold}); "
                "repeated failure beyond tolerance, escalation required.",
            ),
            occurrence=occurrence,
            repeats=repeats,
        )

    remediated = [p for p in prior_findings
                  if p.remediation_status and p.remediation_status == "remediated"]
    if remediated:
        return Classification(
            classification=MemoryClassification.RESOLVED_RECURRING_FINDING,
            basis=(
                "This finding was previously remediated and is failing again — "
                "a regression that this recurrence makes visible.",
            ),
            occurrence=occurrence,
            repeats=repeats,
        )

    if occurrence >= recurrence_threshold:
        return Classification(
            classification=MemoryClassification.RECURRING_FINDING,
            basis=(
                f"This control has occurred {occurrence} times with finding type "
                f"'{finding_type}' (recurrence threshold: {recurrence_threshold}); "
                "same finding, again.",
            ),
            occurrence=occurrence,
            repeats=repeats,
        )

    if related_same_type_controls >= pattern_threshold:
        return Classification(
            classification=MemoryClassification.PATTERN_DETECTED,
            basis=(
                f"{related_same_type_controls} other controls in the same domain "
                f"share finding type '{finding_type}' (pattern threshold: "
                f"{pattern_threshold}); this is part of a cluster, not an "
                "isolated gap.",
            ),
            occurrence=occurrence,
            repeats=repeats,
        )

    return Classification(
        classification=MemoryClassification.NEW_FINDING,
        basis=("No stored memory shows this control failing this way before.",),
        occurrence=occurrence,
        repeats=repeats,
    )


@dataclass(frozen=True)
class MemoryContext:
    """Everything the enrichment prompt is allowed to see about the past.

    `previous_findings` is control-specific history; `related_findings` is the
    same finding type across other controls in the same domain (for pattern
    work); `active_exceptions` / `expired_exceptions` cover this control only.
    """

    previous_findings: tuple[PriorFinding, ...] = ()
    related_findings: tuple[PriorFinding, ...] = ()
    active_exceptions: tuple[ExceptionView, ...] = ()
    expired_exceptions: tuple[ExceptionView, ...] = ()
    remediated_prior: tuple[PriorFinding, ...] = ()

    @property
    def has_memory(self) -> bool:
        return bool(
            self.previous_findings or self.related_findings
            or self.active_exceptions or self.expired_exceptions
        )

    def summary(self) -> str:
        """One-line human summary of what memory was present."""
        parts: list[str] = []
        if self.previous_findings:
            parts.append(f"{len(self.previous_findings)} prior finding(s) on this control")
        if self.active_exceptions:
            parts.append(f"{len(self.active_exceptions)} active exception(s)")
        if self.expired_exceptions:
            parts.append(f"{len(self.expired_exceptions)} expired exception(s)")
        if self.remediated_prior:
            parts.append(f"{len(self.remediated_prior)} previously remediated")
        if self.related_findings:
            controls = len({f.control_id for f in self.related_findings})
            parts.append(f"{len(self.related_findings)} related finding(s) across {controls} control(s)")
        return "; ".join(parts) if parts else "no stored memory for this control"

    def as_prompt_context(self) -> str:
        """Render the memory block that goes into the enrichment prompt.

        Every line is a *stored fact* with a date and, where recorded, the human
        decision — the model may reason about these and about nothing else.
        """
        lines: list[str] = []

        if self.previous_findings:
            lines.append("PREVIOUS FINDINGS ON THIS CONTROL (oldest first):")
            for f in reversed(self.previous_findings):
                lines.append(
                    f"- {self._ts(f)} {f.finding_type} ({f.classification or 'unclassified'}) "
                    f"remediation={f.remediation_status or 'none'}"
                )
                if f.human_feedback:
                    lines.append(f"  human_feedback: {f.human_feedback}")
            lines.append("")

        if self.active_exceptions:
            lines.append("ACTIVE EXCEPTIONS ON THIS CONTROL (approved deviations):")
            for e in self.active_exceptions:
                expiry = e.expires_at.isoformat() if e.expires_at else "no expiry"
                lines.append(
                    f"- exception {e.exception_id} reason: {e.reason} "
                    f"creator: {e.creator} expires: {expiry}"
                )
            lines.append("")

        if self.expired_exceptions:
            lines.append("EXPIRED EXCEPTIONS ON THIS CONTROL (no longer active):")
            for e in self.expired_exceptions:
                lines.append(
                    f"- exception {e.exception_id} reason: {e.reason} "
                    f"expired: {e.expires_at.isoformat() if e.expires_at else 'unknown'}"
                )
            lines.append("")

        if self.related_findings:
            controls = sorted({f.control_id for f in self.related_findings})
            by_control: dict[str, int] = {}
            for f in self.related_findings:
                by_control[f.control_id] = by_control.get(f.control_id, 0) + 1
            lines.append("RELATED FINDINGS (same type, other controls in the domain):")
            lines.append(
                ", ".join(f"{c} x{by_control[c]}" for c in controls) + "."
            )
            lines.append("")

        return "\n".join(lines).strip()

    @staticmethod
    def _ts(f: PriorFinding) -> str:
        return f.created_at.isoformat() if f.created_at else "unknown date"


def assemble_context(
    *,
    previous_findings: Sequence[PriorFinding] = (),
    related_findings: Sequence[PriorFinding] = (),
    exceptions: Sequence[ExceptionView] = (),
) -> MemoryContext:
    """Split stored exceptions into active/expired and hold the rest."""
    active = tuple(e for e in exceptions if e.is_active)
    expired = tuple(e for e in exceptions if not e.is_active)
    remediated = tuple(
        p for p in previous_findings
        if p.remediation_status and p.remediation_status == "remediated"
    )
    return MemoryContext(
        previous_findings=tuple(previous_findings),
        related_findings=tuple(related_findings),
        active_exceptions=active,
        expired_exceptions=expired,
        remediated_prior=remediated,
    )


def matched_memories(context: MemoryContext, *, control_id: str) -> list[str]:
    """Short descriptions of the memory that was actually used.

    This is the "what changed because of memory" surface — the API returns it so
    a reviewer can see exactly which rows influenced the label and the prompt.
    """
    out: list[str] = []
    for f in reversed(context.previous_findings):
        out.append(
            f"prior {f.finding_type} on {f.control_id} "
            f"({f.classification or 'unclassified'}) "
            f"{f.created_at.isoformat() if f.created_at else ''}".rstrip()
        )
    for e in context.active_exceptions:
        out.append(
            f"active exception {e.exception_id} on {e.control_id}: {e.reason}"
        )
    for e in context.expired_exceptions:
        out.append(
            f"expired exception {e.exception_id} on {e.control_id}"
        )
    for f in context.related_findings:
        out.append(
            f"related {f.finding_type} on {f.control_id}"
        )
    for f in context.remediated_prior:
        out.append(f"previously remediated {f.control_id}")
    return out


def finding_types_for_status(statuses: Iterable[str]) -> tuple[str, ...]:
    """The finding types the review workflow records for a set of statuses."""
    from backend.api.compliance.memory.enums import FindingType

    out: list[str] = []
    for raw in statuses:
        try:
            out.append(FindingType.from_status(raw).value)
        except ValueError:
            continue
    return tuple(out)


__all__ = [
    "Classification",
    "DEFAULT_ESCALATION_THRESHOLD",
    "DEFAULT_PATTERN_THRESHOLD",
    "DEFAULT_RECURRING_THRESHOLD",
    "ExceptionView",
    "MemoryContext",
    "PriorFinding",
    "assemble_context",
    "classify_finding",
    "finding_types_for_status",
    "matched_memories",
]