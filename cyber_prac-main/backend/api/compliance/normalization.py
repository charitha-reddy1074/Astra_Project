"""Evidence normalisation: format, quality, dedup, chunking, summaries.

NO LLM and no database. This module is the pure "what is this artefact and how
much of it is worth using" layer that feeds the report and the cost accounting.
Nothing here can change a status — the deterministic evaluator grades the raw
evidence text through `status.py`, and normalisation only *annotates* it.

What it produces per item:

* **format** — the kind of artefact it is (policy document, configuration
  export, log file, access review, ...), from the filename and the content.
* **quality** — a STRONG / MEDIUM / WEAK / MISSING band. The band is mapped
  from the same deterministic rubric the evaluation already applies
  (`backend.core.evaluation_metrics.score_evidence_quality`: operational 100,
  policy-implemented 75, policy-only 40, filename-only 20, missing 0), so the
  report and the status can never disagree about how strong the evidence is.
* **content_hash** — a stable digest of the normalised text, so the same
  artefact attached to several controls (or uploaded twice) is recognised and
  its cost is not paid twice.
* **budgeting** — the same per-item / total caps the semantic grounding uses,
  applied here so the "chunks" and "sent to the model" numbers are real.

The pipeline service (`compliance/pipeline.py`) re-gathers evidence and runs
these helpers over it; the semantic layer still builds its own grounded context
from the raw refs. The two layers agree on the *caps* but keep their own views,
so neither can drift into changing the other's behaviour.
"""
from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Any, Iterable, Sequence

from backend.api.compliance.enums import EvidenceNature
from backend.api.compliance.types import EvidenceRef
from backend.core.evaluation_metrics import score_evidence_quality

#: Budget caps, deliberately equal to the semantic grounding's so an evidence
#: package is counted once whichever layer spends it.
MAX_EVIDENCE_CHARS = 1200        # per item
MAX_TOTAL_EVIDENCE_CHARS = 4000  # per control across all items
MAX_EVIDENCE_ITEMS = 8           # per control
CHUNK_CHARS = 800                # summary chunk size for very long artefacts

_WHITESPACE_RE = re.compile(r"\s+")


class EvidenceFormat(str, Enum):
    """The kind of artefact an evidence item is (advisory, never graded)."""

    POLICY_DOCUMENT = "policy_document"
    SCREENSHOT = "screenshot"
    CONFIGURATION = "configuration"
    LOG_FILE = "log_file"
    ACCESS_RECORD = "access_record"
    ASSESSMENT_ANSWER = "assessment_answer"
    MANUAL_ENTRY = "manual_entry"
    STRUCTURED_JSON = "structured_json"
    UNKNOWN = "unknown"


class EvidenceQuality(str, Enum):
    """Band over the deterministic rubric grade. STRONG/MEDIUM are
    implementation-grade; WEAK is policy-only / generic; MISSING is nothing."""

    STRONG = "STRONG"
    MEDIUM = "MEDIUM"
    WEAK = "WEAK"
    MISSING = "MISSING"


_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff"})
_JSON_EXTS = frozenset({"json", "csv", "xml", "yaml", "yml", "toml", "ini", "cfg"})
_LOG_EXTS = frozenset({"log", "csv", "tsv"})

_POLICY_MARKERS = (
    "policy", "procedure", "standard", "code of conduct", "charter", "guideline",
    "acceptable use", "iris", "incident response plan",
)
_ACCESS_MARKERS = (
    "access review", "entitlement", "user access", "recertif",
    "provisioning", "role catalogue",
)
_CONFIG_MARKERS = (
    "configuration", "config baseline", "rule set", "firewall", "terraform",
    "template: config", "aws config", "cis benchmark",
)
_LOG_MARKERS = (
    "audit log", "access log", "event log", "system log", "siem",
    "timestamp", "monitoring alert",
)
_SCREENSHOT_MARKERS = ("screenshot", "dashboard")

# ── nature markers ───────────────────────────────────────────────────────────
#
# These decide *what kind of claim* an artefact can support, which is a
# different question from the `format` markers above (which decide what the
# artefact *is*). A screenshot is a SCREENSHOT by format; whether it evidences
# design or operation depends on what it depicts, and the two must not be
# collapsed.

#: Design-grade: the control is written down, not shown working.
_DESIGN_MARKERS = (
    "policy", "procedure", "standard", "guideline", "charter", "code of conduct",
    "acceptable use", "data flow", "architecture diagram", "design document",
    "scope of", "responsibilities", "information security policy",
    "incident response plan", "business continuity plan", "risk assessment",
)

