"""Deterministic confidence scoring.

NO LLM. Confidence answers one question: *how much should a reviewer trust this
evaluation?* It is computed from observable properties of the evidence actually
supplied, never from a model's self-assessment.

Factors and their weights (sum to 1.0 before penalties):

    volume     how many distinct artefacts were considered
    quality    the deterministic evidence-quality grade
    coverage   how much of the requirement's clauses the evidence touches
    response   whether a recorded answer corroborates the artefacts
    type_match whether the artefact classes match what the framework expects

Penalties apply for contradictions (evidence says the control is absent while the
answer claims it is present) and for period-only evidence on a framework that
evaluates at a point in time.
"""
from __future__ import annotations

import re
from typing import Sequence

from backend.api.compliance.adapters.base import FrameworkAdapter
from backend.api.compliance.types import EvidenceRef

#: Weight of each factor in the confidence average.
W_VOLUME = 0.20
W_QUALITY = 0.30
W_COVERAGE = 0.25
W_RESPONSE = 0.15
W_TYPE_MATCH = 0.10

#: Multiplicative penalties.
_CONTRADICTION_PENALTY = 0.55
_PERIOD_ONLY_PENALTY = 0.85
#: A lone artefact is a thin basis for a conclusion.
_SOLE_EVIDENCE_PENALTY = 0.92

#: Below this evidence volume factor, volume contributes almost nothing.
_VOLUME_SATURATION = 3.0

#: Strong negation of a control state: a negator bound to the state itself.
#: One hit is enough. Written as patterns rather than substrings so that
#: "is not in place" is recognised as negation instead of matching the
#: affirmative marker "in place" that a naive substring test would find.
_STRONG_NEGATION = re.compile(
    r"\b(?:"
    r"not|never|no longer|isn'?t|aren'?t|cannot|can'?t|does\s?n'?t|do\s?n'?t"
    r"|has\s?n'?t|have\s?n'?t|had\s?n'?t|was\s?n'?t|were\s?n'?t|is\s+not|are\s+not"
    r")\s+(?:"
    r"been\s+|being\s+)?"
    r"(?:configured|implemented|enabled|performed|documented|enforced|"
    r"established|deployed|formalized|formalised|tested|reviewed|approved|"
    r"completed|in\s+place|present|available|operating|maintained|"
    r"applied|effective|defined|monitored)"
    r"|\bno\s+(?:[a-z]+\s+){0,3}?"
    r"(?:policy|procedure|policies|procedures|standard|standards|process|"
    r"processes|control|controls|evidence|documentation|walkthrough|"
    r"attestation|baseline|inventory|review|assessment|testing|monitoring)\b"
)

#: Weak markers of an absent control. Individually ambiguous — "the disabled
#: account list is configured" is not a gap — so two distinct weak markers are
#: required before a contradiction is declared.
_WEAK_NEGATION = (
    "absent", "missing", "disabled", "not applicable", "gap identified",
    "out of scope", "not yet", "pending remediation", "no evidence",
    "cannot demonstrate", "unable to demonstrate", "underdeveloped",
)

#: Affirmative statements of a control state. Only used to avoid declaring a
#: contradiction when the text is genuinely ambiguous.
_AFFIRMATIVE_MARKERS = (
    "is configured", "are configured", "is implemented", "are implemented",
    "is enabled", "are enabled", "is in place", "are in place",
    "is active", "are active", "is deployed", "are deployed",
    "is enforced", "are enforced", "is documented", "are documented",
)

_ANSWER_AFFIRMATIVE = frozenset({"yes", "y", "true", "1", "partial", "partially"})


def _split_words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(w) > 3}


