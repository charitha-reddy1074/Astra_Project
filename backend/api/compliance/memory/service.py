"""DB orchestration for the Hindsight memory layer.

The division of labour:

* **classification** (`rules.py`) is deterministic and pure — given the stored
  history it always returns the same label, which is what an audit trail needs.
* **enrichment** (`enrich.py`) is advisory and contained — a model summarises
  how this finding relates to the history *after* the label exists, and its
  failure can never change the label (or a compliance status).
* **this module** does the orchestrating: loads the assessment and its recorded
  evaluations, reads the organisation's stored memory, classifies each finding,
  optionally asks the model to enrich it, and appends the result to memory.

The organisation key is the isolation boundary. All queries filter by it, so one
organisation's history is never offered to another — this is the "organisation
data is isolated" property tests and auditors depend on.

`expires_at` on an exception is enforced in two places that agree: lazily (a
batch UPDATE so the status column reads honestly) and deterministically in
`ExceptionView.is_active` (so a not-yet-updated row still cannot count as
active). Both keep an expired exception from shielding a control.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.config import settings
from backend.api.compliance.memory import enrich as enrich_module
from backend.api.compliance.memory.enums import (
    FindingType,
    MemoryEvent,
    RemediationStatus,
)
from backend.api.compliance.memory.enrich import MemoryEnricher
from backend.api.compliance.memory.rules import (
    ExceptionView,
    MemoryContext,
    PriorFinding,
    assemble_context,
    classify_finding,
    matched_memories,
)
from backend.api.compliance.service import (
    ComplianceService,
    load_evaluable_framework,
)
from backend.api.models.assessment import Assessment
from backend.api.models.compliance import ComplianceEvaluation
from backend.api.models.memory import ComplianceException, MemoryRecord

logger = logging.getLogger(__name__)

#: Finding statuses that raise a finding — mirrors compliance/service.py. A
#: PASS is not memory-worthy and NOT_APPLICABLE is a scoping decision, not a gap.
_FINDING_STATUSES = frozenset({"FAIL", "PARTIAL", "INSUFFICIENT_EVIDENCE"})


class MemoryError(RuntimeError):
    """Base error for the Hindsight surface."""


@dataclass
class MemoryReviewRun:
    """The outcome of reviewing a recorded evaluation with stored memory."""

    assessment_id: str
    framework: str
    organization_id: str
    items: list[dict[str, Any]] = field(default_factory=list)
    #: Rows that were already stored (a re-review of the same evaluation row)
    #: and so were not written again.
    skipped: int = 0

    @property
    def recorded(self) -> int:
        return len(self.items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "framework": self.framework,
            "organization_id": self.organization_id,
            "reviewed": self.recorded + self.skipped,
            "recorded": self.recorded,
            "skipped": self.skipped,
            "items": self.items,
        }


def organization_key(organization: str | None) -> str:
    """Normalise an organisation name into the memory isolation key.

    The same key an assessment review uses, so the read endpoints accept
    exactly what a caller would pass after reading an assessment.
    """
    key = (organization or "").strip().lower()
    return key


class MemoryService:
    """Persist and read organisational compliance memory."""

    def __init__(self, db: AsyncSession, **overrides: Any):
        self.db = db
        self.recurring_threshold = int(
            overrides.get("recurring_threshold", settings.MEMORY_RECURRING_THRESHOLD)
        )
        self.escalation_threshold = int(
            overrides.get("escalation_threshold", settings.MEMORY_ESCALATION_THRESHOLD)
        )
        self.pattern_threshold = int(
            overrides.get("pattern_threshold", settings.MEMORY_PATTERN_THRESHOLD)
        )

    # ── helpers ─────────────────────────────────────────────────────────────

    def _org_key(self, assessment: Assessment) -> str:
        """The organization key for an assessment's memory.

        Falls back to a safely namespaced `assessment:<id>` when an assessment
        carries no organisation at all, so memory is still isolated (by
        assessment) rather than merged into a shared bucket.
        """
        return organization_key(assessment.organization) or f"assessment:{assessment.id}"

    async def _expire_lazy(self) -> int:
        """Flip actively-past-due exceptions to `expired`, returning the count.

        Deterministic enforcement lives in `ExceptionView.is_active`; this keeps
        the `status` column reporting honestly as well, and runs at the start of
        any path that surfaces exceptions.
        """
        result = await self.db.execute(
            update(ComplianceException)
            .where(
                ComplianceException.status == "active",
                ComplianceException.expires_at.isnot(None),
                ComplianceException.expires_at < datetime.utcnow(),
            )
            .values(status="expired")
        )
        return int(result.rowcount or 0)

    async def _prior_findings(
        self, org: str, framework: str, control_id: str, finding_type: str
    ) -> list[PriorFinding]:
        rows = (await self.db.execute(
            select(MemoryRecord)
            .where(
                MemoryRecord.organization_id == org,
                MemoryRecord.framework == framework,
                MemoryRecord.control_id == control_id,
                MemoryRecord.finding_type == finding_type,
            )
            .order_by(MemoryRecord.created_at.asc())
        )).scalars().all()
        return [PriorFinding.from_row(r) for r in rows]

    async def _exceptions(
        self, org: str, framework: str, control_id: str
    ) -> list[ExceptionView]:
        rows = (await self.db.execute(
            select(ComplianceException)
            .where(
                ComplianceException.organization_id == org,
                ComplianceException.framework == framework,
                ComplianceException.control_id == control_id,
            )
            .order_by(ComplianceException.created_at.asc())
        )).scalars().all()
        return [ExceptionView.from_row(r) for r in rows]

    async def _related_findings(
        self,
        org: str,
        framework: str,
        control_id: str,
        domain_code: str | None,
        finding_type: str,
    ) -> list[PriorFinding]:
        """Same-type findings on *other* controls in the organisation.

        Scoped to the same domain when the reviewed control has one, so a CC6.x
        pattern is credited as one cluster and a CC6.x + CC8.x pair is not.
        """
        rows = (await self.db.execute(
            select(MemoryRecord)
            .where(
                MemoryRecord.organization_id == org,
                MemoryRecord.framework == framework,
                MemoryRecord.finding_type == finding_type,
                MemoryRecord.control_id != control_id,
            )
            .order_by(MemoryRecord.created_at.asc())
        )).scalars().all()
        out = [PriorFinding.from_row(r) for r in rows]
        if domain_code:
            out = [f for f in out if f.domain_code == domain_code]
        return out

    @staticmethod
    def _evidence_text(row: ComplianceEvaluation) -> str:
        refs = row.evidence_refs or []
        parts = [
            str(ref.get("text", "")) for ref in refs
            if isinstance(ref, dict) and str(ref.get("text", "")).strip()
        ]
        text = "\n".join(parts)
        return text[:4000] or "No readable evidence text recorded."

    # ── the review workflow ─────────────────────────────────────────────────

    async def review(
        self,
        assessment_id: str,
        framework_id: str,
        *,
        control_codes: Sequence[str] | None = None,
        model_inference: bool = True,
        actor: str | None = None,
        llm_client: Any = None,
    ) -> MemoryReviewRun:
        """Classify recorded finding evaluations against stored memory.

        Deterministic: run it twice with the same history and you get the same
        labels. That is the property the two new governance paths rely on —
        a re-review never silently changes a conclusion. `llm_client` is
        injectable so tests drive enrichment with a stub.
        """
        from backend.api.repositories.assessment_repo import AssessmentRepository

        assessment = await AssessmentRepository(self.db).get_by_id(assessment_id)
        if assessment is None:
            raise MemoryError(f"Assessment {assessment_id} not found.")
        framework, adapter = await load_evaluable_framework(self.db, framework_id)
        org = self._org_key(assessment)
        fw_key = adapter.key

        await self._expire_lazy()

        rows = await ComplianceService(self.db).list_evaluations(
            assessment_id, current_only=True, framework=fw_key
        )
        if control_codes:
            wanted = {c.strip().upper() for c in control_codes}
            rows = [r for r in rows if (r.control_code or "").strip().upper() in wanted]

        finding_rows = [r for r in rows if r.status in _FINDING_STATUSES]
        enricher: MemoryEnricher | None = None
        if model_inference and str(settings.MEMORY_ENRICH_ENABLED).lower() in (
            "1", "true", "yes", "on"
        ):
            enricher = MemoryEnricher(llm_client=llm_client)

        items: list[dict[str, Any]] = []
        skipped = 0
        for row in finding_rows:
            item = await self._review_one(
                row=row, org=org, fw_key=fw_key, enricher=enricher, actor=actor,
            )
            if item is None:
                skipped += 1
            else:
                items.append(item)

        return MemoryReviewRun(
            assessment_id=assessment_id,
            framework=fw_key,
            organization_id=org,
            items=items,
            skipped=skipped,
        )

    async def _review_one(
        self,
        *,
        row: ComplianceEvaluation,
        org: str,
        fw_key: str,
        enricher: MemoryEnricher | None,
        actor: str | None,
    ) -> dict[str, Any] | None:
        code = (row.control_code or "").strip().upper()

        # Dedup guard: one record per (evaluation, control). Re-running the
        # review over the same recorded evaluation is a no-op, so an accidental
        # double-click cannot fabricate a second "occurrence".
        existing = (await self.db.execute(
            select(MemoryRecord).where(
                MemoryRecord.evaluation_id == row.id,
                MemoryRecord.control_id == code,
            )
        )).scalars().all()
        if existing:
            return None

        finding_type = FindingType.from_status(row.status).value

        prior = await self._prior_findings(org, fw_key, code, finding_type)
        exceptions = await self._exceptions(org, fw_key, code)
        related = await self._related_findings(
            org, fw_key, code, row.domain_code, finding_type
        )
        related_controls = len({f.control_id for f in related})

        label = classify_finding(
            finding_type=finding_type,
            prior_findings=prior,
            active_exceptions=exceptions,
            related_same_type_controls=related_controls,
            recurrence_threshold=self.recurring_threshold,
            escalation_threshold=self.escalation_threshold,
            pattern_threshold=self.pattern_threshold,
        )
        context = assemble_context(
            previous_findings=prior, related_findings=related, exceptions=exceptions
        )
        matched = matched_memories(context, control_id=code)

        enrichment_meta: dict[str, Any] = {}
        if enricher is not None and context.has_memory:
            deterministic = {
                "status": row.status,
                "confidence": round(float(row.confidence or 0.0), 3),
                "classification": label.classification.value,
                "assurance_level": row.assurance_level or "POINT_IN_TIME",
            }
            try:
                _enrichment, outcome = enricher.enrich(
                    control_id=code,
                    context=context,
                    deterministic=deterministic,
                    evidence_text=self._evidence_text(row),
                )
            except Exception as exc:  # noqa: BLE001 - contained, advisory only
                logger.warning("memory enrichment failed for %s: %s", code, exc)
                outcome = {"enrichment": None, "error": f"enrichment failed: {exc}"}
            enrichment_meta = outcome
        elif enricher is not None:
            enrichment_meta = {
                "enrichment": None, "error": "no stored memory to enrich against",
            }

        # Pydantic objects are not JSON-serialisable, and the JSON column stores
        # a dict. `.as_dict()` is the wire shape of the enrichment contract.
        _enrichment = enrichment_meta.get("enrichment")
        enrichment_value = _enrichment.as_dict() if _enrichment is not None else None

        record = MemoryRecord(
            organization_id=org,
            assessment_id=row.assessment_id,
            evaluation_id=row.id,
            framework=fw_key,
            framework_code=row.framework_code,
            control_id=code,
            domain_code=row.domain_code or None,
            domain_name=row.domain_name or None,
            finding_type=finding_type,
            event=MemoryEvent.FINDING_RAISED.value,
            classification=label.classification.value,
            decision=None,
            occurrence=label.occurrence,
            severity=row.severity or "medium",
            status=row.status,
            human_feedback="",
            remediation_status=RemediationStatus.OPEN.value,
            remediation_actions=None,
            actor=actor,
            meta={
                "evidence_count": row.evidence_count or 0,
                "confidence": round(float(row.confidence or 0.0), 3),
                "assurance_level": row.assurance_level or "POINT_IN_TIME",
                "basis": list(label.basis),
                "repeats": label.repeats,
                "memory_summary": context.summary(),
                "matched_memories": matched,
                "thresholds": {
                    "recurring": self.recurring_threshold,
                    "escalation": self.escalation_threshold,
                    "pattern": self.pattern_threshold,
                },
            },
            enrichment=enrichment_value,
        )
        self.db.add(record)
        await self.db.flush()
        return {
            "record_id": record.id,
            "assessment_id": row.assessment_id,
            "control_id": code,
            "status": row.status,
            "finding_type": finding_type,
            "classification": label.classification.value,
            "basis": list(label.basis),
            "occurrence": label.occurrence,
            "memory_summary": context.summary(),
            "matched_memories": matched,
            "enrichment": enrichment_value,
            "enrichment_error": enrichment_meta.get("error"),
            "created_at": record.created_at,
        }

    # ── human decisions ─────────────────────────────────────────────────────

    async def record_decision(
        self,
        *,
        assessment_id: str,
        control_code: str,
        framework: str,
        decision: str,
        human_feedback: str = "",
        reason: str = "",
        expires_at: datetime | None = None,
        remediation_actions: list[str] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Store a human's accept/reject/exception/escalate/remediate decision.

        Decisions are appended as their own memory events (never an UPDATE of a
        finding), and `exception` additionally creates a `ComplianceException`
        that future reviews treat as shielding — but only while it is active.
        """
        from backend.api.repositories.assessment_repo import AssessmentRepository

        assessment = await AssessmentRepository(self.db).get_by_id(assessment_id)
        if assessment is None:
            raise MemoryError(f"Assessment {assessment_id} not found.")
        org = self._org_key(assessment)
        code = control_code.strip().upper()
        decision = decision.strip().lower()

        known = {"accepted", "rejected", "exception", "escalated", "remediated"}
        if decision not in known:
            raise MemoryError(
                f"Unknown decision '{decision}'. Expected one of: {', '.join(sorted(known))}."
            )

        latest = (await self.db.execute(
            select(MemoryRecord)
            .where(
                MemoryRecord.organization_id == org,
                MemoryRecord.control_id == code,
                MemoryRecord.event == MemoryEvent.FINDING_RAISED.value,
            )
            .order_by(MemoryRecord.created_at.desc())
        )).scalars().first()

        exception_id: str | None = None
        event = MemoryEvent.FINDING_REVIEWED.value
        remediation = None
        decision_type = "human_decision"

        if decision == "exception":
            if not reason.strip():
                raise MemoryError("An exception requires a reason.")
            exception = await self.create_exception(
                organization_id=org,
                framework=framework or "",
                control_id=code,
                reason=reason,
                creator=actor or "assessor",
                expires_at=expires_at,
                assessment_id=assessment_id,
                actor=actor,
            )
            exception_id = exception.id
            event = MemoryEvent.EXCEPTION_GRANTED.value
            decision_type = FindingType.EXCEPTION.value
        elif decision == "escalated":
            event = MemoryEvent.ESCALATED.value
        elif decision == "remediated":
            event = MemoryEvent.REMEDIATION_COMPLETED.value
            decision_type = FindingType.REMEDIATION.value
            remediation = RemediationStatus.REMEDIATED.value
            if latest is not None:
                latest.remediation_status = RemediationStatus.REMEDIATED.value

        record = MemoryRecord(
            organization_id=org,
            assessment_id=assessment_id,
            evaluation_id=latest.evaluation_id if latest else None,
            framework=framework,
            framework_code=(
                getattr(assessment, "framework_code", None)
                if hasattr(assessment, "framework_code") else None
            ),
            control_id=code,
            finding_type=decision_type,
            event=event,
            classification="",
            decision=decision,
            occurrence=(latest.occurrence if latest else 1),
            severity=latest.severity if latest else None,
            status=latest.status if latest else None,
            human_feedback=human_feedback,
            remediation_status=remediation,
            remediation_actions=remediation_actions,
            actor=actor,
            meta={"decision": decision, "reason": reason or None,
                  "exception_id": exception_id},
        )
        self.db.add(record)
        await self.db.flush()
        return {
            "record_id": record.id,
            "organization_id": org,
            "control_id": code,
            "decision": decision,
            "event": event,
            "exception_id": exception_id,
            "human_feedback": human_feedback,
        }

    # ── exception lifecycle ─────────────────────────────────────────────────

    async def create_exception(
        self,
        *,
        organization_id: str,
        framework: str,
        control_id: str,
        reason: str,
        creator: str,
        expires_at: datetime | None = None,
        assessment_id: str | None = None,
        severity: str | None = None,
        actor: str | None = None,
    ) -> ComplianceException:
        """Record an approved deviation from a control requirement.

        A finding covered by an active exception is still *recorded* by the
        review workflow; the exception only explains the label. This is the
        "a previous exception does not suppress" rule, enforced here by storing
        the reason, the creator and the expiry alongside the decision.
        """
        org = organization_key(organization_id) or "unassigned"
        exception = ComplianceException(
            organization_id=org,
            assessment_id=assessment_id,
            framework=framework,
            control_id=control_id.strip().upper(),
            reason=reason.strip(),
            creator=creator,
            created_by=actor or creator,
            expires_at=expires_at,
            status="active",
            severity=severity,
        )
        self.db.add(exception)
        await self.db.flush()
        return exception

    async def grant_exception_on_assessment(
        self,
        assessment_id: str,
        *,
        framework: str,
        control_code: str,
        reason: str,
        creator: str,
        expires_at: datetime | None = None,
        severity: str | None = None,
        actor: str | None = None,
    ) -> ComplianceException:
        """Grant an exception, deriving the organisation key from the assessment.

        One code path owns exception creation; this resolves the `organization_id`
        from the assessment so the router does not have to re-derive it.
        """
        from backend.api.repositories.assessment_repo import AssessmentRepository

        assessment = await AssessmentRepository(self.db).get_by_id(assessment_id)
        if assessment is None:
            raise MemoryError(f"Assessment {assessment_id} not found.")
        return await self.create_exception(
            organization_id=self._org_key(assessment),
            framework=framework,
            control_id=control_code,
            reason=reason,
            creator=creator,
            expires_at=expires_at,
            assessment_id=assessment_id,
            severity=severity,
            actor=actor,
        )

    async def revoke_exception(
        self,
        exception_id: str,
        *,
        reason: str = "",
        actor: str | None = None,
    ) -> ComplianceException:
        """Revoke an exception; future reviews treat the control as actionable."""
        exception = (await self.db.execute(
            select(ComplianceException).where(ComplianceException.id == exception_id)
        )).scalar_one_or_none()
        if exception is None:
            raise MemoryError(f"Exception {exception_id} not found.")
        if exception.status in ("expired", "revoked"):
            raise MemoryError(
                f"Exception {exception_id} is already {exception.status}; "
                "it does not shield the control."
            )
        exception.status = "revoked"
        exception.revoked_at = datetime.utcnow()
        exception.revoked_by = actor
        exception.revoked_reason = reason.strip() or None

        record = MemoryRecord(
            organization_id=exception.organization_id,
            assessment_id=exception.assessment_id,
            framework=exception.framework,
            control_id=exception.control_id,
            finding_type=FindingType.EXCEPTION.value,
            event=MemoryEvent.EXCEPTION_REVOKED.value,
            classification="",
            decision="revoked",
            human_feedback=reason,
            actor=actor,
            meta={"exception_id": exception.id},
        )
        self.db.add(record)
        await self.db.flush()
        return exception

    # ── read paths ──────────────────────────────────────────────────────────

    async def control_history(
        self,
        organization_id: str,
        control_id: str,
        *,
        framework: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Everything stored about one control: memory records and exceptions."""
        org = organization_key(organization_id) or ""
        await self._expire_lazy()
        code = control_id.strip().upper()

        stmt = select(MemoryRecord).where(
            MemoryRecord.organization_id == org,
            MemoryRecord.control_id == code,
        )
        if framework:
            stmt = stmt.where(MemoryRecord.framework == framework)
        stmt = stmt.order_by(MemoryRecord.created_at.desc()).limit(limit)
        records = list((await self.db.execute(stmt)).scalars().all())

        estmt = select(ComplianceException).where(
            ComplianceException.organization_id == org,
            ComplianceException.control_id == code,
        )
        if framework:
            estmt = estmt.where(ComplianceException.framework == framework)
        estmt = estmt.order_by(ComplianceException.created_at.desc())
        exceptions = list((await self.db.execute(estmt)).scalars().all())

        return {
            "organization_id": org,
            "control_id": code,
            "memory": [_record_payload(r) for r in records],
            "exceptions": [_exception_payload(e) for e in exceptions],
        }

    async def similar_findings(
        self,
        organization_id: str,
        control_id: str,
        *,
        framework: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """The same finding types on other controls — what a pattern would build."""
        org = organization_key(organization_id) or ""
        await self._expire_lazy()
        code = control_id.strip().upper()

        types = set((await self.db.execute(
            select(MemoryRecord.finding_type).where(
                MemoryRecord.organization_id == org,
                MemoryRecord.control_id == code,
            ).distinct()
        )).scalars().all())

        if not types:
            return {"organization_id": org, "control_id": code, "similar": []}

        stmt = select(MemoryRecord).where(
            MemoryRecord.organization_id == org,
            MemoryRecord.control_id != code,
            MemoryRecord.finding_type.in_(types),
        )
        if framework:
            stmt = stmt.where(MemoryRecord.framework == framework)
        stmt = stmt.order_by(MemoryRecord.created_at.desc()).limit(limit)
        records = list((await self.db.execute(stmt)).scalars().all())
        return {
            "organization_id": org,
            "control_id": code,
            "similar": [_record_payload(r) for r in records],
        }

    async def recurring_findings(
        self,
        organization_id: str,
        *,
        framework: str | None = None,
        threshold: int | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Controls whose memory shows the same finding type repeatedly."""
        org = organization_key(organization_id) or ""
        await self._expire_lazy()
        minimum = threshold if threshold is not None else self.recurring_threshold

        stmt = select(MemoryRecord).where(
            MemoryRecord.organization_id == org,
        )
        if framework:
            stmt = stmt.where(MemoryRecord.framework == framework)
        stmt = stmt.order_by(MemoryRecord.created_at.desc())
        records = list((await self.db.execute(stmt)).scalars().all())

        buckets: dict[tuple[str, str], list[MemoryRecord]] = {}
        for r in records:
            if r.finding_type in {
                FindingType.CONTROL_FAILURE.value,
                FindingType.PARTIAL_COVERAGE.value,
                FindingType.EVIDENCE_GAP.value,
            }:
                buckets.setdefault((r.control_id, r.finding_type), []).append(r)

        items = []
        for (control, finding_type), group in buckets.items():
            if len(group) < minimum:
                continue
            latest = group[0]
            remediated = any(
                r.remediation_status == RemediationStatus.REMEDIATED.value for r in group
            )
            items.append({
                "control_id": control,
                "finding_type": finding_type,
                "occurrences": len(group),
                "last_seen": latest.created_at,
                "latest_classification": latest.classification or None,
                "remediated": remediated,
            })
        items.sort(key=lambda i: (-i["occurrences"], i["control_id"]))
        return {
            "organization_id": org,
            "threshold": minimum,
            "total": len(items),
            "items": items[:limit],
        }

    async def risk_summary(
        self,
        organization_id: str,
        *,
        framework: str | None = None,
    ) -> dict[str, Any]:
        """Historical risk: counts, recurrence, exceptions, open findings."""
        org = organization_key(organization_id) or ""
        await self._expire_lazy()

        stmt = select(MemoryRecord).where(MemoryRecord.organization_id == org)
        if framework:
            stmt = stmt.where(MemoryRecord.framework == framework)
        records = list((await self.db.execute(stmt)).scalars().all())

        by_type: dict[str, int] = {}
        by_classification: dict[str, int] = {}
        control_counts: dict[str, int] = {}
        open_findings = 0
        finding_records = 0
        for r in records:
            by_type[r.finding_type] = by_type.get(r.finding_type, 0) + 1
            if r.classification:
                by_classification[r.classification] = (
                    by_classification.get(r.classification, 0) + 1
                )
            if r.event == MemoryEvent.FINDING_RAISED.value:
                finding_records += 1
                control_counts[r.control_id] = control_counts.get(r.control_id, 0) + 1
                if (r.remediation_status or "") not in {
                    RemediationStatus.REMEDIATED.value,
                    RemediationStatus.ACCEPTED.value,
                }:
                    open_findings += 1

        estmt = select(ComplianceException).where(
            ComplianceException.organization_id == org
        )
        if framework:
            estmt = estmt.where(ComplianceException.framework == framework)
        exceptions = list((await self.db.execute(estmt)).scalars().all())

        status_counts: dict[str, int] = {}
        for e in exceptions:
            status_counts[e.status] = status_counts.get(e.status, 0) + 1

        leaderboard = sorted(
            ({"control_id": c, "findings": n} for c, n in control_counts.items()),
            key=lambda i: -i["findings"],
        )[:10]

        return {
            "organization_id": org,
            "memory_records": len(records),
            "by_finding_type": by_type,
            "by_classification": by_classification,
            "recurrence_leaderboard": leaderboard,
            "open_findings": open_findings,
            "finding_records": finding_records,
            "exceptions": {
                "total": len(exceptions),
                **status_counts,
            },
        }

    async def list_exceptions(
        self,
        organization_id: str,
        *,
        framework: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        org = organization_key(organization_id) or ""
        await self._expire_lazy()
        stmt = select(ComplianceException).where(
            ComplianceException.organization_id == org
        )
        if framework:
            stmt = stmt.where(ComplianceException.framework == framework)
        if status:
            stmt = stmt.where(ComplianceException.status == status)
        stmt = stmt.order_by(ComplianceException.created_at.desc())
        exceptions = list((await self.db.execute(stmt)).scalars().all())
        return {
            "organization_id": org,
            "total": len(exceptions),
            "exceptions": [_exception_payload(e) for e in exceptions],
        }


# ── payload builders ─────────────────────────────────────────────────────────

def _record_payload(r: MemoryRecord) -> dict[str, Any]:
    return {
        "id": r.id,
        "organization_id": r.organization_id,
        "assessment_id": r.assessment_id,
        "evaluation_id": r.evaluation_id,
        "framework": r.framework,
        "framework_code": r.framework_code,
        "control_id": r.control_id,
        "domain_code": r.domain_code,
        "finding_type": r.finding_type,
        "event": r.event,
        "classification": r.classification,
        "decision": r.decision,
        "occurrence": r.occurrence,
        "severity": r.severity,
        "status": r.status,
        "human_feedback": r.human_feedback,
        "remediation_status": r.remediation_status,
        "remediation_actions": r.remediation_actions,
        "actor": r.actor,
        "meta": r.meta,
        "enrichment": r.enrichment,
        "created_at": r.created_at,
    }


def _exception_payload(e: ComplianceException) -> dict[str, Any]:
    return {
        "id": e.id,
        "organization_id": e.organization_id,
        "assessment_id": e.assessment_id,
        "framework": e.framework,
        "control_id": e.control_id,
        "reason": e.reason,
        "creator": e.creator,
        "created_by": e.created_by,
        "created_at": e.created_at,
        "expires_at": e.expires_at,
        "status": e.status,
        "severity": e.severity,
        "revoked_at": e.revoked_at,
        "revoked_by": e.revoked_by,
        "revoked_reason": e.revoked_reason,
        "memory_record_id": e.memory_record_id,
    }


__all__ = [
    "MemoryError",
    "MemoryReviewRun",
    "MemoryService",
    "organization_key",
]