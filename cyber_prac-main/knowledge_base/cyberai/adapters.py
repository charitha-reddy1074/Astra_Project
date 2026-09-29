"""Framework adapters.

Each adapter converts one source-JSON *shape* into the shared IR
(see ir.py). Adding a future framework with a new shape means adding one
adapter class and registering it — no schema or importer changes.

Three shapes exist in f_data/:
  * FlatAdapter    — nist.json, iso.json, cis.json  (flat array of items)
  * PciAdapter     — pci.json                        (domain -> sub_domain -> control)
  * MarketAdapter  — market_assesment.json           (rich; domain -> category -> control)
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from .ir import ControlIR, FrameworkIR, MaturityLevelIR, NodeIR, QuestionIR
from .settings import FILENAME_TO_CODE


# =============================================================================
# Base
# =============================================================================
class FrameworkAdapter(ABC):
    """Base adapter. Subclasses implement `parse`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stem = path.stem.lower()
        self.raw = json.loads(path.read_text(encoding="utf-8"))

    @property
    def code(self) -> str:
        return FILENAME_TO_CODE.get(self.stem, self.stem.upper())

    @abstractmethod
    def parse(self) -> FrameworkIR:  # pragma: no cover - interface
        ...

    # -- shared helper -------------------------------------------------------
    @staticmethod
    def _synth_question(control: ControlIR) -> QuestionIR:
        """Create one default question for controls whose source has none."""
        has_maturity = bool(control.attributes.get("maturity_levels"))
        return QuestionIR(
            text=control.statement,
            question_type="MATURITY_SCALE" if has_maturity else "YES_NO",
            question_code=f"{control.control_id}-Q1",
            is_synthesized=True,
            sort_order=0,
        )


# =============================================================================
# Shape A — flat arrays (NIST, ISO, CIS)
# =============================================================================
class FlatAdapter(FrameworkAdapter):
    """Flat list of items; hierarchy encoded as domain/category codes."""

    def parse(self) -> FrameworkIR:
        items: list[dict] = self.raw
        framework_name = items[0].get("framework", self.code) if items else self.code

        fw = FrameworkIR(
            code=self.code,
            name=framework_name,
            version="1.0",
            source_format="json",
            source_file=self.path.name,
        )

        seen_nodes: dict[str, NodeIR] = {}
        dom_order: dict[str, int] = {}
        cat_order: dict[str, int] = {}

        for idx, item in enumerate(items):
            domain = item["domain"]
            category = item["category"]

            # domain node (root)
            if domain not in seen_nodes:
                dom_order[domain] = len(dom_order)
                node = NodeIR(code=domain, name=domain, node_type="domain",
                              sort_order=dom_order[domain])
                seen_nodes[domain] = node
                fw.nodes.append(node)

            # category node (child of domain)
            if category not in seen_nodes:
                cat_order[category] = len(cat_order)
                node = NodeIR(code=category, name=category, node_type="category",
                              parent_code=domain, sort_order=cat_order[category])
                seen_nodes[category] = node
                fw.nodes.append(node)

            control = ControlIR(
                control_id=item["control_id"],
                statement=item.get("statement", ""),
                node_code=category,
                name=(item.get("control_name") or None),
                requires_evidence=bool(item.get("requires_evidence", False)),
                sort_order=idx,
            )
            control.questions.append(QuestionIR(
                text=item.get("question", item.get("statement", "")),
                question_type=item.get("question_type", "FREE_TEXT"),
                question_code=f"{control.control_id}-Q1",
                sort_order=0,
            ))
            fw.controls.append(control)

        return fw


# =============================================================================
# Shape B — PCI (domain -> sub_domain -> control), controls duplicated
# =============================================================================
class PciAdapter(FrameworkAdapter):
    """PCI nests controls under sub_domains AND duplicates them under the
    domain. We treat the sub_domain path as authoritative and de-duplicate."""

    def parse(self) -> FrameworkIR:
        fw = FrameworkIR(
            code=self.code,
            name=self.raw.get("framework_name", self.code),
            version=str(self.raw.get("version", "1.0")),
            source_format=self.raw.get("source_format"),
            source_file=self.path.name,
            external_uuid=self.raw.get("framework_id"),
            ingested_at=self.raw.get("ingested_at"),
        )

        seen_control_ids: set[str] = set()
        ctrl_order = 0

        for d_idx, domain in enumerate(self.raw.get("domains", [])):
            dom_code = domain["domain_id"]
            fw.nodes.append(NodeIR(
                code=dom_code, name=domain.get("domain_name", dom_code),
                node_type="domain", sort_order=d_idx,
            ))

            # Authoritative path: controls under sub_domains.
            for s_idx, sub in enumerate(domain.get("sub_domains", [])):
                sub_code = sub["sub_domain_id"]
                fw.nodes.append(NodeIR(
                    code=sub_code, name=sub.get("sub_domain_name", sub_code),
                    node_type="sub_domain", parent_code=dom_code, sort_order=s_idx,
                ))
                for control in sub.get("controls", []):
                    ctrl_order = self._add_control(
                        fw, control, sub_code, seen_control_ids, ctrl_order)

            # Fallback: any domain-level control not seen under a sub_domain
            # is attached directly to the domain (robust against future files).
            for control in domain.get("controls", []):
                if control["control_id"] not in seen_control_ids:
                    ctrl_order = self._add_control(
                        fw, control, dom_code, seen_control_ids, ctrl_order)

        return fw

    def _add_control(self, fw, control, node_code, seen, order) -> int:
        cid = control["control_id"]
        if cid in seen:
            return order
        seen.add(cid)
        evidence_types = control.get("expected_evidence_types", []) or []
        c = ControlIR(
            control_id=cid,
            statement=control.get("control_statement", ""),
            node_code=node_code,
            requires_evidence=bool(evidence_types),
            sort_order=order,
            evidence_types=evidence_types,
            attributes={
                "maturity_levels": control.get("maturity_levels", []),
                "cross_references": control.get("cross_references", []),
            },
        )
        c.questions.append(self._synth_question(c))
        fw.controls.append(c)
        return order + 1


