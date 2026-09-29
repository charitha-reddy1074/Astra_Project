"""The structured contract Groq must satisfy.

This is the only shape the model is allowed to speak. The prompt asks for it and
`SemanticAssessment` enforces it, so a model that drifts — prose, markdown, a
missing key, an invented control ID — is rejected here rather than being stored
on an evaluation row.

Two rules are enforced by construction rather than by asking the model nicely:

* `control_id` must be one the caller actually retrieved. A model that answers
  about a control nobody supplied is hallucinating, so the value is checked
  against the allowed set and the whole assessment is refused on a mismatch.
* The confidence band is derived from the *policy*, not read off the model. The
  model states its own `confidence` as supporting detail, but the band that
  reaches storage is computed in `policy.py` from the deterministic result, so
  a confident-sounding model cannot promote a weak result.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.api.compliance.enums import ComplianceStatus, ConfidenceBand

#: The keys the model is required to emit, in the order the prompt lists them.
REQUIRED_KEYS: tuple[str, ...] = (
    "decision",
    "confidence",
    "control_id",
    "evidence_strength",
    "reasoning",
    "identified_gaps",
    "recommended_actions",
)

Decision = Literal["PASS", "PARTIAL", "FAIL", "INSUFFICIENT_EVIDENCE"]
EvidenceStrength = Literal["none", "weak", "moderate", "strong"]


class SemanticAssessment(BaseModel):
    """One control's model reading of the supplied evidence.

    Deliberately *not* the compliance answer. `decision` is the model's
    interpretation and is recorded alongside the deterministic status; the
    deterministic status is what the platform claims.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    decision: Decision = Field(
        description="Model's interpretation of the evidence against the requirement."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model's own confidence in this reading. Supporting detail only — "
                    "the stored band comes from the policy.",
    )
    control_id: str = Field(description="Must be one of the retrieved control IDs.")
    evidence_strength: EvidenceStrength = Field(
        description="How strongly the supplied evidence addresses the requirement."
    )
    reasoning: str = Field(
        min_length=1,
        description="Facts observed in the evidence first, then interpretation, "
                    "explicitly separated.",
    )
    identified_gaps: list[str] = Field(
        default_factory=list,
        description="Concrete uncovered parts of the requirement. Empty only if none.",
    )
    recommended_actions: list[str] = Field(
        default_factory=list,
        description="Actions a control owner can take to close the gaps.",
    )

    @field_validator("control_id")
    @classmethod
    def _control_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("control_id must not be empty")
        return value.strip().upper()

    @field_validator("reasoning")
    @classmethod
    def _reasoning_bounded(cls, value: str) -> str:
        # An unbounded "reasoning" is where a model starts narrating its own
        # process and re-asserting the prompt; cap it and keep the tail useful.
        if len(value) > 2000:
            return value[:2000].rsplit(" ", 1)[0] + "..."
        return value

    @model_validator(mode="after")
    def _decisions_are_closed(self) -> "SemanticAssessment":
        if self.decision not in {s.value for s in ComplianceStatus}:
            raise ValueError(f"decision '{self.decision}' is not a compliance status")
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "confidence": round(self.confidence, 3),
            "control_id": self.control_id,
            "evidence_strength": self.evidence_strength,
            "reasoning": self.reasoning,
            "identified_gaps": list(self.identified_gaps),
            "recommended_actions": list(self.recommended_actions),
        }


class SemanticBatch(BaseModel):
    """The batched shape: one entry per control, for the batched request path.

    Batching several controls into one call is the main cost lever, so the model
    is asked for a list keyed by `control_id` instead of one object per call.
    """

    model_config = ConfigDict(extra="ignore")

    assessments: list[SemanticAssessment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_controls(self) -> "SemanticBatch":
        seen: set[str] = set()
        for item in self.assessments:
            if item.control_id in seen:
                raise ValueError(f"duplicate assessment for control {item.control_id}")
            seen.add(item.control_id)
        return self


class SemanticResult(BaseModel):
    """What the platform records for one control: model reading + what survived.

    `assessment` is None whenever the model could not be trusted or was not
    called at all (no evidence, timeout, malformed output, failure). The
    `fallback_reason` then says which, so an auditor can tell "the model agreed"
    apart from "the model never got a say".
    """

    model_config = ConfigDict(extra="ignore")

    framework: str
    control_id: str
    authoritative_status: str = Field(
        description="The deterministic Part 1 status. This is the platform's claim."
    )
    confidence_band: ConfidenceBand = Field(
        description="Policy-derived, never the model's own number. A control whose "
                    "review fell back is capped at MEDIUM, because HIGH requires a "
                    "framework match that was never confirmed."
    )
    model_invoked: bool = False
    assessment: SemanticAssessment | None = None
    agrees_with_deterministic: bool | None = None
    #: Set when the model read the evidence more strictly than the deterministic
    #: rules. Recorded, and it is the only direction a disagreement may move the
    #: *presented* status in — never toward a better claim.
    more_cautious_than_deterministic: bool = False
    model_tier: str = ""
    cache_hit: bool = False
    fallback_reason: str = ""
    fallback_reasoning: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "control_id": self.control_id,
            "authoritative_status": self.authoritative_status,
            "confidence_band": self.confidence_band.value,
            "model_invoked": self.model_invoked,
            "assessment": self.assessment.as_dict() if self.assessment else None,
            "agrees_with_deterministic": self.agrees_with_deterministic,
            "more_cautious_than_deterministic": self.more_cautious_than_deterministic,
            "model_tier": self.model_tier,
            "cache_hit": self.cache_hit,
            "fallback_reason": self.fallback_reason,
            "fallback_reasoning": self.fallback_reasoning,
        }


def allowed_control_ids(controls: Sequence[Any]) -> tuple[str, ...]:
    """Upper-cased control IDs the model is permitted to answer about."""
    return tuple(str(getattr(c, "control_id", c)).strip().upper() for c in controls)


def parse_assessment(
    payload: Mapping[str, Any],
    *,
    allowed_ids: Sequence[str],
) -> SemanticAssessment:
    """Validate one model payload against the retrieved control set.

    Raises ValueError when the model answered about a control that was not
    offered. Callers treat that as a refusal, not as a result to be repaired:
    a model that invents an identifier is not to be trusted about the rest of
    its answer either.
    """
    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise ValueError(f"missing required key(s): {', '.join(missing)}")

    assessment = SemanticAssessment.model_validate(payload)
    permitted = {cid.strip().upper() for cid in allowed_ids}
    if assessment.control_id not in permitted:
        raise ValueError(
            f"model returned control_id '{assessment.control_id}' which was not "
            f"retrieved (allowed: {', '.join(sorted(permitted)) or 'none'})"
        )
    return assessment


__all__ = [
    "REQUIRED_KEYS",
    "ConfidenceBand",
    "Decision",
    "EvidenceStrength",
    "SemanticAssessment",
    "SemanticBatch",
    "SemanticResult",
    "allowed_control_ids",
    "parse_assessment",
]
