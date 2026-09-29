"""Gather evidence for a control from the platform's existing storage.

NO LLM. This module only *collects* what already exists — it does not judge it.
Judging happens in `status.py` / `confidence.py`.

Sources, in the same precedence the report uses so the two surfaces can never
disagree about what evidence a control has:

  1. `Evidence` rows attached to the `Response` for that control;
  2. files the owner provided against a `DocumentRequest` scoped to that control;
  3. files provided against a `DocumentRequest` scoped to the control's domain.

Previews are read with `report_builder._read_preview` — the `<file>.preview.txt`
sidecar written at upload time — so the compliance layer grades exactly the text
the report displays.

An evidence item with no readable preview is still returned (the reviewer should
see that a file was referenced), but `EvidenceRef.has_content` is False and the
evidence gate in `status.py` treats it as ungraded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from backend.api.compliance.enums import EvidenceKind
from backend.api.compliance.types import EvidenceRef

#: Cap on the preview text read per file, matching report_builder so a control is
#: never graded on materially more text than the report shows.
_PREVIEW_PER_FILE = 20_000

#: Cap on the combined text handed to the grader for one control.
_PREVIEW_TOTAL = 120_000

#: A DocumentRequest only counts once the owner has supplied it and the assessor
#: has not rejected it.
_PROVIDED_STATUSES = frozenset({"provided", "accepted"})


@dataclass
class ControlEvidence:
    """Everything gathered for one control: its answer, and its artefacts."""
    control_code: str
    domain_code: str = ""
    response_value: str | None = None
    #: Normalised 0.0-1.0. None when no answer was recorded at all, which the
    #: evaluator treats differently from a recorded answer of zero.
    response_score: float | None = None
    answer_notes: str = ""
    evidence: list[EvidenceRef] = field(default_factory=list)

    @property
    def has_answer(self) -> bool:
        return self.response_value is not None and str(self.response_value).strip() != ""


def _read_preview(file_path: str) -> str:
    """Read the .preview.txt sidecar, reusing the report's own reader."""
    from backend.api.services.report_builder import _read_preview as read
    return read(file_path)