#: Operation-grade: the control is shown acting across a span of time.
_OPERATION_MARKERS = (
    "audit log", "access log", "event log", "system log", "siem",
    "monthly", "quarterly", "weekly", "last 12 months", "past 12 months",
    "sample of", "ticketing", "ticket ", "ticket sample", "alert",
    "false positive", "incident record", "review log", "sampling",
    "exception report", "change log", "patch history", "backup log",
)

#: Implementation-grade: the control is configured in a system, right now.
_IMPLEMENTATION_MARKERS = (
    "configuration", "config baseline", "rule set", "firewall", "terraform",
    "template: config", "aws config", "screenshot of", "console",
    "exported from", "policy as configured", "current configuration",
    "rule", "enabled", "deployed", "hardened", "baseline scan",
)

#: EvidenceFormat values that describe an artefact that *shows the control in
#: place* rather than merely declares it. Used by the report's
#: `is_implementation_grade`.
_IMPLEMENTATION_FORMATS = frozenset({
    EvidenceFormat.CONFIGURATION,
    EvidenceFormat.LOG_FILE,
    EvidenceFormat.ACCESS_RECORD,
    EvidenceFormat.SCREENSHOT,
    EvidenceFormat.STRUCTURED_JSON,
})


def content_hash(text: str) -> str:
    """Stable digest of an evidence item's normalised content.

    Two copies of the same artefact normalise to the same digest, so dedup is a
    hash-lookup rather than pairwise comparison. Deliberately excludes
    whitespace and case so trivially-different copies collapse together.
    """
    normalised = _WHITESPACE_RE.sub(" ", (text or "").strip().lower())
    return hashlib.sha256(normalised.encode("utf-8", errors="replace")).hexdigest()


def compact_summary(text: str, limit: int = 240) -> str:
    """A one-paragraph, whitespace-collapsed preview of an item's content."""
    compact = _WHITESPACE_RE.sub(" ", (text or "").strip())
    if len(compact) <= limit:
        return compact
    cut = compact[:limit].rsplit(" ", 1)[0]
    return f"{cut}…"


def detect_format(
    source_name: str = "",
    text: str = "",
    summary: str = "",
    kind: str = "",
) -> EvidenceFormat:
    """Infer an artefact's format from its name and its content.

    Heuristics, and intentionally conservative: a filename extension is the
    strongest signal, content markers second, and a plain text artefact with no
    marker is UNKNOWN rather than being guessed.
    """
    name = (source_name or "").lower()
    body = (text or "").lower()

    def has_any(markers: Iterable[str]) -> bool:
        return any(m in body for m in markers)

    ext = name.rsplit(".", 1)[-1] if "." in name else ""

    if ext in _IMAGE_EXTS or has_any(_SCREENSHOT_MARKERS):
        return EvidenceFormat.SCREENSHOT
    if ext == "log" or has_any(_LOG_MARKERS):
        return EvidenceFormat.LOG_FILE
    if has_any(_ACCESS_MARKERS):
        return EvidenceFormat.ACCESS_RECORD
    if ext in _JSON_EXTS or has_any(_CONFIG_MARKERS):
        return EvidenceFormat.STRUCTURED_JSON
    if ext in {"conf", "yaml", "yml", "tf", "toml", "ini", "cfg"}:
        return EvidenceFormat.CONFIGURATION
    if has_any(_POLICY_MARKERS):
        return EvidenceFormat.POLICY_DOCUMENT
    if ext in {"json", "csv", "xml"}:
        return EvidenceFormat.STRUCTURED_JSON
    if ext in {"pdf", "doc", "docx", "txt", "md"} and source_name:
        # A named document with content but no recognised marker is a manual
        # submission; without content it is a reference to a file we could not
        # preview (its format is unknowable, not "manual").
        return EvidenceFormat.MANUAL_ENTRY if body else EvidenceFormat.UNKNOWN
    if not source_name and not body:
        return EvidenceFormat.UNKNOWN
    return EvidenceFormat.UNKNOWN


def extract_metadata(text: str) -> dict[str, Any]:
    """Small, honest set of structural signals a reviewer can eyeball."""
    body = (text or "").lower()
    return {
        "chars": len(body.strip()),
        "has_timestamps": bool(re.search(r"\b(19|20)\d{2}[-/]\d{1,2}", body)),
        "has_dates": bool(re.search(r"\b\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", body)),
        "has_approval": any(m in body for m in ("approval record", "approved by", "sign-off", "signed")),
        "has_revision_history": "revision history" in body,
        "has_owner_attribution": any(m in body for m in ("security owner", "control owner", "responsible for")),
    }


