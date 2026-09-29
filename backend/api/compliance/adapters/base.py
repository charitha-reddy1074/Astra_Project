"""Framework abstraction.

A `FrameworkAdapter` is the only place that knows what makes one compliance
framework different from another: how its controls are shaped, how criticality
is derived, what evidence it expects, and — most importantly — what an
evaluation is permitted to claim.

The base class implements everything framework-agnostic (dataset reading,
requirement assembly, default criticality) so a new adapter is a few dozen
lines. Registration is explicit in `registry.py`; nothing auto-discovers.
"""
from __future__ import annotations

import re
from abc import ABC
from typing import Any, Iterable, Mapping, Sequence

from backend.api.compliance.dataset import (
    DatasetFormatError,
    is_dataset,
    statement_text,
)
from backend.api.compliance.enums import AssuranceLevel, ComplianceStatus
from backend.api.compliance.types import (
    ControlSpec,
    DatasetControl,
    FrameworkSpec,
    RequirementSpec,
)

_ID_KEYS = ("control_id", "code", "id")
_NAME_KEYS = ("control_name", "name", "title", "label")
_FRAMEWORK_NAME_KEYS = ("framework_name", "name")


def _pick(mapping: Mapping[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _listify(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


class FrameworkAdapter(ABC):
    """Base class for a framework-specific compliance adapter."""

    #: Stable internal key, e.g. ``"nist-csf-2-0"``. Persisted on every
    #: evaluation row as ``framework``.
    key: str = ""
    #: Framework name as published.
    name: str = ""
    #: Lowercase tokens that identify this framework in a framework `code`,
    #: `name` or dataset `framework_id`. Checked in order, first hit wins.
    aliases: tuple[str, ...] = ()
    #: Token set that must ALL appear in a candidate once it is split on
    #: non-alphanumerics. The robust half of matching: a code derived from a
    #: dataset name can be any shape (`NIST_CYBERSECURITY_FRAMEWORK_CSF` from
    #: "NIST Cybersecurity Framework (CSF)"), so substring aliases alone miss.
    required_tokens: frozenset[str] = frozenset()
    spec: FrameworkSpec

    # ── identity ────────────────────────────────────────────────────────────

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {t for t in re.split(r"[^a-z0-9]+", value.lower()) if t}

    @classmethod
    def matches(cls, *candidates: str | None) -> bool:
        """True when any candidate string identifies this framework."""
        if not cls.required_tokens and not cls.aliases:
            return False
        for candidate in candidates:
            if not candidate:
                continue
            raw = candidate.lower()
            if any(alias in raw for alias in cls.aliases):
                return True
            if cls.required_tokens and cls.required_tokens <= cls._tokens(raw):
                return True
        return False

    # ── controls & requirements ─────────────────────────────────────────────

    def read_dataset(self, doc: Mapping[str, Any]) -> list[DatasetControl]:
        """Read controls out of a drop-in dataset file.

        Framework-agnostic: the format contract lives in `dataset.py` and both
        bundled datasets conform to it.
        """
        if not is_dataset(doc):
            raise DatasetFormatError(
                f"{self.name or self.key}: file is not a framework dataset."
            )
        out: list[DatasetControl] = []
        for domain in doc["framework"].get("domains") or []:
            if not isinstance(domain, Mapping):
                continue
            domain_id = _pick(domain, ("domain_id", "code", "id"))
            domain_name = _pick(domain, ("domain_name", "name")) or domain_id
            subs = [s for s in (domain.get("sub_domains") or []) if isinstance(s, Mapping)]
            if not subs:
                subs = [{
                    "sub_domain_id": domain_id,
                    "sub_domain_name": domain_name,
                    "description": domain.get("description"),
                    "controls": domain.get("controls") or [],
                }]
            for sub in subs:
                sub_id = _pick(sub, ("sub_domain_id", "category_id", "code", "id")) or domain_id
                sub_name = _pick(sub, ("sub_domain_name", "category_name", "name")) or sub_id
                sub_desc = (sub.get("description") or "").strip()
                for control in sub.get("controls") or []:
                    if not isinstance(control, Mapping):
                        continue
                    code = _pick(control, _ID_KEYS)
                    statement = statement_text(control)
                    if not code or not statement:
                        continue
                    metadata = control.get("metadata")
                    out.append(DatasetControl(
                        control_id=code,
                        statement=statement,
                        domain_id=domain_id,
                        domain_name=domain_name,
                        sub_domain_id=sub_id,
                        sub_domain_name=sub_name,
                        sub_domain_description=sub_desc,
                        metadata=metadata if isinstance(metadata, Mapping) else {},
                    ))
        return out

    def build_requirement(self, control: DatasetControl) -> RequirementSpec:
        """Resolve the requirement a control is evaluated against.

        Default: the control's own statement, under its sub-domain criteria
        statement. Adapters override to enrich `clauses`.
        """
        return RequirementSpec(
            requirement_id=control.sub_domain_id or control.control_id,
            text=control.statement,
        )

    def build_requirement_from_metadata(
        self,
        *,
        control_id: str,
        statement: str,
        category_code: str,
        category_name: str,
        metadata: Mapping[str, Any],
    ) -> RequirementSpec:
        """Same as `build_requirement`, but for a control already in the database.

        The database is the system of record, so an operator's edit to a control
        statement is evaluated as written. The dataset's own metadata survived
        import inside `Control.maturity_criteria.dataset_metadata`, which is what
        an adapter reads `metadata` from here.
        """
        return RequirementSpec(
            requirement_id=category_code or control_id,
            text=statement,
        )

    def expected_evidence_types_from_metadata(
        self,
        control_id: str,
        metadata: Mapping[str, Any],
    ) -> tuple[str, ...]:
        """Expected evidence classes for a control read back from the database."""
        return tuple(_listify(metadata.get("expected_evidence_types")))

    def build_control(self, control: DatasetControl) -> ControlSpec:
        """Assemble the evaluable `ControlSpec` for a dataset control."""
        requirement = self.build_requirement(control)
        return ControlSpec(
            framework=self.key,
            framework_name=self.name,
            control_id=control.control_id,
            requirement=requirement,
            domain_code=control.domain_id,
            domain_name=control.domain_name,
            statement=control.statement,
            criticality=self.criticality(control.control_id),
            expected_evidence_types=self.expected_evidence_types(control),
            metadata=dict(control.metadata),
        )

    def build_controls(self, doc: Mapping[str, Any]) -> list[ControlSpec]:
        return [self.build_control(c) for c in self.read_dataset(doc)]

    # ── framework policy hooks ──────────────────────────────────────────────

    def criticality(self, control_id: str) -> str:
        """``critical`` | ``standard`` | ``informational`` for a control code."""
        return "standard"

    def expected_evidence_types(self, control: DatasetControl) -> tuple[str, ...]:
        """Evidence classes that would satisfy this control.

        Empty by default: the evaluation never *requires* a specific class, it
        only grades the evidence actually supplied. Adapters may return a
        vocabulary to sharpen confidence.
        """
        return tuple(_listify(control.metadata.get("expected_evidence_types")))

    def classify_temporality(self, text: str) -> str:
        """Label one artefact's extracted text ``"period"`` or ``"point_in_time"``.

        Whether the artefact describes the control as designed and implemented,
        or its operation across a span of time. The base adapter has no
        opinion and labels everything point-in-time; the SOC 2 Type 1 adapter
        overrides this because the distinction is what separates a Type 1
        conclusion from a Type 2 one.
        """
        return "point_in_time"

    def cap_evidence(
        self,
        *,
        control_id: str,
        evidence_score: float,
        evidence_kinds: Sequence[str],
    ) -> tuple[float, list[str]]:
        """Framework-specific ceiling on how much the evidence alone can prove.

        ``evidence_kinds`` carries the `classify_temporality` label of each
        usable artefact. Returns ``(capped_score, gaps)``. The default adapter
        imposes no cap; the SOC 2 Type 1 adapter uses it to refuse
        operating-effectiveness conclusions.
        """
        return evidence_score, []

    def status_ceiling(self, status: ComplianceStatus, *,
                       evidence_kinds: Sequence[str]) -> ComplianceStatus:
        """Hardest status this framework will permit, whatever the arithmetic.

        Paired with `cap_evidence` so a ceiling cannot be defeated by a
        favourable blend. The base adapter permits every status.
        """
        return status

    def recommendation_for(self, status: ComplianceStatus, *,
                           evidence_kinds: Sequence[str]) -> str | None:
        """Framework-specific recommendation override, or None for the default."""
        return None

    # ── helpers used by the base class / subclasses ─────────────────────────

    @staticmethod
    def _dataset_framework_name(doc: Mapping[str, Any]) -> str:
        fw = doc.get("framework")
        return _pick(fw if isinstance(fw, Mapping) else {}, _FRAMEWORK_NAME_KEYS)

    @classmethod
    def _series_code(cls, control_id: str) -> str:
        """Leading criteria series of a control code: ``CC6.1`` -> ``CC6``,
        ``GV.OC-01`` -> ``GV``."""
        return re.split(r"[.\-_]", control_id.strip(), maxsplit=1)[0].upper()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} key={self.key!r}>"


def make_spec(
    *,
    key: str,
    name: str,
    version: str = "",
    description: str = "",
    assurance: AssuranceLevel = AssuranceLevel.POINT_IN_TIME,
    assurance_statement: str = "",
    pass_threshold: float = 0.70,
) -> FrameworkSpec:
    return FrameworkSpec(
        key=key,
        name=name,
        version=version,
        description=description,
        assurance=assurance,
        assurance_statement=assurance_statement,
        pass_threshold=pass_threshold,
    )