def normalise_response_score(raw: Any) -> float | None:
    """Coerce a stored `Response.score` to 0.0-1.0.

    The platform persists answer scores on a 0-100 scale; anything unparseable
    becomes None so the evaluator records "no answer score" rather than a
    fabricated zero.
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 1.0:
        value = value / 100.0
    return max(0.0, min(1.0, value))


def _clip(text: str) -> str:
    text = text or ""
    return text[:_PREVIEW_PER_FILE]


def _document_request_evidence(
    assessment: Any,
    settings: Any,
) -> tuple[dict[str, list[EvidenceRef]], dict[str, list[EvidenceRef]]]:
    """Owner-provided files, grouped by control code and by domain code."""
    by_control: dict[str, list[EvidenceRef]] = {}
    by_domain: dict[str, list[EvidenceRef]] = {}
    requests = getattr(assessment, "document_requests", None) or []
    if not requests:
        return by_control, by_domain

    evidence_dir = os.path.join(settings.EVIDENCE_FOLDER, assessment.id)
    for request in requests:
        if (request.status or "") not in _PROVIDED_STATUSES:
            continue
        provided_at = getattr(request, "provided_at", None)
        for stored_name in (request.provided_files or []):
            text = _clip(_read_preview(os.path.join(evidence_dir, stored_name)))
            ref = EvidenceRef(
                evidence_id=f"dr:{request.id}:{stored_name}",
                kind=EvidenceKind.DOCUMENT_REQUEST,
                summary=(request.note or request.evidence_type or "")[:500],
                text=text,
                source_name=stored_name,
                collected_at=provided_at,
            )
            # A request scoped to a control vouches for that control only; a
            # domain-level request applies across the domain.
            if request.control_code:
                by_control.setdefault(request.control_code, []).append(ref)
            elif request.domain_code:
                by_domain.setdefault(request.domain_code, []).append(ref)
    return by_control, by_domain


def _response_evidence_by_control(
    assessment: Any,
) -> dict[str, ControlEvidence]:
    """Each control's recorded answer and its response-attached evidence.

    Keyed by `Response.control_id`, which is normally the Control row UUID.
    `resolve_control_codes` re-keys it to control codes afterwards.

    Answer and evidence live on the same `ControlEvidence` rather than in a
    parallel dict, so there is only one place a control's data can be.
    """
    answers: dict[str, ControlEvidence] = {}

    for response in (getattr(assessment, "responses", None) or []):
        raw_key = getattr(response, "control_id", None)
        if not raw_key:
            continue
        entry = answers.setdefault(raw_key, ControlEvidence(control_code=raw_key))
        if response.response_value is not None and not entry.has_answer:
            entry.response_value = response.response_value
            entry.response_score = normalise_response_score(response.score)
        if response.notes and not entry.answer_notes:
            entry.answer_notes = response.notes

        for item in (getattr(response, "evidence", None) or []):
            entry.evidence.append(EvidenceRef(
                evidence_id=item.id,
                kind=EvidenceKind.RESPONSE_ATTACHMENT,
                summary=(item.description or "")[:500],
                text=_clip(_read_preview(item.file_path)),
                source_name=item.file_name,
                collected_at=getattr(item, "uploaded_at", None),
            ))
    return answers


def resolve_control_codes(
    answers: Mapping[str, ControlEvidence],
    control_id_to_code: Mapping[str, str],
) -> None:
    """Re-key `answers` from `Response.control_id` UUIDs to control codes.

    Mutates in place: replaces keys, merging when several responses point at the
    same control (the importer generates one question per control, but an
    operator can add follow-ups, and all of their evidence must count).
    """
    if not control_id_to_code:
        return
    for raw_key, entry in list(answers.items()):
        code = control_id_to_code.get(raw_key)
        if not code or code == raw_key:
            continue
        target = answers.get(code)
        if target is None:
            answers[code] = ControlEvidence(
                control_code=code,
                domain_code=entry.domain_code,
                response_value=entry.response_value,
                response_score=entry.response_score,
                answer_notes=entry.answer_notes,
                evidence=list(entry.evidence),
            )
        else:
            if not target.has_answer and entry.has_answer:
                target.response_value = entry.response_value
                target.response_score = entry.response_score
            if not target.answer_notes and entry.answer_notes:
                target.answer_notes = entry.answer_notes
            target.evidence.extend(entry.evidence)
        del answers[raw_key]


def gather_control_evidence(
    assessment: Any,
    *,
    control_codes: Sequence[str],
    control_id_to_code: Mapping[str, str] | None = None,
    control_domain: Mapping[str, str] | None = None,
    settings: Any = None,
) -> dict[str, ControlEvidence]:
    """Collect answer + evidence for every control in `control_codes`.

    Controls with nothing recorded are still present in the result, with an
    empty evidence list — which is what makes them evaluate to
    INSUFFICIENT_EVIDENCE rather than disappear from the run.
    """
    from backend.api.config import settings as app_settings

    settings = settings or app_settings

    answers = _response_evidence_by_control(assessment)
    if control_id_to_code:
        resolve_control_codes(answers, control_id_to_code)
    request_by_control, request_by_domain = _document_request_evidence(assessment, settings)

    result: dict[str, ControlEvidence] = {}
    for code in control_codes:
        entry = answers.get(code) or ControlEvidence(control_code=code)
        entry.domain_code = (control_domain or {}).get(code, entry.domain_code)

        # Response-attached evidence outranks documents the owner supplied; a
        # domain-level request is the last resort, matching the report.
        collected = list(entry.evidence)
        collected.extend(request_by_control.get(code, []))
        if not collected:
            collected.extend(request_by_domain.get(entry.domain_code, []))
        entry.evidence = collected
        result[code] = entry
    return result


def combine_text(refs: Iterable[EvidenceRef], limit: int = _PREVIEW_TOTAL) -> str:
    """Join the readable previews of `refs`, up to `limit` characters."""
    parts = [r.text for r in refs if r.text and r.text.strip()]
    return "\n".join(parts)[:limit]
