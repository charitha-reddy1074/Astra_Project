"""Evidence-to-control mapping: proposing an edge, and refusing to fake one.

NO LLM and NO DATABASE. This is the pure decision layer that answers two
questions the rest of the platform needed answered but had no place for:

1. *Which controls might this artefact be about?* — retrieval, reusing the
   existing `ControlRetriever` so a mapping can only ever name a control the
   assessment already has in scope. There is no path from an evidence file to a
   control id that is not in the framework dataset.
2. *Is this edge strong enough to act on?* — scoring, against a fixed
   threshold. Below it, the edge is persisted as a candidate and the control is
   held at REVIEW_REQUIRED until a human decides.

Why the threshold exists
------------------------
The whole control-centric workflow rests on a claim the platform cannot verify
on its own: that a document is actually about a control. A confident, well-
formatted PDF can be filed against a control it never evidenced, and without a
gate that mistake becomes a PASS that no one ever checked. So the gate is
structural — `requires_review` is computed from a constant and a score, and
`status.py` acts on it. An LLM's opinion can raise or lower a *relevance score*
on a proposed mapping (see `semantic/`), but it can never set `requires_review`
directly, and it can never invent a control that retrieval did not return.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from backend.api.compliance.enums import (
    EvidenceNature,
    MappingReviewStatus,
)
from backend.api.compliance.types import ControlSpec, EvidenceRef

#: Below this, the edge is a *question for a human*, not a candidate fact.
#: Set where it is deliberately conservative: a false PASS is far more costly
#: than a false REVIEW_REQUIRED, and the cost of the latter is one click.
AUTO_ACCEPT_THRESHOLD = 0.35

#: At or above this the mapping is confident enough to skip human review and
#: feed the deterministic evaluation directly. Kept well above
#: `AUTO_ACCEPT_THRESHOLD` so a mapping has to be clearly right, not marginally
#: right, before nobody looks at it.
CONFIDENT_THRESHOLD = 0.60

#: An artefact this long or shorter cannot be read as demonstrating a control,
#: whatever it matches on. A filename, a title and a one-line note can share
#: every term of a requirement statement and still prove nothing.
MIN_EVIDENCE_CHARS_FOR_MAPPING = 200

#: A control statement longer than this is truncated before its clauses are
#: matched, so a pathological dataset entry cannot dominate the match count.
_MAX_CLAUSE_CHARS = 240

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset("""
a an and are as at be been by for from has have in into is it its of on or
that the their there these this to was were will with which who whom whose
not no nor but if then than so such can could may might must shall should
""".split())


def _tokens(text: str) -> set[str]:
    return {
        t for t in _TOKEN_RE.findall((text or "").lower())
        if t not in _STOPWORDS and len(t) > 2
    }


def control_text(control: ControlSpec) -> str:
    """The text a mapping is matched against: statement, requirement, clauses."""
    parts = [
        control.control_id,
        control.domain_name,
        control.statement,
        control.requirement.requirement_id,
        control.requirement.text,
        *[clause[:_MAX_CLAUSE_CHARS] for clause in control.requirement.clauses],
    ]
    return " ".join(p for p in parts if p)


def matched_sections(evidence_text: str, control: ControlSpec) -> list[str]:
    """Which specific parts of the control the artefact actually speaks to.

    Deliberately clause-level rather than "yes, it matches the control": a
    reviewer deciding whether to confirm an edge needs to see *where* it
    matches, and a control with 11 points of focus needs to be able to have one
    of them evidenced and the other ten not.

    Returns the requirement id, the domain and any clause whose distinctive
    terms appear in the artefact. A clause that matches on a single common word
    is not reported — one shared term between a document and a 20-word clause is
    noise, not a match.
    """
    body = _tokens(evidence_text)
    if not body:
        return []

    found: list[str] = []
    requirement_id = (control.requirement.requirement_id or "").strip()
    if requirement_id:
        clause_tokens = _tokens(control.requirement.text)
        if clause_tokens:
            overlap = body & clause_tokens
            if len(overlap) / len(clause_tokens) >= 0.25:
                found.append(requirement_id)

    for clause in control.requirement.clauses:
        clause_body = _tokens(clause[:_MAX_CLAUSE_CHARS])
        if len(clause_body) < 3:
            continue
        overlap = body & clause_body
        # Require a meaningful share of the clause, not one lucky term.
        if len(overlap) / len(clause_body) >= 0.30:
            found.append(clause[:_MAX_CLAUSE_CHARS].strip())

    if control.domain_code and any(
        term in _tokens(evidence_text) for term in _tokens(control.domain_name)
    ):
        found.insert(0, control.domain_code)
    return found[:8]


def relevance(
    evidence_text: str,
    control: ControlSpec,
    *,
    evidence_nature: EvidenceNature = EvidenceNature.UNDETERMINED,
) -> float:
    """0.0-1.0 confidence that `evidence_text` is about `control`.

    Three signals, weighted so the strongest one dominates:

    * **term coverage** — what share of the control's own vocabulary the artefact
      actually uses. Bounded at 0.65, because shared vocabulary is necessary but
      never sufficient: every vendor security page mentions "access control".
    * **explicit identity** — the artefact names the control id or requirement
      id outright. This is close to dispositive and is worth up to 0.35.
    * **nature fit** — evidence that can demonstrate a control outranks a
      document that only describes it. Worth a small bonus, deliberately small:
      nature is about *what an artefact can prove*, never about whether it is
      *about* this control, and letting it outweigh topical overlap would map
      every screenshot to every control.
    """
    if len((evidence_text or "").strip()) < MIN_EVIDENCE_CHARS_FOR_MAPPING:
        # Too short to demonstrate anything. Not zero, because a thin artefact
        # can still be a legitimately-suggested candidate a human wants to see.
        return 0.05

    body = _tokens(evidence_text)
    if not body:
        return 0.0

    control_tokens = _tokens(control_text(control))
    if not control_tokens:
        return 0.0

    coverage = len(body & control_tokens) / len(control_tokens)
    score = min(0.65, coverage * 0.65)

    upper = evidence_text.upper()
    if control.control_id.strip().upper() and control.control_id.strip().upper() in upper:
        score += 0.35
    requirement_id = (control.requirement.requirement_id or "").strip().upper()
    if requirement_id and len(requirement_id) >= 3 and requirement_id in upper:
        score += 0.20

    if evidence_nature in (EvidenceNature.TECHNICAL_IMPLEMENTATION,
                           EvidenceNature.OPERATIONAL_ACTIVITY):
        score += 0.05
    elif evidence_nature is EvidenceNature.POLICY_DESIGN:
        score += 0.02

    return max(0.0, min(1.0, score))


@dataclass(frozen=True)
class MappingDecision:
    """One proposed edge, with everything a reviewer needs to judge it."""

    evidence_id: str
    control_id: str
    framework: str
    framework_code: str = ""
    domain_code: str = ""
    domain_name: str = ""
    control_row_id: str | None = None
    relevance_score: float = 0.0
    matched_sections: tuple[str, ...] = ()
    mapping_reason: str = ""
    evidence_source_name: str = ""
    evidence_kind: str = ""
    evidence_nature: str = EvidenceNature.UNDETERMINED.value
    evidence_format: str = ""
    mapping_method: str = "lexical"
    review_status: str = MappingReviewStatus.PENDING_REVIEW.value
    requires_review: bool = True

    @property
    def is_confident(self) -> bool:
        return self.relevance_score >= CONFIDENT_THRESHOLD

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_source_name": self.evidence_source_name,
            "evidence_kind": self.evidence_kind,
            "evidence_nature": self.evidence_nature,
            "evidence_format": self.evidence_format,
            "framework": self.framework,
            "framework_code": self.framework_code,
            "control_id": self.control_row_id,
            "control_code": self.control_id,
            "domain_code": self.domain_code,
            "domain_name": self.domain_name,
            "relevance_score": round(self.relevance_score, 4),
            "matched_sections": list(self.matched_sections),
            "mapping_reason": self.mapping_reason,
            "mapping_method": self.mapping_method,
            "review_status": self.review_status,
            "requires_review": self.requires_review,
        }


def _reason(
    score: float,
    sections: Sequence[str],
    control: ControlSpec,
    nature: EvidenceNature,
) -> str:
    """A sentence a reviewer can act on. Never empty, never a bare number."""
    if score >= CONFIDENT_THRESHOLD:
        lead = (
            f"Strong match ({score:.0%}): the artefact refers to "
            f"{control.control_id} using the control's own language."
        )
    elif score >= AUTO_ACCEPT_THRESHOLD:
        lead = (
            f"Plausible match ({score:.0%}): topical overlap with "
            f"{control.control_id}, below the {CONFIDENT_THRESHOLD:.0%} confidence bar."
        )
    else:
        lead = (
            f"Weak match ({score:.0%}): little of {control.control_id}'s language "
            f"appears in the artefact. Offered as a candidate only — confirm "
            "before this counts as evidence."
        )
    if sections:
        lead += f" Matches: {'; '.join(sections[:3])}."
    if nature is EvidenceNature.OPERATIONAL_ACTIVITY:
        lead += (
            " The artefact is operational activity over a period, so it cannot "
            "carry a Type 1 conclusion on its own."
        )
    elif nature is EvidenceNature.UNDETERMINED:
        lead += (
            " The artefact's nature could not be classified, so it is recorded "
            "but not relied on."
        )
    return lead


def decide(
    evidence: EvidenceRef,
    control: ControlSpec,
    *,
    control_row_id: str | None = None,
    framework_code: str = "",
    nature: EvidenceNature | str = EvidenceNature.UNDETERMINED,
    method: str = "lexical",
    score: float | None = None,
    sections: Sequence[str] | None = None,
) -> MappingDecision:
    """Score one (evidence, control) pair and decide whether a human must look.

    The single place `requires_review` is computed. Everything downstream —
    persistence, the evaluation run, the report — reads that flag rather than
    re-deriving a verdict, so the review gate can only ever fire from one rule.
    """
    resolved_nature = _as_nature(nature) or _as_nature(evidence.nature)
    if resolved_nature is None:
        resolved_nature = EvidenceNature.UNDETERMINED

    resolved_score = (
        relevance(evidence.text, control, evidence_nature=resolved_nature)
        if score is None else max(0.0, min(1.0, float(score)))
    )
    resolved_sections = tuple(
        sections if sections is not None else matched_sections(evidence.text, control)
    )
    requires_review = resolved_score < AUTO_ACCEPT_THRESHOLD

    return MappingDecision(
        evidence_id=evidence.evidence_id,
        control_id=control.control_id,
        framework=control.framework,
        framework_code=framework_code,
        domain_code=control.domain_code,
        domain_name=control.domain_name,
        control_row_id=control_row_id,
        relevance_score=resolved_score,
        matched_sections=resolved_sections,
        mapping_reason=_reason(resolved_score, resolved_sections, control, resolved_nature),
        evidence_source_name=evidence.source_name or evidence.evidence_id,
        evidence_kind=evidence.kind.value,
        evidence_nature=resolved_nature.value,
        evidence_format=evidence.format,
        mapping_method=method,
        review_status=(
            MappingReviewStatus.PENDING_REVIEW.value
            if requires_review
            else MappingReviewStatus.AUTO_ACCEPTED.value
        ),
        requires_review=requires_review,
    )


def _as_nature(nature: EvidenceNature | str | None) -> EvidenceNature | None:
    if nature is None:
        return None
    if isinstance(nature, EvidenceNature):
        return nature
    try:
        return EvidenceNature(str(nature))
    except ValueError:
        return None


def decide_many(
    evidence: EvidenceRef,
    controls: Iterable[ControlSpec],
    *,
    framework_code: str = "",
    nature: EvidenceNature | str | None = None,
    method: str = "lexical",
    control_row_ids: dict[str, str] | None = None,
    min_score: float = AUTO_ACCEPT_THRESHOLD,
    limit: int = 25,
) -> list[MappingDecision]:
    """Score one artefact against many controls, strongest first.

    `min_score` is the floor for *offering* an edge at all. It is deliberately
    the same constant as the review gate, so an edge below it is never proposed
    in the first place rather than being proposed and then held — a candidate
    list is a reviewer's attention budget, and attention spent on hopeless
    matches is attention not spent on plausible ones.
    """
    decisions = [
        decide(
            evidence,
            control,
            control_row_id=(control_row_ids or {}).get(control.control_id),
            framework_code=framework_code,
            nature=nature or EvidenceNature.UNDETERMINED,
            method=method,
        )
        for control in controls
    ]
    kept = [d for d in decisions if d.relevance_score >= min_score]
    kept.sort(key=lambda d: (-d.relevance_score, d.control_id))
    return kept[:limit]


def controls_requiring_review(
    decisions: Sequence[MappingDecision],
) -> set[str]:
    """Control codes whose *only* mappings are unconfirmed.

    This is the set `status.py` receives as `mapping_requires_review`. It is
    deliberately all-or-nothing per control: a control with one solid mapping is
    evidenced, and a marginal second mapping should not hold the whole control
    hostage. A human still sees that marginal mapping in the review queue — the
    gate and the queue are separate concerns.
    """
    grouped: dict[str, list[MappingDecision]] = {}
    for decision in decisions:
        grouped.setdefault(decision.control_id, []).append(decision)
    return {
        control_code
        for control_code, items in grouped.items()
        if all(d.requires_review for d in items)
    }


__all__ = [
    "AUTO_ACCEPT_THRESHOLD",
    "CONFIDENT_THRESHOLD",
    "MIN_EVIDENCE_CHARS_FOR_MAPPING",
    "MappingDecision",
    "control_text",
    "controls_requiring_review",
    "decide",
    "decide_many",
    "matched_sections",
    "relevance",
]
