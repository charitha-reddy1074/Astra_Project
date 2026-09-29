"""The assessment pipeline: evidence normalisation + cost-accounted reviews.

Part 4's write path (`run`) and read path (`report`) over the existing engine.

What it does — and deliberately does NOT do:

* It reuses `ComplianceService.evaluate` unchanged (the deterministic engine)
  and the existing semantic and memory layers. There is **no parallel engine**;
  one evaluation row set exists and every layer reads or annotates it.
* Evidence normalisation (`compliance/normalization.py`) is advisory: format,
  quality band and content hash describe an artefact for the report and for
  cost accounting, and never alter a status. The deterministic gate still keys
  off the raw evidence text only.
* `run` persists the semantic review records and the memory review records it
  produces, so the dashboard renders the *recorded* readings. `report` reads
  only — it never calls a model, never writes a row. Calling `report` after a
  `run` costs nothing beyond IO, which is the property a dashboard needs.

Cost accounting is a first-class output (`PipelineMetrics`): evidence files
seen, deduplicated by content hash, trimmed by the same budget the semantic
layer enforces, chunked, and how many of those chunks actually reached a model
(to model-invoked, minus cache hits). The number is real because the budget
below is the budget actually applied.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import select, update

from backend.api.compliance.evidence import gather_control_evidence
from backend.api.compliance.normalization import (
    budget_evidence,
    dedupe_by_hash,
    evidence_chunks,
    is_implementation_grade,
    normalize_refs,
    quality_from_score,
)
from backend.api.compliance.semantic.client import SemanticEvaluator
from backend.api.compliance.semantic.retrieval import ControlRetriever
from backend.api.compliance.semantic.schema import SemanticResult
from backend.api.compliance.semantic.service import SemanticService
from backend.api.compliance.service import (
    ComplianceError,
    ComplianceService,
    FrameworkNotEvaluable,
    load_controls_in_scope,
    load_evaluable_framework,
    result_from_row,
)
from backend.api.models.compliance import (
    ComplianceEvaluation,
    SemanticReviewRecord,
)
from backend.api.models.memory import MemoryRecord

logger = logging.getLogger(__name__)

#: Findings a dashboard should count as still open for the risk panel.
_OPEN_FINDING_STATUSES = {"open", "acknowledged"}
#: Memory classifications that constitute a decision-relevant repeat.
_RISK_CLASSIFICATIONS = {
    "RECURRING_FINDING", "ESCALATION_REQUIRED", "PATTERN_DETECTED"
}
#: How many memory records are read back per control for the read-only report.
_MEMORY_LIMIT = 8


class AssessmentPipelineError(RuntimeError):
    """Base error for the assessment pipeline surface."""


@dataclass
class PipelineMetrics:
    """Deterministic counters for one (or a combined) pipeline run.

    Every number is derived from an action the pipeline actually took, so the
    "avoided budget" figure is an accounting of real, enforced limits rather
    than a projection.
    """

    frameworks: int = 0
    controls_reviewed: int = 0
    evidence_files_seen: int = 0
    deduplicated: int = 0
    dropped_over_budget: int = 0
    truncated: int = 0
    chunks_built: int = 0
    semantic_evaluations: int = 0
    semantic_cache_hits: int = 0
    controls_skipped_semantic: int = 0
    memory_records_written: int = 0
    llm_calls_by_tier: dict[str, int] = field(default_factory=dict)

    @property
    def avoided_llm_calls(self) -> int:
        return self.controls_skipped_semantic + self.semantic_cache_hits

    def note(self, *, refs_seen: int, deduplicated: int, dropped: int,
             truncated: int, chunks: int) -> None:
        self.evidence_files_seen += refs_seen
        self.deduplicated += deduplicated
        self.dropped_over_budget += dropped
        self.truncated += truncated
        self.chunks_built += chunks

    def note_semantic(self, results: Sequence[SemanticResult]) -> None:
        for r in results:
            if r.model_invoked:
                if r.cache_hit:
                    self.semantic_cache_hits += 1
                else:
                    self.semantic_evaluations += 1
                    tier = r.model_tier or "unknown"
                    self.llm_calls_by_tier[tier] = self.llm_calls_by_tier.get(tier, 0) + 1
            else:
                self.controls_skipped_semantic += 1

    def note_runs(self, groups: Sequence[dict[str, Any]]) -> None:
        self.frameworks = len(groups)
        self.controls_reviewed = sum(len(g.get("controls", ())) for g in groups)

    def as_dict(self) -> dict[str, Any]:
        return {
            "frameworks": self.frameworks,
            "controls_reviewed": self.controls_reviewed,
            "evidence_files_seen": self.evidence_files_seen,
            "deduplicated": self.deduplicated,
            "dropped_over_budget": self.dropped_over_budget,
            "truncated": self.truncated,
            "chunks_built": self.chunks_built,
            "semantic_evaluations": self.semantic_evaluations,
            "semantic_cache_hits": self.semantic_cache_hits,
            "controls_skipped_semantic": self.controls_skipped_semantic,
            "avoided_llm_calls": self.avoided_llm_calls,
            "memory_records_written": self.memory_records_written,
            "llm_calls_by_tier": dict(self.llm_calls_by_tier),
        }


def _normalised_package(
    refs: Sequence[Any],
    framework_key: str,
    metrics: PipelineMetrics,
) -> tuple[dict[str, Any], list[Any]]:
    """Normalise, dedup and budget one control's evidence; account the cost.

    Returns the report package and the budgeted `EvidenceRef` list the semantic
    layer is actually given; they come from the same normalise → dedup → budget
    pipeline, which is why the package's "chunks"/"dropped" counters and the
    semantic layer's grounding agree on the caps. The raw `refs` may come from
    the record of a previous run (read path) or a freshly re-gathered set
    beside a new run (write path) — the accounting is the same either way.
    """
    raw_items = list(refs)
    metrics.note(
        refs_seen=len(raw_items),
        deduplicated=0, dropped=0, truncated=0, chunks=0,
    )

    normalised = normalize_refs(raw_items, framework_key)
    kept, dropped_dups, _ = dedupe_by_hash(normalised)
    metrics.note(
        refs_seen=0, deduplicated=dropped_dups, dropped=0, truncated=0, chunks=0,
    )
    kept, dropped_budget, truncated = budget_evidence(kept)
    chunks = sum(len(evidence_chunks(r.text)) for r in kept if r.has_content)
    metrics.note(
        refs_seen=0, deduplicated=0, dropped=dropped_budget,
        truncated=truncated, chunks=chunks,
    )

    package = {
        "total_items": len(raw_items),
        "deduplicated": dropped_dups,
        "dropped_over_budget": dropped_budget,
        "truncated": truncated,
        "chunks": chunks,
        "items": [_ref_payload(r) for r in kept],
    }
    return package, list(kept)


def _ref_payload(ref: Any) -> dict[str, Any]:
    return {
        "evidence_id": ref.evidence_id,
        "kind": ref.kind.value,
        "source_name": ref.source_name,
        "summary": ref.summary,
        "format": ref.format or "",
        "quality": ref.quality or "",
        "content_hash": ref.content_hash or None,
        "chars": len(ref.text or ""),
        "has_content": ref.has_content,
    }


def _finding_payload(finding: Any | None) -> dict[str, Any] | None:
    if finding is None:
        return None
    return {
        "id": finding.id,
        "control_code": finding.control_code,
        "title": finding.title,
        "gap_description": finding.gap_description,
        "severity": finding.severity,
        "status": finding.status,
    }


def _risk_payload(result: Any) -> dict[str, Any]:
    status = result.status.value
    criticality = result.criticality or "standard"
    severity = result.severity.value
    if status in {"FAIL", "PARTIAL"}:
        if criticality == "critical" and status == "FAIL":
            rating = "critical"
        elif severity in {"critical", "high"}:
            rating = "high"
        elif status == "FAIL":
            rating = severity
        else:
            rating = severity if severity != "critical" else "high"
    elif status == "INSUFFICIENT_EVIDENCE":
        rating = "monitoring"
    else:
        rating = "low" if status == "PASS" else "info"
    return {"severity": severity, "criticality": criticality, "rating": rating}


def _recommendation_payload(result: Any, memory_item: dict[str, Any] | None) -> dict[str, Any]:
    actions: list[str] = []
    remediation_status: str | None = None
    if memory_item:
        remediation_status = memory_item.get("remediation_status")
        mem = memory_item.get("_memory")
        if isinstance(mem, MemoryRecord):
            actions = list(mem.remediation_actions or ())
            remediation_status = remediation_status or mem.remediation_status
    return {
        "text": result.recommendation or "",
        "remediation_status": remediation_status or None,
        "remediation_actions": actions,
    }


def _historical_payload(memory_item: dict[str, Any] | None) -> dict[str, Any]:
    if memory_item is None:
        return {
            "classification": "",
            "basis": [],
            "occurrence": 0,
            "matched_memories": [],
            "enrichment": None,
            "enrichment_error": None,
        }
    return {
        "classification": memory_item.get("classification", ""),
        "basis": list(memory_item.get("basis", []) or []),
        "occurrence": int(memory_item.get("occurrence", 0) or 0),
        "matched_memories": list(memory_item.get("matched_memories", []) or []),
        "enrichment": memory_item.get("enrichment"),
        "enrichment_error": memory_item.get("enrichment_error"),
    }


def _advisory_payload(semantic: Any) -> dict[str, Any]:
    """Serialise a SemanticResult (run path) or SemanticReviewRecord (report)."""

    def _as_dict(obj: Any) -> dict[str, Any] | None:
        if obj is None:
            return None
        if hasattr(obj, "as_dict"):
            return obj.as_dict()
        if isinstance(obj, dict):
            return obj
        return None

    assessment = _as_dict(getattr(semantic, "assessment", None))
    fallback_reason = getattr(semantic, "fallback_reason", "") or ""
    fallback_reasoning = getattr(semantic, "fallback_reasoning", "") or ""
    return {
        "model_invoked": bool(getattr(semantic, "model_invoked", False)),
        "decision": (assessment or {}).get("decision"),
        "confidence": (assessment or {}).get("confidence"),
        "evidence_strength": (assessment or {}).get("evidence_strength"),
        "reasoning": (assessment or {}).get("reasoning"),
        "identified_gaps": list((assessment or {}).get("identified_gaps", []) or []),
        "recommended_actions": list((assessment or {}).get("recommended_actions", []) or []),
        "band": str(getattr(semantic, "confidence_band", "")),
        "more_cautious_than_deterministic": bool(
            getattr(semantic, "more_cautious_than_deterministic", False)
        ),
        "cache_hit": bool(getattr(semantic, "cache_hit", False)),
        "fallback_reason": fallback_reason,
        "fallback_reasoning": fallback_reasoning,
    }


def _status_payload(result: Any) -> dict[str, Any]:
    return {
        "is_conclusive": bool(result.status.is_conclusive),
        "is_failure": bool(result.status.is_failure),
    }


def _control_payload(
    *,
    result: Any,
    spec: Any,
    package: dict[str, Any],
    quality: dict[str, Any],
    memory_item: dict[str, Any] | None,
    finding: Any | None,
    semantic: Any | None,
    framework_code: str | None,
) -> dict[str, Any]:
    status = result.status.value
    band = quality["band"]
    quality["formats"] = sorted({
        str(i.get("format") or "unknown") for i in package["items"]
    })

    return {
        "framework": result.framework,
        "framework_code": framework_code,
        "control": {
            "control_id": spec.control_id,
            "statement": spec.statement or "",
            "domain_code": spec.domain_code,
            "domain_name": spec.domain_name,
            "criticality": spec.criticality,
            "expected_evidence_types": list(spec.expected_evidence_types or ()),
            "requirement": {
                "requirement_id": spec.requirement.requirement_id,
                "text": spec.requirement.text,
                "clauses": list(spec.requirement.clauses or ()),
            },
        },
        "requirement": {
            "requirement_id": spec.requirement.requirement_id,
            "text": spec.requirement.text,
            "clauses": list(spec.requirement.clauses or ()),
        },
        "status": status,
        **_status_payload(result),
        "reasoning": result.reasoning or "",
        "gaps": list(result.gaps or ()),
        "score": round(float(result.score or 0.0), 3),
        "response_score": round(float(result.response_score or 0.0), 3),
        "evidence_score": round(float(result.evidence_score or 0.0), 3),
        "evidence": {
            "summary": result.evidence_summary or "",
            "count": int(result.evidence_count or 0),
            "score": round(float(result.evidence_score or 0.0), 3),
            **{k: v for k, v in package.items()},
        },
        "evidence_quality": quality,
        "confidence": round(float(result.confidence or 0.0), 3),
        "confidence_band": result.confidence_band.value if getattr(
            result, "confidence_band", None
        ) is not None else str(getattr(result, "confidence_band", "")),
        "confidence_factors": dict(result.confidence_factors or {}),
        "historical_context": _historical_payload(memory_item),
        "finding": _finding_payload(finding),
        "risk": _risk_payload(result),
        "recommendation": _recommendation_payload(result, memory_item),
        "semantic_advisory": _advisory_payload(semantic) if semantic is not None else None,
        "assurance_level": result.assurance_level or "POINT_IN_TIME",
    }


def _merged_counts(groups: Sequence[dict[str, Any]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for group in groups:
        summary = group.get("summary", {})
        for status, count in (summary.get("counts") or {}).items():
            merged[status] = merged.get(status, 0) + int(count or 0)
    return merged


def _overall_summary(groups: Sequence[dict[str, Any]]) -> dict[str, Any]:
    counts = _merged_counts(groups)
    total = sum(counts.values())
    conclusive = sum(g.get("summary", {}).get("conclusive", 0) for g in groups)
    open_findings = sum(g.get("summary", {}).get("open_findings", 0) for g in groups)
    high_risk = sum(g.get("summary", {}).get("high_risk_findings", 0) for g in groups)
    recurring = sum(g.get("summary", {}).get("recurring", 0) for g in groups)
    confidences = [
        c for g in groups for c in (g.get("summary", {}).get("confidence_samples", []) or [])
    ]
    return {
        "total": total,
        "counts": counts,
        "conclusive": conclusive,
        "conclusive_pct": round(100.0 * conclusive / total, 1) if total else 0.0,
        "mean_confidence": (
            round(sum(confidences) / len(confidences), 3) if confidences else 0.0
        ),
        "open_findings": open_findings,
        "high_risk_findings": high_risk,
        "recurring": recurring,
    }


class AssessmentPipelineService:
    """The combined assessment path: evaluate + annotate + review + account."""

    def __init__(self, db: Any, *, actor: str | None = None, llm_client: Any = None):
        self.db = db
        self.actor = actor
        self.llm_client = llm_client

    # ── write path ─────────────────────────────────────────────────────────

    async def run(
        self,
        assessment_id: str,
        framework_id: str | None = None,
        *,
        control_codes: Sequence[str] | None = None,
        create_findings: bool = True,
        include_memory_review: bool = True,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Evaluate, annotate and review; persist every new record.

        Returns the same report shape a later `report()` call will produce, so a
        client that just ran the pipeline can render it immediately, and a
        refresh days later fetches the recorded (not re-computed) readings.
        """
        from backend.api.repositories.assessment_repo import AssessmentRepository

        assessment = await AssessmentRepository(self.db).get_by_id(assessment_id)
        if assessment is None:
            raise ComplianceError(f"Assessment {assessment_id} not found.")

        framework_ids: list[str] = []
        if framework_id:
            framework_ids = [framework_id]
        else:
            framework_ids = list(assessment.framework_ids or [])
            if control_codes and not framework_ids:
                raise AssessmentPipelineError(
                    "No evaluable framework is selected for this assessment; "
                    "pass framework_id."
                )

        metrics = PipelineMetrics()
        groups: list[dict[str, Any]] = []
        runs: list[list[Any]] = []
        for fid in framework_ids:
            try:
                group, run_rows = await self._run_framework(
                    assessment=assessment,
                    framework_id=fid,
                    control_codes=control_codes,
                    create_findings=create_findings,
                    include_memory_review=include_memory_review,
                    top_k=top_k,
                    metrics=metrics,
                )
            except FrameworkNotEvaluable as exc:
                logger.warning("pipeline skipped unevaluable framework: %s", exc)
                continue
            except ComplianceError as exc:
                logger.warning("pipeline skipped a framework row: %s", exc)
                continue
            if group is not None:
                groups.append(group)
                runs.append(run_rows)

        # `ComplianceService.evaluate` marks every current row of the assessment
        # non-current when it writes a run, which is right for a single-framework
        # re-evaluation but would let a combined run's second framework unset the
        # first one's rows. Compensate framework-by-framework so each framework's
        # newest run (and only that framework's) is current.
        for run_rows in runs:
            if not run_rows:
                continue
            fw_key = run_rows[0].framework
            await self.db.execute(
                update(ComplianceEvaluation)
                .where(
                    ComplianceEvaluation.assessment_id == assessment_id,
                    ComplianceEvaluation.framework == fw_key,
                    ComplianceEvaluation.is_current.is_(True),
                )
                .values(is_current=False)
            )
            run_id = run_rows[0].run_id
            await self.db.execute(
                update(ComplianceEvaluation)
                .where(
                    ComplianceEvaluation.assessment_id == assessment_id,
                    ComplianceEvaluation.run_id == run_id,
                )
                .values(is_current=True)
            )

        metrics.note_runs(groups)
        return self._report_payload(
            assessment=assessment,
            groups=groups,
            metrics=metrics,
            mode="pipeline",
        )

    async def report(
        self,
        assessment_id: str,
        framework_id: str | None = None,
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """The read-only report: recorded readings only, no model calls.

        Works whether or not a pipeline ever ran: the deterministic rows alone
        produce a full report (with no semantic or memory sections), which is
        the pre-pipeline state rendered in the same shape.
        """
        from backend.api.repositories.assessment_repo import AssessmentRepository

        assessment = await AssessmentRepository(self.db).get_by_id(assessment_id)
        if assessment is None:
            raise ComplianceError(f"Assessment {assessment_id} not found.")

        rows = await ComplianceService(self.db).list_evaluations(
            assessment_id, current_only=True
        )
        if framework_id:
            _framework, adapter = await load_evaluable_framework(self.db, framework_id)
            rows = [r for r in rows if (r.framework or "") == adapter.key]
        if not rows:
            metrics = PipelineMetrics()
            return self._report_payload(
                assessment=assessment,
                groups=[],
                metrics=metrics,
                mode="report",
            )

        by_framework: dict[str, list[Any]] = {}
        for row in rows:
            by_framework.setdefault(row.framework or "", []).append(row)

        metrics = PipelineMetrics()
        groups: list[dict[str, Any]] = []
        for fw_key, group_rows in by_framework.items():
            fid = (group_rows[0].framework_id or "")
            try:
                _framework, adapter = await load_evaluable_framework(self.db, fid or "")
            except (ComplianceError, FrameworkNotEvaluable):
                continue
            if adapter.key != fw_key:
                continue
            group = await self._report_framework(
                assessment=assessment,
                framework_id=fid,
                rows=group_rows,
                metrics=metrics,
            )
            if group is not None:
                groups.append(group)

        metrics.note_runs(groups)
        return self._report_payload(
            assessment=assessment,
            groups=groups,
            metrics=metrics,
            mode="report",
        )

    # ── internals ──────────────────────────────────────────────────────────

    async def _run_framework(
        self,
        *,
        assessment: Any,
        framework_id: str,
        control_codes: Sequence[str] | None,
        create_findings: bool,
        include_memory_review: bool,
        top_k: int,
        metrics: PipelineMetrics,
    ) -> tuple[dict[str, Any] | None, list[ComplianceEvaluation]]:
        framework, adapter = await load_evaluable_framework(self.db, framework_id)
        specs, id_to_code, code_to_domain = await load_controls_in_scope(
            self.db, assessment, framework
        )
        if control_codes:
            wanted = {c.strip().upper() for c in control_codes}
            specs = [s for s in specs if s.control_id.strip().upper() in wanted]
        if not specs:
            return None, []

        codes = [s.control_id for s in specs]
        ev_run = await ComplianceService(self.db).evaluate(
            assessment.id,
            framework_id,
            actor=self.actor,
            control_codes=codes,
            create_findings=create_findings,
        )

        gathered = gather_control_evidence(
            assessment,
            control_codes=codes,
            control_id_to_code=id_to_code,
            control_domain=code_to_domain,
        )

        specs_by_code = {s.control_id: s for s in specs}
        rows_by_code = {r.control_code.strip().upper(): r for r in ev_run.rows}

        # Normalise + account the evidence each control will actually be
        # judged-and-reviewed on. One pass, one ledger: the package the row
        # reports and the refs the semantic layer is handed are the same
        # budgeted set, so the "chunks/dropped" numbers are the semantic
        # layer's actual input, not a projection.
        packages: dict[str, dict[str, Any]] = {}
        evidence_by_control: dict[str, list[Any]] = {}
        for result in ev_run.results:
            entry = gathered.get(result.control_id)
            refs = list(entry.evidence) if entry else []
            package, kept_refs = _normalised_package(refs, adapter.key, metrics)
            packages[result.control_id] = package
            evidence_by_control[result.control_id] = kept_refs

        controls_by_id = specs_by_code
        evaluator = SemanticEvaluator()
        service = SemanticService(
            evaluator=evaluator,
            retriever=ControlRetriever(list(specs)),
            top_k=top_k,
        )
        semantic_results = service.evaluate_run(
            results=ev_run.results,
            controls_by_id=controls_by_id,
            evidence_by_control=evidence_by_control,
        )
        metrics.note_semantic(semantic_results)
        semantic_by_code = {
            s.control_id.strip().upper(): s for s in semantic_results
        }
        await self._persist_semantic(
            assessment_id=assessment.id,
            run_id=ev_run.run_id,
            framework_code=framework.code,
            results=semantic_results,
            specs_by_code=controls_by_id,
        )

        # Memory review persists its own records (Hindsight).
        mem_by_code: dict[str, dict[str, Any]] = {}
        mem_run = None
        if True:  # review always runs; cost is already gated upstream
            from backend.api.compliance.memory.service import MemoryService

            mem_run = await MemoryService(self.db).review(
                assessment.id,
                framework_id,
                control_codes=[s.control_id for s in specs],
                model_inference=include_memory_review,
                actor=self.actor,
                llm_client=self.llm_client,
            )
            mem_by_code = {
                (i.get("control_id") or "").strip().upper(): i for i in mem_run.items
            }
        metrics.memory_records_written += len(mem_run.items) if mem_run else 0

        findings_by_code = {
            (f.control_code or "").strip().upper(): f for f in ev_run.findings
        }

        controls: list[dict[str, Any]] = []
        confidence_samples: list[float] = []
        for result in ev_run.results:
            code = result.control_id
            row = rows_by_code.get(code.strip().upper())
            spec = specs_by_code.get(code)
            if row is None or spec is None:
                continue
            confidence_samples.append(float(result.confidence or 0.0))
            controls.append(_control_payload(
                result=result,
                spec=spec,
                package=packages[code],
                quality=self._quality_payload_row(row),
                memory_item=mem_by_code.get(code.strip().upper()),
                finding=findings_by_code.get(code.strip().upper()),
                semantic=semantic_by_code.get(code.strip().upper()),
                framework_code=framework.code,
            ))

        summary = dict(ev_run.summary)
        summary["confidence_samples"] = confidence_samples
        summary.update(self._summary_extension(controls))

        return {
            "framework": adapter.key,
            "framework_code": framework.code,
            "framework_name": framework.name,
            "assurance_level": adapter.spec.assurance.value,
            "asserts_operating_effectiveness": False,
            "run_id": ev_run.run_id,
            "summary": summary,
            "controls": controls,
        }, ev_run.rows

    async def _report_framework(
        self,
        *,
        assessment: Any,
        framework_id: str,
        rows: list[ComplianceEvaluation],
        metrics: PipelineMetrics,
    ) -> dict[str, Any] | None:
        framework, adapter = await load_evaluable_framework(self.db, framework_id)
        specs, id_to_code, code_to_domain = await load_controls_in_scope(
            self.db, assessment, framework
        )
        specs_by_code = {s.control_id: s for s in specs}
        codes = [r.control_code.strip().upper() for r in rows
                 if (r.control_code or "").strip().upper() in specs_by_code]
        if not codes:
            return None
        codes = sorted({c for c in codes if c in specs_by_code})

        gathered = gather_control_evidence(
            assessment,
            control_codes=codes,
            control_id_to_code=id_to_code,
            control_domain=code_to_domain,
        )

        semantic = await self._stored_semantic(assessment.id, adapter.key)
        memory = await self._stored_memory(assessment.id, adapter.key)
        findings = await self._stored_findings(assessment.id, framework.code)

        rows_by_code = {(r.control_code or "").strip().upper(): r for r in rows}
        results_by_code: dict[str, Any] = {}
        confidence_samples: list[float] = []
        controls: list[dict[str, Any]] = []
        for code in codes:
            row = rows_by_code.get(code)
            entry = gathered.get(code)
            refs = tuple(entry.evidence) if entry else ()
            result = result_from_row(row, refs)
            results_by_code[code] = result
            spec = specs_by_code[code]
            package, _kept_refs = _normalised_package(refs, adapter.key, metrics)
            band = quality_from_score(result.evidence_score)
            confidence_samples.append(float(result.confidence or 0.0))
            controls.append(_control_payload(
                result=result,
                spec=spec,
                package=package,
                quality={
                    "band": band.value,
                    "score": round(float(result.evidence_score or 0.0), 3),
                    "is_implementation_grade": is_implementation_grade(band),
                    "formats": [],
                },
                memory_item=memory.get(code, {}).get("item"),
                finding=findings.get(code),
                semantic=semantic.get(code),
                framework_code=framework.code,
            ))

        run_id = rows[0].run_id if rows else None
        counts: dict[str, int] = {}
        conclusive = 0
        for result in results_by_code.values():
            counts[result.status.value] = counts.get(result.status.value, 0) + 1
            if result.status.is_conclusive:
                conclusive += 1
        total = len(results_by_code)
        summary = {
            "run_id": run_id,
            "assessment_id": assessment.id,
            "framework": adapter.key,
            "assurance_level": adapter.spec.assurance.value,
            "asserts_operating_effectiveness": False,
            "total": total,
            "counts": counts,
            "conclusive": conclusive,
            "conclusive_pct": round(100.0 * conclusive / total, 1) if total else 0.0,
            "mean_confidence": (
                round(sum(confidence_samples) / len(confidence_samples), 3)
                if confidence_samples else 0.0
            ),
            "confidence_samples": confidence_samples,
        }
        summary.update(self._summary_extension(controls))

        return {
            "framework": adapter.key,
            "framework_code": framework.code,
            "framework_name": framework.name,
            "assurance_level": adapter.spec.assurance.value,
            "asserts_operating_effectiveness": False,
            "run_id": run_id,
            "summary": summary,
            "controls": controls,
        }

    async def _persist_semantic(
        self,
        *,
        assessment_id: str,
        run_id: str,
        framework_code: str,
        results: Sequence[SemanticResult],
        specs_by_code: Mapping[str, Any],
    ) -> None:
        for sem in results:
            spec = specs_by_code.get(sem.control_id)
            self.db.add(SemanticReviewRecord(
                assessment_id=assessment_id,
                run_id=run_id,
                framework=sem.framework,
                framework_code=framework_code,
                control_id=None,
                control_code=sem.control_id,
                domain_code=(spec.domain_code if spec else None),
                authoritative_status=sem.authoritative_status,
                confidence_band=str(sem.confidence_band.value),
                model_invoked=sem.model_invoked,
                assessment=_sem_assessment_dict(sem.assessment),
                agrees_with_deterministic=sem.agrees_with_deterministic,
                more_cautious_than_deterministic=(
                    sem.model_invoked and sem.more_cautious_than_deterministic
                ),
                model_tier=sem.model_tier,
                cache_hit=sem.cache_hit,
                fallback_reason=sem.fallback_reason,
                fallback_reasoning=sem.fallback_reasoning,
            ))
        await self.db.flush()

    async def _stored_semantic(
        self, assessment_id: str, fw_key: str
    ) -> dict[str, Any]:
        rows = (await self.db.execute(
            select(SemanticReviewRecord)
            .where(
                SemanticReviewRecord.assessment_id == assessment_id,
                SemanticReviewRecord.framework == fw_key,
            )
            .order_by(SemanticReviewRecord.created_at.desc())
        )).scalars().all()
        latest: dict[str, SemanticReviewRecord] = {}
        for r in rows:
            latest.setdefault((r.control_code or "").strip().upper(), r)
        return latest

    async def _stored_memory(
        self, assessment_id: str, fw_key: str
    ) -> dict[str, dict[str, Any]]:
        rows = (await self.db.execute(
            select(MemoryRecord)
            .where(
                MemoryRecord.assessment_id == assessment_id,
                MemoryRecord.framework == fw_key,
            )
            .order_by(MemoryRecord.created_at.desc())
            .limit(_MEMORY_LIMIT)
        )).scalars().all()
        latest: dict[str, dict[str, Any]] = {}
        for r in rows:  # ordered newest-first, so first wins
            key = (r.control_id or "").strip().upper()
            if key in latest:
                continue
            meta = dict(r.meta or {})
            matched = list(meta.get("matched_memories", []) or [])
            latest[key] = {
                "item": {
                    "record_id": r.id,
                    "control_id": r.control_id,
                    "status": r.status,
                    "finding_type": r.finding_type,
                    "classification": r.classification,
                    "basis": list(meta.get("basis", []) or []),
                    "occurrence": r.occurrence,
                    "memory_summary": meta.get("memory_summary", ""),
                    "matched_memories": matched,
                    "enrichment": r.enrichment,
                    "enrichment_error": (r.enrichment or {}).get("error"),
                    "created_at": r.created_at,
                    "remediation_status": r.remediation_status,
                    "remediation_actions": list(r.remediation_actions or []),
                    "_memory": r,
                }
            }
        return latest

    async def _stored_findings(
        self, assessment_id: str, framework_code: str
    ) -> dict[str, Any]:
        from backend.api.models.assessment import Finding

        rows = (await self.db.execute(
            select(Finding).where(
                Finding.assessment_id == assessment_id,
                Finding.framework_code == framework_code,
            )
        )).scalars().all()
        return {
            (f.control_code or "").strip().upper(): f
            for f in rows if f.control_code
        }

    def _summary_extension(
        self,
        controls: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        open_findings = 0
        high_risk = 0
        recurring = 0
        for c in controls:
            finding = c.get("finding")
            if finding and finding.get("status") in _OPEN_FINDING_STATUSES:
                open_findings += 1
                if finding.get("severity") in {"critical", "high"}:
                    high_risk += 1
            cls = c.get("historical_context", {}).get("classification", "")
            if cls in _RISK_CLASSIFICATIONS:
                recurring += 1
        return {
            "open_findings": open_findings,
            "high_risk_findings": high_risk,
            "recurring": recurring,
        }

    def _quality_payload_row(self, row: ComplianceEvaluation) -> dict[str, Any]:
        score = float(row.evidence_score or 0.0)
        band = quality_from_score(score)
        return {
            "band": band.value,
            "score": round(score, 3),
            "is_implementation_grade": is_implementation_grade(band),
            "formats": [],
        }

    def _report_payload(
        self,
        *,
        assessment: Any,
        groups: Sequence[dict[str, Any]],
        metrics: PipelineMetrics,
        mode: str,
    ) -> dict[str, Any]:
        name = getattr(assessment, "name", "") or ""
        if not name:
            name = getattr(assessment, "title", "") or ""
        return {
            "assessment_id": assessment.id,
            "assessment_name": name,
            "organization": getattr(assessment, "organization", None),
            "assurance_level": "POINT_IN_TIME",
            "generated_at": datetime.utcnow().isoformat(),
            "summary": _overall_summary(groups),
            "frameworks": groups,
            "metrics": metrics.as_dict(),
            "advisory": True,
            "mode": mode,
        }


def _sem_assessment_dict(assessment: Any) -> dict[str, Any] | None:
    """A SemanticAssessment/`SemanticAssessment`-like object → JSON dict."""
    if assessment is None:
        return None
    if hasattr(assessment, "model_dump"):
        return assessment.model_dump()
    if isinstance(assessment, dict):
        return assessment
    return None


__all__ = [
    "AssessmentPipelineError",
    "AssessmentPipelineService",
    "PipelineMetrics",
]