def quality_from_score(evidence_score: float | None) -> EvidenceQuality:
    """Band the deterministic rubric grade to STRONG / MEDIUM / WEAK / MISSING.

    The thresholds match the rubric's own bands: operational and
    policy-implemented artefacts grade 75-100 (implementation-grade), policy-only
    and generic content grade 30-60 (weak), filename-only grades 20, nothing 0.
    The report shows STRONG/MEDIUM as `is_implementation_grade = True` — the
    exact claim the deterministic gate (`_MIN_EVIDENCE_FOR_PASS`) enforces, kept
    in the same words here.
    """
    score = float(evidence_score or 0.0)
    if score >= 0.75:
        return EvidenceQuality.STRONG
    if score >= 0.50:
        return EvidenceQuality.MEDIUM
    if score > 0.0:
        return EvidenceQuality.WEAK
    return EvidenceQuality.MISSING


def is_implementation_grade(quality: EvidenceQuality | str) -> bool:
    """True for bands a technical control can be concluded from a *document*.

    Deliberately mirrors the evidence floor in `status.py`: only evidence the
    rubric grades as implemented-in-practice counts towards a PASS. A policy
    document alone (WEAK) may be *useful* context but never proof.
    """
    label = getattr(quality, "value", quality)
    return str(label).upper() in {EvidenceQuality.STRONG.value, EvidenceQuality.MEDIUM.value}


def classify_nature(
    *,
    source_name: str = "",
    text: str = "",
    summary: str = "",
    fmt: EvidenceFormat | str = EvidenceFormat.UNKNOWN,
) -> EvidenceNature:
    """Decide what an artefact can prove: design, implementation, or operation.

    Deliberately conservative in both directions, because the cost of each error
    is asymmetric:

    * overstating an artefact as implementation-grade lets a screenshot of a
      settings page satisfy a control it never demonstrated;
    * understating an artefact as merely policy-driven hides real evidence and
      pushes reviewers to re-collect what they already supplied.

    So a clear implementation signal wins over a design signal, a clear
    operation signal beats both, and anything without a positive signal is
    UNDETERMINED rather than optimistically promoted. An artefact of unknown
    nature may be *attached* to a control but never relied on for a PASS — which
    is the same rule the framework caps enforce, expressed once, here.
    """
    fmt_value = getattr(fmt, "value", fmt)
    body = " ".join(part for part in (text or "", summary or "") if part).lower()
    name = (source_name or "").lower()

    def has(markers: Iterable[str]) -> bool:
        return any(marker in body for marker in markers)

    if not body.strip() and not name:
        return EvidenceNature.UNDETERMINED

    if has(_OPERATION_MARKERS):
        return EvidenceNature.OPERATIONAL_ACTIVITY
    if has(_IMPLEMENTATION_MARKERS) or fmt_value in {
        EvidenceFormat.CONFIGURATION.value,
        EvidenceFormat.SCREENSHOT.value,
    }:
        return EvidenceNature.TECHNICAL_IMPLEMENTATION
    if has(_DESIGN_MARKERS) or fmt_value == EvidenceFormat.POLICY_DOCUMENT.value:
        return EvidenceNature.POLICY_DESIGN
    return EvidenceNature.UNDETERMINED


def nature_capable_status(nature: EvidenceNature | str) -> bool:
    """True when artefacts of this nature can support a Type 1 conclusion on
    their own. The single place the "no Type 2 claims" rule is expressed for
    evidence, shared by the mapping layer and the report."""
    value = getattr(nature, "value", nature)
    try:
        return EvidenceNature(value).is_point_in_time
    except ValueError:
        return False


def best_nature(natures: Sequence[EvidenceNature | str]) -> EvidenceNature:
    """Strongest nature present, or UNDETERMINED when there is none."""
    resolved: list[EvidenceNature] = []
    for nature in natures:
        value = getattr(nature, "value", nature)
        try:
            resolved.append(EvidenceNature(value))
        except ValueError:
            continue
    if not resolved:
        return EvidenceNature.UNDETERMINED
    return max(resolved, key=lambda n: n.strength)


def dedupe_by_hash(
    refs: Sequence[EvidenceRef],
) -> tuple[list[EvidenceRef], int, list[EvidenceRef]]:
    """Collapse items with identical content, preserving the first copy.

    Returns ``(deduped, dropped_count, dropped_items)``. Records dropped are
    returned too so a caller can show *what* was folded (useful for the cost
    panel). Items without readable content are never deduplicated against each
    other — a reference with no text has no digest worth trusting.
    """
    deduped: list[EvidenceRef] = []
    dropped_items: list[EvidenceRef] = []
    seen: dict[str, int] = {}
    for ref in refs:
        if not ref.has_content:
            deduped.append(ref)
            continue
        key = content_hash(ref.text)
        if key in seen:
            dropped_items.append(ref)
            seen[key] += 1
            continue
        seen[key] = 1
        deduped.append(ref)
    return deduped, len(dropped_items), dropped_items


