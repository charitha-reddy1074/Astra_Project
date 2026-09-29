"""The structured contract Groq must satisfy for Hindsight enrichment.

Unlike the semantic layer's `SemanticAssessment`, this is not a compliance
claim: it is a narration of the *relationship* between today's finding and the
organisation's stored history. It is advisory by construction, recorded on the
memory row and never near a compliance status.

Validation mirrors the semantic layer's discipline:

* `control_id` must be the control being reviewed — an enrichment about any
  other control is a refusal to trust, and is dropped.
* `relationship_to_history` must reference the supplied history; the prompt is
  written so a model cannot invent "previous exceptions" that were never shown.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The keys the model is required to emit, in the order the prompt lists them.
REQUIRED_KEYS: tuple[str, ...] = (
    "control_id",
    "relationship_to_history",
    "root_cause_assessment",
    "changed_since_last_review",
    "escalation_recommendation",
    "recommended_actions",
)


class MemoryEnrichment(BaseModel):
    """One control's model narration of how this finding sits in its history.

    Stored on `MemoryRecord.enrichment`. No field here can change a
    classification, a validation status, or a finding's lifecycle.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    control_id: str = Field(
        description="The control being reviewed. Must match the reviewed control."
    )
    relationship_to_history: str = Field(
        min_length=1,
        description="How this finding connects to the PRIOR MEMORY block — prior "
                    "findings, exceptions (active or expired), remediations.",
    )
    root_cause_assessment: str = Field(
        min_length=1,
        description="What the current evidence and history together suggest drives "
                    "this finding. Traceable to the supplied context.",
    )
    changed_since_last_review: bool | None = Field(
        default=None,
        description="True/False/unknown about whether the control's state differs "
                    "from the most recent stored finding.",
    )
    escalation_recommendation: bool = Field(
        default=False,
        description="Advisory: whether a human should escalate. The deterministic "
                    "classification is authoritative; this cannot raise or lower it.",
    )
    recommended_actions: list[str] = Field(
        default_factory=list,
        description="Actions a control owner can take, informed by history.",
    )

    @field_validator("control_id")
    @classmethod
    def _control_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("control_id must not be empty")
        return value.strip().upper()

    def as_dict(self) -> dict[str, Any]:
        return {
            "control_id": self.control_id,
            "relationship_to_history": self.relationship_to_history,
            "root_cause_assessment": self.root_cause_assessment,
            "changed_since_last_review": self.changed_since_last_review,
            "escalation_recommendation": self.escalation_recommendation,
            "recommended_actions": list(self.recommended_actions),
        }


def parse_enrichment(
    payload: dict[str, Any],
    *,
    control_id: str,
) -> MemoryEnrichment:
    """Validate one model payload against the control being reviewed.

    Raises ValueError when a required key is missing or the enrichment answers
    about a different control. Callers treat that as a contained failure —
    `enrichment` is then None and the classification stands.
    """
    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise ValueError(f"missing required key(s): {', '.join(missing)}")

    enrichment = MemoryEnrichment.model_validate(payload)
    if enrichment.control_id != control_id.strip().upper():
        raise ValueError(
            f"enrichment names control '{enrichment.control_id}' but the finding "
            f"under review is '{control_id}'"
        )
    return enrichment


__all__ = ["REQUIRED_KEYS", "MemoryEnrichment", "parse_enrichment"]