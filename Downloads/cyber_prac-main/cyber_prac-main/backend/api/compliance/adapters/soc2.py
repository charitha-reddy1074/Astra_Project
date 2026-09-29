"""SOC 2 Type 1 adapter (AICPA Trust Services Criteria).

TYPE 1 — AND ONLY TYPE 1
-----------------------
A Type 1 examination addresses the *design and implementation* of controls as of
a specified date. It does not, and this adapter must never let an evaluation
imply, that a control *operated effectively* over a period — that is a Type 2
conclusion and is out of scope for this platform.

Two mechanisms enforce that, both deterministic:

1. `FrameworkSpec.assurance` is pinned to ``TYPE_1`` and every evaluation row
   carries it, so no downstream surface can present a Type 2 result.
2. `cap_evidence` / `status_ceiling` detect *period-scoped* evidence
   (population samples, exception reports, "throughout the period" logs,
   quarterly rollups) and refuse to let it alone support a passing Type 1
   conclusion. Operating-performance evidence is a Type 2 artefact; on its own it
   proves nothing about design at the assessment date.

The dataset this reads is the AICPA SOC 2 compliance guide on AWS
(`soc2_tsc_2017.json`): Common Criteria CC1-CC9 plus the category criteria
PI1, A1, C1 and P1-P8.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from backend.api.compliance.adapters.base import FrameworkAdapter, make_spec
from backend.api.compliance.enums import AssuranceLevel, ComplianceStatus
from backend.api.compliance.types import DatasetControl, RequirementSpec

#: Series whose weight reflects the exposure a failure creates. This is platform
#: policy for prioritisation, NOT a claim about AICPA's own prioritisation.
_CRITICAL_SERIES = frozenset({"CC6", "CC7"})
_STANDARD_SERIES = frozenset({"CC1", "CC2", "CC3", "CC4", "CC5", "CC8", "CC9",
                              "A", "C", "PI", "P"})
_INFORMATIONAL_SERIES = frozenset({"CUEC", "AWS"})

#: Evidence classes the SOC 2 dataset itself names, taken from the dataset's
#: `supporting_reference_content.control_documentation_template` ("Expected
#: evidence: policy documents, logs, screenshots, and Config reports") and
#: `aws_evidence_sources`. Referenced for confidence sharpening only.
_SOC2_EVIDENCE_VOCABULARY: tuple[str, ...] = (
    "policy",
    "procedure",
    "walkthrough",
    "configuration",
    "log",
    "screenshot",
    "report",
    "attestation",
)

#: Markers of evidence that describes control *operation over a period* — a
#: Type 2 artefact. Matched case-insensitively against extracted evidence text.
_PERIOD_MARKERS: tuple[str, ...] = (
    "throughout the period",
    "for the period",
    "during the period",
    "over the period",
    "over the review period",
    "period under review",
    "period ended",
    "review period",
    "sample of",
    "sampled items",
    "sampling",
    "population of",
    "full population",
    "exception report",
    "exceptions report",
    "quarterly report",
    "monthly report",
    "audit log sample",
    "ticket sample",
    "year over year",
    "rolling 12",
    "as of the period",
)

#: Markers of evidence that describes the control *as designed and implemented*
#: at the assessment date — exactly what a Type 1 conclusion may rest on.
_POINT_IN_TIME_MARKERS: tuple[str, ...] = (
    "walkthrough",
    "as-designed",
    "as designed",
    "design document",
    "architecture",
    "diagram",
    "current state",
    "current configuration",
    "screenshot",
    "policy",
    "procedure",
    "standard operating procedure",
    "org chart",
    "charter",
    "approved",
    "revision history",
    "version control",
    "configuration export",
    "approved on",
    "effective date",
)

_PERIOD_RE = re.compile("|".join(re.escape(m) for m in _PERIOD_MARKERS))
_POINT_IN_TIME_RE = re.compile("|".join(re.escape(m) for m in _POINT_IN_TIME_MARKERS))

#: Ceiling applied to the evidence half of the score when every usable artefact
#: is period-scoped. Paired with `status_ceiling`, so the result is PARTIAL at
#: best regardless of how the blended arithmetic lands.
_PERIOD_ONLY_EVIDENCE_CAP = 0.40

_TYPE_1_GAP = (
    "Only period-scoped evidence was supplied. A SOC 2 Type 1 examination "
    "addresses the design and implementation of the control as of the assessment "
    "date; operating-effectiveness evidence over a period is a Type 2 artefact "
    "and cannot support a Type 1 conclusion."
)
_TYPE_1_RECOMMENDATION = (
    "Supply design and implementation evidence for the Type 1 assessment date, "
    "for example a policy or procedure with its approval record, a current "
    "configuration export or architecture diagram, or a dated walkthrough "
    "narrative. Retain the period-scoped material separately as Type 2 support."
)


class Soc2Type1Adapter(FrameworkAdapter):
    key = "soc2-type-1"
    name = "AICPA SOC 2 Trust Services Criteria (Type 1)"
    aliases = ("soc2", "soc 2", "soc-2", "soc2_tsc", "soc2-tsc",
               "trust services criteria", "aicpa")
    #: Matches SOC2_TSC_2017, "AICPA SOC 2 - Trust Services Criteria", etc.
    required_tokens = frozenset({"soc2", "tsc"})

    spec = make_spec(
        key=key,
        name=name,
        version="TSC 2017",
        description=(
            "AICPA Trust Services Criteria — Common Criteria CC1-CC9 with the "
            "category criteria PI1, A1, C1 and P1-P8. Evaluated at Type 1 scope."
        ),
        assurance=AssuranceLevel.TYPE_1,
        assurance_statement=(
            "Type 1 scope. This evaluation addresses the design and implementation "
            "of controls as of the assessment date. It makes NO assertion about the "
            "operating effectiveness of controls over any period; that is a Type 2 "
            "conclusion and is out of scope."
        ),
        pass_threshold=0.70,
    )

    # ── criticality (platform prioritisation policy, not an AICPA claim) ─────

    def criticality(self, control_id: str) -> str:
        series = self._series_code(control_id or "")
        if series in _CRITICAL_SERIES:
            return "critical"
        if series in _STANDARD_SERIES:
            return "standard"
        if series in _INFORMATIONAL_SERIES:
            return "informational"
        return "standard"

    # ── requirements ────────────────────────────────────────────────────────

    def build_requirement(self, control: DatasetControl) -> RequirementSpec:
        """The criterion objective is the requirement; the `points_of_focus`
        published in the dataset metadata are the clauses evidence is matched
        against. The series scope becomes the criteria statement context."""
        clauses = tuple(_meta_list(control.metadata, "points_of_focus"))
        return RequirementSpec(
            requirement_id=control.sub_domain_id or control.control_id,
            text=control.statement,
            clauses=clauses,
        )

    def expected_evidence_types(self, control: DatasetControl) -> tuple[str, ...]:
        explicit = super().expected_evidence_types(control)
        if explicit:
            return explicit
        return _SOC2_EVIDENCE_VOCABULARY

    def build_requirement_from_metadata(
        self,
        *,
        control_id: str,
        statement: str,
        category_code: str,
        category_name: str,
        metadata: Mapping[str, Any],
    ) -> RequirementSpec:
        """Requirement for a control read back from the database.

        The criterion objective is the requirement; the dataset's
        `points_of_focus` become the clauses evidence is matched against. They
        arrive inside `Control.maturity_criteria.dataset_metadata` from import.
        """
        return RequirementSpec(
            requirement_id=category_code or control_id,
            text=statement,
            clauses=tuple(_meta_list(metadata, "points_of_focus")),
        )

    def expected_evidence_types_from_metadata(self, control_id, metadata):
        return super().expected_evidence_types_from_metadata(
            control_id, metadata
        ) or _SOC2_EVIDENCE_VOCABULARY

    # ── Type 1 enforcement ──────────────────────────────────────────────────

    @staticmethod
    def classify_temporality(text: str) -> str:
        """``"period"`` or ``"point_in_time"`` for one artefact's text.

        Deterministic keyword classification. A point-in-time marker wins over a
        period marker, because a design artefact frequently *describes* the
        period it is meant to cover (e.g. a policy mandating quarterly reviews)
        and must not be misread as period evidence.
        """
        blob = (text or "").lower()
        if not blob.strip():
            return "point_in_time"
        if _POINT_IN_TIME_RE.search(blob):
            return "point_in_time"
        if _PERIOD_RE.search(blob):
            return "period"
        return "point_in_time"

    def cap_evidence(
        self,
        *,
        control_id: str,
        evidence_score: float,
        evidence_kinds: Sequence[str],
    ) -> tuple[float, list[str]]:
        """Ceiling driven by the caller's temporality classification.

        The base signature takes the *classified* evidence; the evaluator passes
        `evidence_kinds` holding the temporality label of each usable artefact
        (see `evaluator.collect_evidence_temporality`).
        """
        usable = [k for k in evidence_kinds if k in ("period", "point_in_time")]
        if not usable or any(k == "point_in_time" for k in usable):
            return evidence_score, []
        return min(evidence_score, _PERIOD_ONLY_EVIDENCE_CAP), [_TYPE_1_GAP]

    def status_ceiling(self, status: ComplianceStatus, *,
                       evidence_kinds: Sequence[str]) -> ComplianceStatus:
        """Never let period-only evidence reach PASS on a Type 1 evaluation."""
        usable = [k for k in evidence_kinds if k in ("period", "point_in_time")]
        if not usable or any(k == "point_in_time" for k in usable):
            return status
        if status is ComplianceStatus.PASS:
            return ComplianceStatus.PARTIAL
        return status

    def recommendation_for(self, status: ComplianceStatus, *,
                           evidence_kinds: Sequence[str]) -> str | None:
        """Type 1-specific remediation guidance, or None to use the default."""
        usable = [k for k in evidence_kinds if k in ("period", "point_in_time")]
        if usable and not any(k == "point_in_time" for k in usable):
            if status in (ComplianceStatus.PARTIAL, ComplianceStatus.FAIL,
                          ComplianceStatus.INSUFFICIENT_EVIDENCE):
                return _TYPE_1_RECOMMENDATION
        return None


def _meta_list(metadata: object, key: str) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    value = metadata.get(key)
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]
