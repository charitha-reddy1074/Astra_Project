"""NIST CSF 2.0 adapter.

NIST CSF 2.0 is a *framework*, not an assurance engagement: it publishes
Functions (GV, ID, PR, DE, RS, RC) -> Categories (GV.OC, …) -> Subcategories
(GV.OC-01, …) whose outcome statements are the thing an organisation actually
implements. Evaluation therefore makes a point-in-time implementation claim and
is labelled POINT_IN_TIME, never TYPE_1/TYPE_2.

Criticality reuses `backend.core.evaluation_metrics._nist_criticality` — the
exact function the existing scoring engine already uses — so the compliance
layer and the report can never disagree about which NIST controls are critical.
"""
from __future__ import annotations

from backend.api.compliance.adapters.base import FrameworkAdapter, make_spec
from backend.api.compliance.enums import AssuranceLevel
from backend.api.compliance.types import ControlSpec, DatasetControl, RequirementSpec
from backend.core.evaluation_metrics import _nist_criticality

#: Evidence classes the platform recognises, reused by the deterministic quality
#: rubric in `backend.core.evaluation_metrics`. Listed here so a NIST control's
#: expected evidence is visible on the evaluation record.
_NIST_EVIDENCE_VOCABULARY: tuple[str, ...] = (
    "policy",
    "procedure",
    "configuration",
    "records",
    "report",
    "architecture",
    "log",
    "screenshot",
)


class NistCsf20Adapter(FrameworkAdapter):
    key = "nist-csf-2-0"
    name = "NIST Cybersecurity Framework 2.0"
    aliases = ("nist-csf", "nist csf", "nist_csf", "nistcsf", "nist-csf-2-0")
    #: Matches any NIST CSF code form, e.g. NIST_CYBERSECURITY_FRAMEWORK_CSF.
    required_tokens = frozenset({"nist", "csf"})

    spec = make_spec(
        key=key,
        name=name,
        version="2.0",
        description=(
            "NIST CSF 2.0 — Govern, Identify, Protect, Detect, Respond, Recover. "
            "Evaluation asserts implementation of the outcome statement as of the "
            "assessment date; it is not an audit opinion."
        ),
        assurance=AssuranceLevel.POINT_IN_TIME,
        assurance_statement=(
            "Evaluation reflects the state of the described outcome at the assessment "
            "date only. It makes no assertion about sustained operation over time."
        ),
        pass_threshold=0.70,
    )

    def criticality(self, control_id: str) -> str:
        # Reuse the existing engine's rule so the compliance layer and the
        # published report agree on criticality for the same control.
        return _nist_criticality(control_id or "")

    def build_requirement(self, control: DatasetControl) -> RequirementSpec:
        """The outcome statement *is* the requirement.

        No `clauses`: CSF 2.0 publishes no finer-grained checkable criteria
        beneath a subcategory, and the category description is a broad mission
        statement ("the organizational mission is understood and informs
        cybersecurity risk management") that good operational evidence will
        never share vocabulary with. Treating it as a clause would score
        coverage as zero on exactly the evidence that best demonstrates the
        outcome, so it is carried as context instead — see `build_control`.

        The SOC 2 adapter does publish such a decomposition (`points_of_focus`)
        and populates `clauses` from it.
        """
        return RequirementSpec(
            requirement_id=control.sub_domain_id or control.control_id,
            text=control.statement,
        )

    def expected_evidence_types(self, control: DatasetControl) -> tuple[str, ...]:
        explicit = super().expected_evidence_types(control)
        return explicit or _NIST_EVIDENCE_VOCABULARY

    def expected_evidence_types_from_metadata(self, control_id, metadata):
        """CSF 2.0 publishes no per-control evidence list, so use the shared
        vocabulary — the deterministic rubric, not the adapter, does the grading.
        """
        return super().expected_evidence_types_from_metadata(
            control_id, metadata
        ) or _NIST_EVIDENCE_VOCABULARY

    def build_control(self, control: DatasetControl) -> ControlSpec:
        """Attach the category objective as context, never as a clause."""
        spec = super().build_control(control)
        metadata = dict(control.metadata)
        if control.sub_domain_description:
            metadata.setdefault("category_objective", control.sub_domain_description)
        return ControlSpec(
            framework=spec.framework,
            framework_name=spec.framework_name,
            control_id=spec.control_id,
            requirement=spec.requirement,
            domain_code=spec.domain_code,
            domain_name=spec.domain_name,
            statement=spec.statement,
            criticality=spec.criticality,
            expected_evidence_types=spec.expected_evidence_types,
            metadata=metadata,
        )