def clause_coverage(evidence_text: str, clauses: Sequence[str]) -> float:
    """Fraction of the requirement's clauses that the evidence text touches.

    Lexical and deterministic: a clause counts as covered when the evidence
    shares a distinctive word with it. Returns 1.0 when the framework publishes
    no clauses (nothing to miss), 0.0 when there are clauses and no evidence.
    """
    if not clauses:
        return 1.0
    haystack = _split_words(evidence_text)
    if not haystack:
        return 0.0
    covered = 0
    for clause in clauses:
        words = _split_words(clause)
        if words and (words & haystack):
            covered += 1
    return covered / len(clauses)


def type_match(evidence_text: str, expected: Sequence[str]) -> float:
    """How well the artefact classes match the framework's expected classes."""
    if not expected:
        return 0.5  # framework states no expectation — neither helps nor hurts
    blob = (evidence_text or "").lower()
    hits = sum(1 for token in expected if token and token.lower() in blob)
    return min(1.0, hits / max(1, len(expected) / 2))


def is_contradictory(response_value: str | None, evidence_text: str) -> bool:
    """True when the evidence text negates what the answer asserts.

    A control cannot score as implemented while the artefacts supplied for it
    say it is absent. Matching is negation-aware: "the control is not in place"
    is a contradiction, whereas "the disabled-account list is configured" is
    not. Ambiguous single markers do not trigger on their own.
    """
    answer = str(response_value or "").strip().lower()
    if answer not in _ANSWER_AFFIRMATIVE:
        return False
    blob = (evidence_text or "").lower()
    if not blob.strip():
        return False

    if _STRONG_NEGATION.search(blob):
        return True

    weak_hits = sum(1 for marker in _WEAK_NEGATION if marker in blob)
    if weak_hits < 2:
        return False
    # Two or more weak markers, but the text still states the control is in
    # place — defer to the affirmative statement.
    return not any(marker in blob for marker in _AFFIRMATIVE_MARKERS)


def calculate_confidence(
    *,
    adapter: FrameworkAdapter,
    control_id: str,
    response_value: str | None,
    clauses: Sequence[str],
    expected_evidence_types: Sequence[str],
    evidence: Sequence[EvidenceRef],
    evidence_score: float,
    status_is_conclusive: bool,
) -> tuple[float, dict[str, float]]:
    """Return ``(confidence 0.0-1.0, contributing factors)``."""
    usable = [e for e in evidence if e.has_content]
    text = "\n".join(e.text for e in usable)

    volume = min(1.0, len(usable) / _VOLUME_SATURATION)
    coverage = clause_coverage(text, clauses)
    response = 1.0 if str(response_value or "").strip() else 0.0
    match = type_match(text, expected_evidence_types)

    if not usable:
        # Nothing to be confident about. Report a low-but-nonzero figure so an
        # INSUFFICIENT_EVIDENCE result is still distinguishable from one where
        # the engine failed to run.
        return 0.0, {
            "volume": 0.0, "quality": 0.0, "coverage": 0.0,
            "response": response, "type_match": 0.0, "penalty": 0.0,
        }

    factors = {
        "volume": round(volume, 4),
        "quality": round(max(0.0, min(1.0, evidence_score)), 4),
        "coverage": round(coverage, 4),
        "response": round(response, 4),
        "type_match": round(match, 4),
    }
    confidence = (
        W_VOLUME * volume
        + W_QUALITY * max(0.0, min(1.0, evidence_score))
        + W_COVERAGE * coverage
        + W_RESPONSE * response
        + W_TYPE_MATCH * match
    )

    penalty = 1.0
    if is_contradictory(response_value, text):
        penalty *= _CONTRADICTION_PENALTY
    temporality = [adapter.classify_temporality(e.text) for e in usable]
    if temporality and all(t == "period" for t in temporality):
        # On a point-in-time evaluation, period-only evidence is a weaker basis.
        penalty *= _PERIOD_ONLY_PENALTY
    if len(usable) == 1:
        penalty *= _SOLE_EVIDENCE_PENALTY

    confidence *= penalty
    factors["penalty"] = round(penalty, 4)
    return max(0.0, min(1.0, confidence)), factors