def budget_evidence(
    refs: Sequence[EvidenceRef],
    *,
    per_item_chars: int = MAX_EVIDENCE_CHARS,
    total_chars: int = MAX_TOTAL_EVIDENCE_CHARS,
    max_items: int = MAX_EVIDENCE_ITEMS,
) -> tuple[list[EvidenceRef], int, int]:
    """Trim an evidence list to the caps the semantic grounding also applies.

    Returns ``(kept, dropped_count, truncated_count)``. Items past the item
    count or the total budget are dropped; oversized single items are truncated
    in place (a new `EvidenceRef` is built, preserving the id). This is the
    number the pipeline reports as "not sent to the model", so the cost figure
    is an accounting of real, enforced limits.
    """
    kept: list[EvidenceRef] = []
    total = 0
    dropped = 0
    truncated = 0
    for ref in refs:
        if not ref.has_content:
            kept.append(ref)
            continue
        if len(kept) >= max_items:
            dropped += 1
            continue
        remaining = total_chars - total
        if remaining <= 0:
            dropped += 1
            continue
        text = ref.text
        if len(text) > min(per_item_chars, remaining):
            text = text[: max(0, min(per_item_chars, remaining))].rstrip()
            truncated += 1
        total += len(text)
        if text == ref.text:
            kept.append(ref)
        else:
            kept.append(EvidenceRef(
                evidence_id=ref.evidence_id,
                kind=ref.kind,
                summary=ref.summary,
                text=text,
                source_name=ref.source_name,
                collected_at=ref.collected_at,
                format=ref.format,
                quality=ref.quality,
                nature=ref.nature,
                content_hash=ref.content_hash,
            ))
    return kept, dropped, truncated


def evidence_chunks(text: str, chunk_chars: int = CHUNK_CHARS) -> list[str]:
    """Split a long artefact into paragraph-boundary chunks.

    The pipeline reports the total across items so "how many chunks did we
    send" is a real number for the cost panel. Paragraphs are kept whole when
    they fit; a single paragraph longer than the budget is hard-split.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > chunk_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                para[i:i + chunk_chars] for i in range(0, len(para), chunk_chars)
            )
            continue
        if current and len(current) + 1 + len(para) > chunk_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}".strip()
    if current:
        chunks.append(current)
    return chunks


def normalize_refs(
    refs: Sequence[EvidenceRef],
    framework_key: str,
) -> list[EvidenceRef]:
    """Attach format, quality and content_hash to a list of evidence refs.

    `framework_key` is the adapter key (nist-csf-2-0 / soc2-type-1) the rubric
    scopes its keywords to. Each item's quality is graded on its own text with
    the same rubric the evaluation runs on the combined text, so a weak policy
    among strong operational evidence is labelled weak, not laundered into the
    average.
    """
    out: list[EvidenceRef] = []
    for ref in refs:
        fmt = detect_format(ref.source_name, ref.text, ref.summary, ref.kind.value)
        score = 0.0
        if ref.has_content:
            score = max(
                0.0,
                min(1.0, score_evidence_quality(
                    "evidence provided", ref.text, framework_key,
                ) / 100.0),
            )
        quality = quality_from_score(score)
        out.append(EvidenceRef(
            evidence_id=ref.evidence_id,
            kind=ref.kind,
            summary=ref.summary,
            text=ref.text,
            source_name=ref.source_name,
            collected_at=ref.collected_at,
            format=fmt.value,
            quality=quality.value,
            nature=classify_nature(
                source_name=ref.source_name, text=ref.text,
                summary=ref.summary, fmt=fmt,
            ).value,
            content_hash=content_hash(ref.text) if ref.has_content else "",
        ))
    return out


__all__ = [
    "CHUNK_CHARS",
    "MAX_EVIDENCE_CHARS",
    "MAX_EVIDENCE_ITEMS",
    "MAX_TOTAL_EVIDENCE_CHARS",
    "EvidenceFormat",
    "EvidenceQuality",
    "best_nature",
    "budget_evidence",
    "classify_nature",
    "compact_summary",
    "content_hash",
    "dedupe_by_hash",
    "detect_format",
    "evidence_chunks",
    "extract_metadata",
    "is_implementation_grade",
    "nature_capable_status",
    "normalize_refs",
    "quality_from_score",
]