"""DB orchestration for evidence-to-control mappings and the control-centric read model.

Separate from `service.py` because that module owns the *evaluation* run and is
already the busiest thing in the compliance package. This one owns the edge
between evidence and controls, and the read model built on top of it:

  * `map_assessment` — gather every artefact in scope, normalise it, score it
    against every in-scope control, and persist the resulting candidate edges.
  * `review_mapping` — a human confirms or rejects one edge, and the control's
    next evaluation stops being held.
  * `control_index` / `evidence_index` — the same edges read from the two ends.
    Control-centric is "what does this control have"; evidence-centric is "what
    does this document claim to support". They are one indexed table read in
    opposite directions, so a document-centric reverse map can never disagree
    with the control view.
  * `coverage` / `gaps` — the real figures, including `NOT_EVALUATED`, computed
    against the framework's in-scope control count rather than against the number
    of rows that happen to exist. A coverage percentage whose denominator is
    "evaluations we already ran" is a percentage of nothing.
  * gap questions — targeted prompts bound to a framework, control and gap, whose
    answers flow back through `evidence.py` into the same evaluation.

NO LLM anywhere in this module. Mapping decisions come from `mapping.py` (pure),
and a model's opinion may only arrive as a *proposed score* on an edge retrieval
already returned — see `semantic/`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.compliance import mapping as mapping_rules
from backend.api.compliance.enums import (
    ComplianceStatus,
    EvidenceNature,
    MappingReviewStatus,
    Severity,
)
from backend.api.compliance.normalization import best_nature, classify_nature
from backend.api.compliance.types import ControlSpec, EvidenceRef
from backend.api.models.assessment import (
    Assessment,
    DocumentRequest,
    Evidence,
    FollowupQuestion,
    Response,
)
from backend.api.models.compliance import ComplianceEvaluation, EvidenceControlMapping
from backend.api.models.framework import Framework

#: Cap on how many controls one artefact is scored against in a single mapping
#: pass. A full NIST import is 106 controls, which is a cheap lexical pass, but
#: the cap keeps the work bounded if a future framework is an order of magnitude
#: larger — and a candidate list a reviewer cannot read is not a candidate list.
_MAX_CONTROLS_PER_ARTIFACT = 250


class MappingError(RuntimeError):
    """Base error for the mapping surface."""


class MappingNotFound(MappingError):
    """The requested mapping row does not exist for this assessment."""


# ── gathering every artefact in scope ────────────────────────────────────────

@dataclass
class ScopedEvidence:
    """One artefact, resolved to the control codes it is already attached to.

    The `declared` codes are what the *platform* thinks the artefact is for —
    where it was uploaded, which request it answered. They are recorded on every
    mapping so a reviewer can see the difference between "this was filed against
    this control" and "this merely looks like it belongs here", which is the
    difference between a curated evidence pack and a guess.
    """

    ref: EvidenceRef
    declared_codes: tuple[str, ...] = ()
    nature: EvidenceNature = EvidenceNature.UNDETERMINED
    format: str = ""


async def gather_scoped_evidence(
    db: AsyncSession,
    assessment: Assessment,
) -> list[ScopedEvidence]:
    """Every artefact attached to this assessment, with its provenance.

    Reads `Evidence`, `DocumentRequest` and answered gap questions directly
    rather than going through `gather_control_evidence`, because mapping runs
    *before* any control is known: the question here is "which controls might
    this file be about?", which a function that takes a control list cannot ask.
    """
    from backend.api.config import settings as app_settings
    from backend.api.compliance.evidence import _read_preview, _clip
    from backend.api.compliance.enums import EvidenceKind

    out: list[ScopedEvidence] = []
    settings = app_settings
    evidence_dir = None

    # 1. response-attached artefacts
    response_rows = (await db.execute(
        select(Response, Evidence).join(Evidence, Evidence.response_id == Response.id)
        .where(Response.assessment_id == assessment.id)
    )).all()
    for response, item in response_rows:
        out.append(ScopedEvidence(
            ref=EvidenceRef(
                evidence_id=item.id,
                kind=EvidenceKind.RESPONSE_ATTACHMENT,
                summary=(item.description or "")[:500],
                text=_clip(_read_preview(item.file_path)),
                source_name=item.file_name,
                collected_at=item.uploaded_at,
            ),
        ))

    # 2. owner-provided files against document requests
    requests = (await db.execute(
        select(DocumentRequest).where(DocumentRequest.assessment_id == assessment.id)
    )).scalars().all()
    if requests:
        import os

        evidence_dir = os.path.join(settings.EVIDENCE_FOLDER, assessment.id)
        for request in requests:
            if (request.status or "") not in {"provided", "accepted"}:
                continue
            declared = (request.control_code,) if request.control_code else ()
            for stored_name in (request.provided_files or []):
                out.append(ScopedEvidence(
                    ref=EvidenceRef(
                        evidence_id=f"dr:{request.id}:{stored_name}",
                        kind=EvidenceKind.DOCUMENT_REQUEST,
                        summary=(request.note or request.evidence_type or "")[:500],
                        text=_clip(_read_preview(os.path.join(evidence_dir, stored_name))),
                        source_name=stored_name,
                        collected_at=request.provided_at,
                    ),
                    declared_codes=tuple(c for c in declared if c),
                ))

    # 2b. Assessment-wide engagement uploads are shared evidence. They enter
    # retrieval once and receive independent control mappings where relevant.
    import json
    import os
    evidence_dir = evidence_dir or os.path.join(settings.EVIDENCE_FOLDER, assessment.id)
    manifest_path = os.path.join(evidence_dir, "_engagement_manifest.json")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                documents = json.load(fh).get("documents", [])
        except (OSError, ValueError, TypeError):
            documents = []
        for document in documents:
            stored_name = document.get("stored_name")
            if not stored_name:
                continue
            preview_path = os.path.join(evidence_dir, stored_name + ".preview.txt")
            try:
                with open(preview_path, encoding="utf-8") as fh:
                    text = _clip(fh.read())
            except OSError:
                text = ""
            out.append(ScopedEvidence(
                ref=EvidenceRef(
                    evidence_id=f"engagement:{assessment.id}:{stored_name}",
                    kind=EvidenceKind.ENGAGEMENT_DOCUMENT,
                    summary=(document.get("doc_type") or "Uploaded evidence")[:500],
                    text=text,
                    source_name=document.get("original_name") or stored_name,
                ),
            ))

    # 3. answers to targeted gap questions
    questions = (await db.execute(
        select(FollowupQuestion).where(
            FollowupQuestion.assessment_id == assessment.id,
            FollowupQuestion.response.is_not(None),
        )
    )).scalars().all()
    for question in questions:
        answer = (question.response or "").strip()
        if not answer:
            continue
        gap = (question.evidence_gap or "").strip()
        declared = (question.control_code,) if question.control_code else ()
        out.append(ScopedEvidence(
            ref=EvidenceRef(
                evidence_id=f"gap:{question.id}",
                kind=EvidenceKind.GAP_ANSWER,
                summary=(gap or question.text or "")[:500],
                text=answer,
                source_name=f"Gap answer for {question.control_code or 'assessment'}",
                collected_at=question.answered_at,
            ),
            declared_codes=tuple(c for c in declared if c),
        ))

    # Normalise in place so every consumer below reads one classification.
    from backend.api.compliance.normalization import detect_format

    for item in out:
        item.format = detect_format(
            item.ref.source_name, item.ref.text, item.ref.summary, item.ref.kind.value,
        ).value
        item.nature = classify_nature(
            source_name=item.ref.source_name, text=item.ref.text,
            summary=item.ref.summary, fmt=item.format,
        )
        item.ref = EvidenceRef(
            evidence_id=item.ref.evidence_id,
            kind=item.ref.kind,
            summary=item.ref.summary,
            text=item.ref.text,
            source_name=item.ref.source_name,
            collected_at=item.ref.collected_at,
            format=item.format,
            nature=item.nature.value,
        )
    return out


async def attach_accepted_engagement_mappings(db, assessment, framework, gathered) -> None:
    """Feed only accepted, retrieval-supported shared uploads to each control."""
    artifacts = await gather_scoped_evidence(db, assessment)
    by_id = {item.ref.evidence_id: item.ref for item in artifacts}
    rows = (await db.execute(select(EvidenceControlMapping).where(
        EvidenceControlMapping.assessment_id == assessment.id,
        EvidenceControlMapping.framework == framework,
        EvidenceControlMapping.evidence_kind == "engagement_document",
        EvidenceControlMapping.requires_review.is_(False),
        EvidenceControlMapping.review_status.in_(["auto_accepted", "confirmed"]),
    ))).scalars().all()
    for row in rows:
        ref = by_id.get(row.evidence_id)
        entry = gathered.get(row.control_code)
        if ref is not None and entry is not None and all(x.evidence_id != ref.evidence_id for x in entry.evidence):
            entry.evidence.append(ref)


# ── the mapping run ──────────────────────────────────────────────────────────

@dataclass
class MappingRun:
    """What one mapping pass produced, for the response and the activity log."""

    run_id: str
    assessment_id: str
    framework: str
    artefacts: int
    controls: int
    decisions: list[mapping_rules.MappingDecision] = field(default_factory=list)
    rows: list[EvidenceControlMapping] = field(default_factory=list)
    review_queue: int = 0

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "assessment_id": self.assessment_id,
            "framework": self.framework,
            "artefacts": self.artefacts,
            "controls": self.controls,
            "mappings_created": len(self.rows),
            "mappings_auto_accepted": sum(1 for r in self.rows if not r.requires_review),
            "mappings_awaiting_review": self.review_queue,
            "review_threshold": mapping_rules.AUTO_ACCEPT_THRESHOLD,
            "confidence_threshold": mapping_rules.CONFIDENT_THRESHOLD,
        }


def _row_from_decision(
    decision: mapping_rules.MappingDecision,
    *,
    assessment_id: str,
    run_id: str,
) -> EvidenceControlMapping:
    return EvidenceControlMapping(
        assessment_id=assessment_id,
        run_id=run_id,
        evidence_id=decision.evidence_id,
        evidence_source_name=decision.evidence_source_name,
        evidence_kind=decision.evidence_kind,
        evidence_nature=decision.evidence_nature,
        evidence_format=decision.evidence_format or None,
        framework=decision.framework,
        framework_code=decision.framework_code or None,
        control_id=decision.control_row_id,
        control_code=decision.control_id,
        domain_code=decision.domain_code or None,
        domain_name=decision.domain_name or None,
        relevance_score=decision.relevance_score,
        matched_sections=list(decision.matched_sections) or None,
        mapping_reason=decision.mapping_reason,
        mapping_method=decision.mapping_method,
        review_status=decision.review_status,
        requires_review=decision.requires_review,
    )


def _rehydrate(row: EvidenceControlMapping) -> EvidenceRef:
    """The `EvidenceRef` a stored edge refers to, rebuilt for re-grading.

    Carries no text: the text lives in the file, and reading it again is the
    evidence layer's job. Keeping the ref text-free here is deliberate, because a
    mapping row that carried a copy of the document would be a second, silently
    divergent copy of the evidence.
    """
    from backend.api.compliance.enums import EvidenceKind

    try:
        kind = EvidenceKind(row.evidence_kind or "external")
    except ValueError:
        kind = EvidenceKind.EXTERNAL
    return EvidenceRef(
        evidence_id=row.evidence_id,
        kind=kind,
        summary="",
        text="",
        source_name=row.evidence_source_name or row.evidence_id,
        format=row.evidence_format or "",
        nature=row.evidence_nature or EvidenceNature.UNDETERMINED.value,
    )


class MappingService:
    """Propose, persist, review and read evidence-to-control edges."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── write ───────────────────────────────────────────────────────────────

    async def map_assessment(
        self,
        assessment_id: str,
        framework: Framework,
        specs: Sequence[ControlSpec],
        *,
        id_to_code: Mapping[str, str] | None = None,
        run_id: str | None = None,
        actor: str | None = None,
    ) -> MappingRun:
        """Score every artefact against every in-scope control and persist edges.

        `specs` must be the same in-scope list the evaluator will work over, so
        mapping cannot introduce a control the assessment excluded. A re-run
        supersedes the previous edges for the same (control, evidence) pair; the
        history stays in the row's `run_id`, and human decisions (confirmed /
        rejected) are carried across rather than being silently undone.
        """
        from backend.api.repositories.assessment_repo import AssessmentRepository

        assessment = await AssessmentRepository(self.db).get_by_id(assessment_id)
        if assessment is None:
            raise MappingError(f"Assessment {assessment_id} not found.")

        controls = list(specs)[:_MAX_CONTROLS_PER_ARTIFACT]
        artefacts = await gather_scoped_evidence(self.db, assessment)
        run_id = run_id or str(uuid.uuid4())

        existing = (await self.db.execute(
            select(EvidenceControlMapping).where(
                EvidenceControlMapping.assessment_id == assessment_id,
                EvidenceControlMapping.run_id != run_id,
            )
        )).scalars().all()
        prior_decisions = {
            (row.evidence_id, (row.framework, row.control_code)): row
            for row in existing
            if row.evidence_id
        }

        decisions: list[mapping_rules.MappingDecision] = []
        for artefact in artefacts:
            decisions.extend(mapping_rules.decide_many(
                artefact.ref,
                controls,
                framework_code=framework.code,
                nature=artefact.nature,
                control_row_ids=dict(id_to_code or {}),
            ))

        readiness = "[compliance-readiness]" in (assessment.description or "")
        if readiness:
            # Textual retrieval proposes candidates; a reviewer confirms each
            # proposed edge before its evidence can support a control result.
            decisions = [
                replace(
                    decision,
                    review_status=MappingReviewStatus.PENDING_REVIEW.value,
                    requires_review=True,
                    mapping_reason=(decision.mapping_reason +
                        " Readiness assessment candidate: confirm semantic relevance before evaluation."),
                )
                for decision in decisions
            ]

        # A human decision survives a re-map. A mapping someone confirmed must
        # not drop back into the review queue because the artefact was re-read
        # and scored 2% lower; and a rejection must not silently reappear either.
        for index, decision in enumerate(decisions):
            prior = prior_decisions.get((decision.evidence_id, (decision.framework, decision.control_id)))
            if prior is not None and prior.review_status in {
                MappingReviewStatus.CONFIRMED.value,
                MappingReviewStatus.REJECTED.value,
            }:
                decisions[index] = _carry_review(decision, prior)

        existing_by_key = {
            (row.evidence_id, row.framework, row.control_code): row
            for row in existing
        }
        rows = []
        for decision in decisions:
            key = (decision.evidence_id, decision.framework, decision.control_id)
            row = existing_by_key.get(key)
            proposed = _row_from_decision(
                decision, assessment_id=assessment_id, run_id=run_id,
            )
            if row is None:
                row = proposed
                self.db.add(row)
            else:
                row.run_id = run_id
                for field_name in (
                    "evidence_source_name", "evidence_kind", "evidence_nature",
                    "evidence_format", "framework_code", "control_id",
                    "domain_code", "domain_name", "relevance_score",
                    "matched_sections", "mapping_reason", "mapping_method",
                    "review_status", "requires_review",
                ):
                    setattr(row, field_name, getattr(proposed, field_name))
            rows.append(row)
        await self.db.flush()

        return MappingRun(
            run_id=run_id,
            assessment_id=assessment_id,
            framework=framework.code,
            artefacts=len(artefacts),
            controls=len(controls),
            decisions=decisions,
            rows=rows,
            review_queue=sum(1 for r in rows if r.requires_review),
        )

    async def review_mapping(
        self,
        assessment_id: str,
        mapping_id: str,
        action: str,
        *,
        note: str | None = None,
        actor: str | None = None,
    ) -> EvidenceControlMapping:
        """Confirm or reject one edge. A human decision, recorded as such.

        `manual` mappings are the only ones that may be `confirmed`: a reviewer
        asserting an edge that no retrieval proposed is legitimate (that is what
        a human is *for*), but it has to be distinguishable from the platform
        having proposed it, or the audit trail cannot tell who found what.
        """
        from datetime import datetime

        row = (await self.db.execute(
            select(EvidenceControlMapping).where(
                EvidenceControlMapping.id == mapping_id,
                EvidenceControlMapping.assessment_id == assessment_id,
            )
        )).scalar_one_or_none()
        if row is None:
            raise MappingNotFound(
                f"Mapping {mapping_id} not found for assessment {assessment_id}."
            )

        normalised = (action or "").strip().lower()
        if normalised in {"confirm", "confirmed", "accept", "accepted"}:
            row.review_status = MappingReviewStatus.CONFIRMED.value
            row.requires_review = False
        elif normalised in {"reject", "rejected", "decline", "declined"}:
            row.review_status = MappingReviewStatus.REJECTED.value
            # A rejection leaves the control needing a human to look, because
            # rejecting the only mapping turns it back into an evidence gap that
            # has to be chased, not a resolved question.
            row.requires_review = True
        else:
            raise MappingError(
                f"Unknown mapping action '{action}'. Use 'confirm' or 'reject'."
            )

        row.reviewed_by = actor
        row.reviewed_at = datetime.utcnow()
        row.review_note = (note or None) or None
        await self.db.flush()
        return row

    async def create_mapping(
        self,
        assessment_id: str,
        *,
        framework: str,
        framework_code: str,
        control_code: str,
        control_id: str | None,
        evidence_id: str,
        relevance_score: float = 1.0,
        matched_sections: Sequence[str] = (),
        mapping_reason: str = "",
        domain_code: str | None = None,
        domain_name: str | None = None,
        evidence_source_name: str = "",
        evidence_nature: str = EvidenceNature.UNDETERMINED.value,
        actor: str | None = None,
        run_id: str | None = None,
    ) -> EvidenceControlMapping:
        """Assert an edge a human made, for an artefact retrieval did not propose.

        Scored 1.0 and `confirmed` on the strength of the assertion, because a
        person attaching a document to a control *is* the ground truth. The
        `manual` method is what keeps that honest: a reader can always tell an
        asserted edge from a proposed one.
        """
        from datetime import datetime

        run_id = run_id or str(uuid.uuid4())
        row = EvidenceControlMapping(
            assessment_id=assessment_id,
            run_id=run_id,
            evidence_id=evidence_id,
            evidence_source_name=evidence_source_name or evidence_id,
            evidence_nature=evidence_nature,
            framework=framework,
            framework_code=framework_code or None,
            control_id=control_id,
            control_code=control_code,
            domain_code=domain_code,
            domain_name=domain_name,
            relevance_score=max(0.0, min(1.0, float(relevance_score))),
            matched_sections=list(matched_sections) or None,
            mapping_reason=mapping_reason or (
                "Attached by a reviewer. A person asserted this artefact "
                "evidences this control; no automatic retrieval proposed it."
            ),
            mapping_method="manual",
            review_status=MappingReviewStatus.CONFIRMED.value,
            requires_review=False,
            reviewed_by=actor,
            reviewed_at=datetime.utcnow(),
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def delete_mapping(self, assessment_id: str, mapping_id: str) -> None:
        await self.db.execute(
            delete(EvidenceControlMapping).where(
                EvidenceControlMapping.id == mapping_id,
                EvidenceControlMapping.assessment_id == assessment_id,
            )
        )

    # ── read ────────────────────────────────────────────────────────────────

    async def list_mappings(
        self,
        assessment_id: str,
        *,
        framework: str | None = None,
        control_code: str | None = None,
        evidence_id: str | None = None,
        review_status: str | None = None,
        requires_review: bool | None = None,
        limit: int = 500,
    ) -> list[EvidenceControlMapping]:
        stmt = select(EvidenceControlMapping).where(
            EvidenceControlMapping.assessment_id == assessment_id
        )
        if framework:
            stmt = stmt.where(EvidenceControlMapping.framework == framework)
        if control_code:
            stmt = stmt.where(
                EvidenceControlMapping.control_code == control_code.strip().upper()
            )
        if evidence_id:
            stmt = stmt.where(EvidenceControlMapping.evidence_id == evidence_id)
        if review_status:
            stmt = stmt.where(EvidenceControlMapping.review_status == review_status)
        if requires_review is not None:
            stmt = stmt.where(EvidenceControlMapping.requires_review.is_(requires_review))
        stmt = stmt.order_by(
            EvidenceControlMapping.control_code,
            EvidenceControlMapping.relevance_score.desc(),
        ).limit(limit)
        return list((await self.db.execute(stmt)).scalars().all())

    async def controls_held_for_review(
        self,
        assessment_id: str,
        *,
        framework: str | None = None,
    ) -> set[str]:
        """Control codes whose only *active* edges are still awaiting a human.

        "Active" means not rejected. A rejected edge is a decision, not an open
        question, so a control whose sole mapping was rejected is out of the
        review gate — it is a plain evidence gap instead, and the evidence gate
        will say so.
        """
        stmt = select(
            EvidenceControlMapping.control_code,
            EvidenceControlMapping.requires_review,
            EvidenceControlMapping.review_status,
        ).where(
            EvidenceControlMapping.assessment_id == assessment_id,
            EvidenceControlMapping.review_status != MappingReviewStatus.REJECTED.value,
        )
        if framework:
            stmt = stmt.where(EvidenceControlMapping.framework == framework)

        grouped: dict[str, list[bool]] = {}
        for control_code, requires, status in (await self.db.execute(stmt)).all():
            settled = status == MappingReviewStatus.CONFIRMED.value
            grouped.setdefault(control_code, []).append(bool(requires) and not settled)
        return {
            code for code, flags in grouped.items() if flags and all(flags)
        }

    async def control_index(
        self,
        assessment_id: str,
        framework_key: str,
        specs: Sequence[ControlSpec],
    ) -> list[dict[str, Any]]:
        """Control-centric view: every in-scope control with its evidence edges.

        Built from the *full* in-scope control list rather than from evaluation
        rows, so a control that has never been evaluated still appears — with
        status `NOT_EVALUATED`. That is the difference between a control list and
        a list of results.
        """
        rows = await self.list_mappings(assessment_id, framework=framework_key, limit=5000)
        by_control: dict[str, list[EvidenceControlMapping]] = {}
        for row in rows:
            by_control.setdefault(row.control_code, []).append(row)

        current_evaluations = {
            (e.framework, e.control_code): e
            for e in (await self.db.execute(
                select(ComplianceEvaluation).where(
                    ComplianceEvaluation.assessment_id == assessment_id,
                    ComplianceEvaluation.is_current.is_(True),
                )
            )).scalars().all()
        }

        out: list[dict[str, Any]] = []
        for spec in specs:
            edges = by_control.get(spec.control_id, [])
            active = [e for e in edges if e.review_status != MappingReviewStatus.REJECTED.value]
            evaluation = current_evaluations.get((spec.framework, spec.control_id))
            status = evaluation.status if evaluation else ComplianceStatus.NOT_EVALUATED.value
            natures = [e.evidence_nature for e in active]
            out.append({
                "control_id": spec.control_id,
                "statement": spec.statement,
                "domain_code": spec.domain_code,
                "domain_name": spec.domain_name,
                "criticality": spec.criticality,
                "requirement_id": spec.requirement.requirement_id,
                "requirement": spec.requirement.text,
                "clauses": list(spec.requirement.clauses),
                "expected_evidence_types": list(spec.expected_evidence_types),
                "status": status,
                "has_evaluation": evaluation is not None,
                "evidence_count": len(active),
                "mapping_count": len(edges),
                "rejected_count": len(edges) - len(active),
                "evidence_nature": best_nature(natures).value,
                "awaiting_review": any(e.requires_review for e in active),
                "confidence": float(evaluation.confidence or 0.0) if evaluation else 0.0,
                "gaps": list(evaluation.gaps or []) if evaluation else [],
                "recommendation": (evaluation.recommendation or "") if evaluation else "",
                "evidence": [
                    {
                        "mapping_id": e.id,
                        "evidence_id": e.evidence_id,
                        "evidence_source_name": e.evidence_source_name,
                        "evidence_kind": e.evidence_kind,
                        "evidence_nature": e.evidence_nature,
                        "evidence_format": e.evidence_format,
                        "relevance_score": round(float(e.relevance_score or 0.0), 4),
                        "matched_sections": list(e.matched_sections or []),
                        "mapping_reason": e.mapping_reason,
                        "mapping_method": e.mapping_method,
                        "review_status": e.review_status,
                        "requires_review": bool(e.requires_review),
                    }
                    for e in sorted(
                        active, key=lambda r: (-float(r.relevance_score or 0.0), r.evidence_id)
                    )
                ],
            })
        return out

    async def evidence_index(
        self,
        assessment_id: str,
        *,
        framework: str | None = None,
    ) -> list[dict[str, Any]]:
        """Evidence-centric view: each artefact and every control it is linked to.

        This is the reverse of `control_index` over the same rows, and it is what
        makes a document-centric report possible: "what does this PDF support,
        how strongly, and has anyone confirmed it?" — a question a control-centric
        list structurally cannot answer.
        """
        rows = await self.list_mappings(assessment_id, framework=framework, limit=5000)
        by_evidence: dict[str, list[EvidenceControlMapping]] = {}
        for row in rows:
            by_evidence.setdefault(row.evidence_id, []).append(row)

        out: list[dict[str, Any]] = []
        for evidence_id, edges in by_evidence.items():
            active = [e for e in edges if e.review_status != MappingReviewStatus.REJECTED.value]
            head = edges[0]
            out.append({
                "evidence_id": evidence_id,
                "evidence_source_name": head.evidence_source_name or evidence_id,
                "evidence_kind": head.evidence_kind,
                "evidence_nature": head.evidence_nature,
                "evidence_format": head.evidence_format,
                "nature_label": _nature_label(head.evidence_nature),
                "supports_type_1": _nature_supports_type_1(head.evidence_nature),
                "mapping_count": len(edges),
                "active_mappings": len(active),
                "awaiting_review": any(e.requires_review for e in active),
                "best_relevance": round(
                    max((float(e.relevance_score or 0.0) for e in edges), default=0.0), 4
                ),
                "frameworks": sorted({e.framework for e in edges if e.framework}),
                "controls": [
                    {
                        "mapping_id": e.id,
                        "framework": e.framework,
                        "control_code": e.control_code,
                        "domain_code": e.domain_code,
                        "domain_name": e.domain_name,
                        "relevance_score": round(float(e.relevance_score or 0.0), 4),
                        "matched_sections": list(e.matched_sections or []),
                        "mapping_reason": e.mapping_reason,
                        "mapping_method": e.mapping_method,
                        "review_status": e.review_status,
                        "requires_review": bool(e.requires_review),
                    }
                    for e in sorted(
                        active, key=lambda r: (-float(r.relevance_score or 0.0), r.control_code)
                    )
                ],
                "rejected_controls": [
                    e.control_code for e in edges
                    if e.review_status == MappingReviewStatus.REJECTED.value
                ],
            })
        out.sort(key=lambda item: (-item["best_relevance"], item["evidence_id"]))
        return out

    # ── coverage / gaps ─────────────────────────────────────────────────────

    async def coverage(
        self,
        assessment_id: str,
        framework_key: str,
        specs: Sequence[ControlSpec],
    ) -> dict[str, Any]:
        """Real coverage, denominated by the framework's in-scope control count.

        The counts come from the control list, not from the evaluation rows, so
        `NOT_EVALUATED` is a first-class bucket rather than an absence. Every
        figure a dashboard shows has to be reconstructable from a single query
        against stored data, which is the only way it stays true after someone
        edits a control or re-runs an evaluation.
        """
        index = await self.control_index(assessment_id, framework_key, specs)
        total = len(index)
        counts = {status.value: 0 for status in ComplianceStatus}
        for entry in index:
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1

        applicable = [e for e in index if e["status"] != ComplianceStatus.NOT_APPLICABLE.value]
        conclusive = [e for e in applicable if e["status"] in {
            s.value for s in ComplianceStatus if s.is_conclusive
        }]
        evidenced = [e for e in index if e["evidence_count"] > 0]
        pending = [e for e in index if e["awaiting_review"]]

        def pct(n: int, of: int) -> float:
            return round(100.0 * n / of, 1) if of else 0.0

        return {
            "framework": framework_key,
            "total_controls": total,
            "applicable_controls": len(applicable),
            "counts": counts,
            "evaluated": sum(1 for e in index if e["has_evaluation"]),
            "not_evaluated": counts.get(ComplianceStatus.NOT_EVALUATED.value, 0),
            "conclusive": len(conclusive),
            "conclusive_pct": pct(len(conclusive), len(applicable)),
            "evidenced": len(evidenced),
            "evidence_coverage_pct": pct(len(evidenced), total),
            "awaiting_review": len(pending),
            "not_applicable": counts.get(ComplianceStatus.NOT_APPLICABLE.value, 0),
            "status_order": [s.value for s in ComplianceStatus],
        }

    async def gaps(
        self,
        assessment_id: str,
        framework_key: str,
        specs: Sequence[ControlSpec],
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """The remediation backlog, derived from the database and nothing else.

        A control appears here when its status is a gap, and the entry says
        *which* kind — never evaluated, nothing to grade, unattributable
        evidence, partly covered, not met — plus what evidence it already expects
        and whether someone is already being asked for it. The expected evidence
        types come from the framework dataset, not from a hand-maintained list, so
        the ask for a new control is as current as its import.
        """
        index = await self.control_index(assessment_id, framework_key, specs)
        open_requests = {
            (r.control_code, r.domain_code, r.status)
            for r in (await self.db.execute(
                select(DocumentRequest).where(
                    DocumentRequest.assessment_id == assessment_id,
                    DocumentRequest.status.in_(("requested", "rejected")),
                )
            )).scalars().all()
        }
        questions = {
            (q.control_code, q.status)
            for q in (await self.db.execute(
                select(FollowupQuestion).where(
                    FollowupQuestion.assessment_id == assessment_id,
                )
            )).scalars().all()
        }

        out: list[dict[str, Any]] = []
        for entry in index:
            try:
                status = ComplianceStatus(entry["status"])
            except ValueError:
                continue
            if not status.is_gap:
                continue
            code = entry["control_id"]
            out.append({
                "control_id": code,
                "domain_code": entry["domain_code"],
                "domain_name": entry["domain_name"],
                "criticality": entry["criticality"],
                "status": status.value,
                "gap_kind": _GAP_KINDS.get(status, status.value),
                "requirement": entry["requirement"],
                "gaps": entry["gaps"],
                "recommendation": entry["recommendation"],
                "expected_evidence_types": entry["expected_evidence_types"],
                "evidence_count": entry["evidence_count"],
                "awaiting_review": entry["awaiting_review"],
                "document_requested": any(
                    r[0] == code for r in open_requests
                ),
                "question_open": any(
                    q[0] == code and q[1] == "open" for q in questions
                ),
                "severity": _GAP_SEVERITY.get(status, Severity.MEDIUM.value),
            })

        rank = {s.value: i for i, s in enumerate(ComplianceStatus)}
        out.sort(key=lambda g: (rank.get(g["status"], 99), g["control_id"]))
        return out[:limit]

    # ── targeted questions ──────────────────────────────────────────────────

    async def raise_gap_questions(
        self,
        assessment_id: str,
        framework_key: str,
        specs: Sequence[ControlSpec],
        *,
        statuses: Sequence[str] = (
            ComplianceStatus.INSUFFICIENT_EVIDENCE.value,
            ComplianceStatus.PARTIAL.value,
            ComplianceStatus.NOT_EVALUATED.value,
        ),
        actor: str | None = None,
        limit: int = 100,
    ) -> list[FollowupQuestion]:
        """Turn open evidence gaps into questions bound to a control and a gap.

        One question per control, and never a duplicate: an assessment that has
        already asked about a control keeps its existing question, so a
        contributor is not asked the same thing twice by two reviewers. This is
        the questionnaire used as the workflow describes — a way to close a
        specific gap, not a questionnaire that drives the assessment.
        """
        gap_entries = await self.gaps(assessment_id, framework_key, specs, limit=limit)
        wanted = {entry["control_id"] for entry in gap_entries if entry["status"] in set(statuses)}
        if not wanted:
            return []

        existing = (await self.db.execute(
            select(FollowupQuestion).where(
                FollowupQuestion.assessment_id == assessment_id,
                FollowupQuestion.control_code.in_(sorted(wanted)),
                FollowupQuestion.status != "resolved",
            )
        )).scalars().all()
        already = {q.control_code for q in existing if q.control_code}

        code_to_row = {spec.control_id: spec for spec in specs}
        by_code = {entry["control_id"]: entry for entry in gap_entries}
        created: list[FollowupQuestion] = []
        for code in sorted(wanted - already):
            spec = code_to_row.get(code)
            entry = by_code.get(code)
            if spec is None or entry is None:
                continue
            gap_text = _gap_question_text(entry, spec)
            question = FollowupQuestion(
                assessment_id=assessment_id,
                framework=framework_key,
                control_code=code,
                evidence_gap=entry["gap_kind"],
                text=gap_text,
                help_text=(
                    "This question exists because " + code + " has no usable "
                    f"evidence ({entry['gap_kind']}). Answer it, or attach an "
                    "artefact that demonstrates the control."
                ),
                question_type="FREE_TEXT",
                source="vendor_assessment",
                domain_code=spec.domain_code,
                domain_name=spec.domain_name,
                expected_evidence_types=list(spec.expected_evidence_types),
                gap_if_deficient=entry["recommendation"] or None,
                status="open",
                weight=2.0,
            )
            self.db.add(question)
            created.append(question)
        await self.db.flush()
        return created

    async def answer_gap_question(
        self,
        assessment_id: str,
        question_id: str,
        answer: str,
        *,
        actor: str | None = None,
    ) -> FollowupQuestion:
        """Record an answer. It becomes evidence on the next evaluation run.

        The answer is *not* scored here and is not a conclusion. It is written so
        `evidence.py` can collect it as an `EvidenceRef` for the target control,
        where the same deterministic rubric grades it as it grades any artefact.
        A typed answer that bypassed that would be the soft path to a PASS this
        whole design exists to close off.
        """
        from datetime import datetime

        from backend.core.evaluation_metrics import score_evidence_quality

        question = (await self.db.execute(
            select(FollowupQuestion).where(
                FollowupQuestion.id == question_id,
                FollowupQuestion.assessment_id == assessment_id,
            )
        )).scalar_one_or_none()
        if question is None:
            raise MappingNotFound(
                f"Question {question_id} not found for assessment {assessment_id}."
            )

        text = (answer or "").strip()
        if not text:
            raise MappingError("An answer cannot be empty.")
        question.response = text
        question.answered_at = datetime.utcnow()
        question.answered_by = actor
        question.status = "answered"
        # Advisory only, and only ever a fraction of what a real artefact earns:
        # a narrative answer is capped at 0.5 so it can never, by itself, carry a
        # control over the pass threshold on the answer half of the blend.
        question.response_score = min(
            0.5,
            score_evidence_quality(text[:20000], text, question.framework or "nist-csf-2-0") / 200.0,
        )
        await self.db.flush()
        return question


# ── helpers ──────────────────────────────────────────────────────────────────

#: What kind of work each gap status represents. Distinct from the status itself
#: because the status says where a control stands, and this says what to do.
_GAP_KINDS = {
    ComplianceStatus.NOT_EVALUATED: "Never evaluated",
    ComplianceStatus.INSUFFICIENT_EVIDENCE: "No evidence supplied",
    ComplianceStatus.REVIEW_REQUIRED: "Evidence not attributable to this control",
    ComplianceStatus.PARTIAL: "Requirement only partly evidenced",
    ComplianceStatus.FAIL: "Requirement not met",
}

_GAP_SEVERITY = {
    ComplianceStatus.FAIL: Severity.HIGH.value,
    ComplianceStatus.NOT_EVALUATED: Severity.MEDIUM.value,
    ComplianceStatus.INSUFFICIENT_EVIDENCE: Severity.MEDIUM.value,
    ComplianceStatus.REVIEW_REQUIRED: Severity.LOW.value,
    ComplianceStatus.PARTIAL: Severity.MEDIUM.value,
}

_NATURE_LABELS = {n.value: n.label for n in EvidenceNature}


def _nature_label(value: str | None) -> str:
    return _NATURE_LABELS.get(value or "", "")


def _nature_supports_type_1(value: str | None) -> bool:
    try:
        return EvidenceNature(value or "").is_point_in_time
    except ValueError:
        return False


def _gap_question_text(entry: Mapping[str, Any], spec: ControlSpec) -> str:
    expected = ", ".join(spec.expected_evidence_types[:3])
    ask = (
        f"Describe how {spec.control_id} is implemented, and name the artefact "
        f"that shows it."
    )
    if expected:
        ask += f" Useful evidence here: {expected}."
    return f"{spec.control_id} — {spec.statement or spec.requirement.text}. {ask}"


def _carry_review(
    decision: mapping_rules.MappingDecision,
    prior: EvidenceControlMapping,
) -> mapping_rules.MappingDecision:
    """Re-apply a human's earlier decision to a freshly computed candidate."""
    return mapping_rules.MappingDecision(
        evidence_id=decision.evidence_id,
        control_id=decision.control_id,
        framework=decision.framework,
        framework_code=decision.framework_code,
        domain_code=decision.domain_code,
        domain_name=decision.domain_name,
        control_row_id=decision.control_row_id,
        relevance_score=decision.relevance_score,
        matched_sections=decision.matched_sections,
        mapping_reason=(
            f"{decision.mapping_reason} (A human previously "
            f"{'confirmed' if prior.review_status == MappingReviewStatus.CONFIRMED.value else 'rejected'}"
            f" this mapping; that decision is retained.)"
        ),
        evidence_source_name=decision.evidence_source_name,
        evidence_kind=decision.evidence_kind,
        evidence_nature=decision.evidence_nature,
        evidence_format=decision.evidence_format,
        mapping_method=decision.mapping_method,
        review_status=prior.review_status,
        requires_review=(
            False if prior.review_status == MappingReviewStatus.CONFIRMED.value
            else decision.requires_review
        ),
    )


__all__ = [
    "MappingError",
    "MappingNotFound",
    "MappingRun",
    "MappingService",
    "ScopedEvidence",
    "gather_scoped_evidence",
]