# =============================================================================
# Shape C — Market (rich: framework maturity model + typed questions)
# =============================================================================
class MarketAdapter(FrameworkAdapter):
    """Richest shape: domain(+POC) -> category -> control(14 attrs) -> N questions."""

    # control fields promoted into attributes JSON (everything non-universal)
    _EXTRA_FIELDS = (
        "sub_topic", "item_type", "scope_cadence", "preferred_tooling",
        "standard_referenced", "current_language_summary", "suggested_update",
        "maturity_levels", "maturity_guide", "cross_references",
    )

    def parse(self) -> FrameworkIR:
        fw = FrameworkIR(
            code=self.code,
            name=self.raw.get("name", self.code),
            version=str(self.raw.get("version", "1.0")),
            description=self.raw.get("description"),
            source_format=self.raw.get("source_format"),
            source_file=self.raw.get("source_file") or self.path.name,
            external_uuid=self.raw.get("framework_id"),
            scoring_scale=self.raw.get("scoring_scale"),
            ingested_at=self.raw.get("ingested_at"),
        )

        for ml in self.raw.get("maturity_levels", []):
            fw.maturity_levels.append(MaturityLevelIR(
                level=ml["level"],
                name=ml.get("name", ""),
                code=ml.get("code"),
                definition=ml.get("definition"),
                attributes={k: ml[k] for k in ("technology", "process") if k in ml},
            ))

        ctrl_order = 0
        for d_idx, domain in enumerate(self.raw.get("domains", [])):
            dom_code = domain["domain_id"]
            fw.nodes.append(NodeIR(
                code=dom_code, name=domain.get("domain_name", dom_code),
                node_type="domain", description=domain.get("description"),
                sort_order=d_idx,
                attributes={k: domain[k] for k in ("point_of_contact", "red_flags")
                            if domain.get(k)},
            ))

            for c_idx, cat in enumerate(domain.get("categories", [])):
                cat_code = cat["category_id"]
                fw.nodes.append(NodeIR(
                    code=cat_code, name=cat.get("category_name", cat_code),
                    node_type="category", parent_code=dom_code,
                    criteria_statement=cat.get("criteria_statement"),
                    sort_order=c_idx,
                ))

                for control in cat.get("controls", []):
                    evidence_types = control.get("expected_evidence_types", []) or []
                    c = ControlIR(
                        control_id=control["control_id"],
                        statement=control.get("control_statement", ""),
                        node_code=cat_code,
                        name=control.get("sub_topic"),
                        requires_evidence=bool(evidence_types),
                        sort_order=ctrl_order,
                        evidence_types=evidence_types,
                        attributes={k: control[k] for k in self._EXTRA_FIELDS
                                    if k in control},
                    )
                    for q_idx, q in enumerate(control.get("questions", [])):
                        c.questions.append(QuestionIR(
                            text=q.get("question_text", ""),
                            question_type=q.get("question_type", "FREE_TEXT"),
                            question_code=q.get("question_id"),
                            choices=q.get("choices"),
                            help_text=q.get("help_text"),
                            weight=q.get("weight"),
                            sort_order=q_idx,
                        ))
                    if not c.questions:
                        c.questions.append(self._synth_question(c))
                    fw.controls.append(c)
                    ctrl_order += 1

        return fw


# =============================================================================
# Registry
# =============================================================================
_REGISTRY: dict[str, type[FrameworkAdapter]] = {
    "nist": FlatAdapter,
    "iso": FlatAdapter,
    "cis": FlatAdapter,
    "pci": PciAdapter,
    "market_assesment": MarketAdapter,
    "market_assessment": MarketAdapter,
}


def adapter_for(path: Path) -> FrameworkAdapter:
    """Resolve the correct adapter for a source file.

    Known files map by stem. Unknown files are auto-detected by shape:
    a top-level list => FlatAdapter; a dict with 'domains' => PciAdapter.
    """
    stem = path.stem.lower()
    cls = _REGISTRY.get(stem)
    if cls is not None:
        return cls(path)

    # Heuristic fallback for future files not yet registered.
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return FlatAdapter(path)
    if isinstance(raw, dict) and "domains" in raw:
        return PciAdapter(path)
    raise ValueError(f"No adapter can handle {path.name}")
