"""Intermediate Representation (IR).

Every framework adapter, regardless of its source JSON shape, normalizes into
these dataclasses. The importer only ever writes IR to the database, so it is
completely decoupled from the quirks of each source file.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class QuestionIR:
    text: str
    question_type: str = "FREE_TEXT"
    question_code: str | None = None
    choices: list | None = None          # serialized to JSON on write
    help_text: str | None = None
    weight: float | None = None
    is_synthesized: bool = False
    sort_order: int = 0


@dataclass
class ControlIR:
    control_id: str                       # source id, unique within framework
    statement: str
    node_code: str                        # code of the parent node it hangs under
    name: str | None = None
    requires_evidence: bool = False
    sort_order: int = 0
    attributes: dict = field(default_factory=dict)   # framework-specific extras
    evidence_types: list[str] = field(default_factory=list)
    questions: list[QuestionIR] = field(default_factory=list)


@dataclass
class NodeIR:
    code: str                             # unique within framework
    name: str
    node_type: str                        # 'domain' | 'category' | 'sub_domain'
    parent_code: str | None = None        # None => root
    description: str | None = None
    criteria_statement: str | None = None
    sort_order: int = 0
    attributes: dict = field(default_factory=dict)


@dataclass
class MaturityLevelIR:
    level: int
    name: str
    code: str | None = None
    definition: str | None = None
    attributes: dict = field(default_factory=dict)


@dataclass
class FrameworkIR:
    code: str
    name: str
    version: str = "1.0"
    description: str | None = None
    source_format: str | None = None
    source_file: str | None = None
    external_uuid: str | None = None
    scoring_scale: dict | None = None
    ingested_at: str | None = None
    nodes: list[NodeIR] = field(default_factory=list)
    controls: list[ControlIR] = field(default_factory=list)
    maturity_levels: list[MaturityLevelIR] = field(default_factory=list)

    def stats(self) -> dict[str, int]:
        return {
            "nodes": len(self.nodes),
            "controls": len(self.controls),
            "questions": sum(len(c.questions) for c in self.controls),
            "maturity_levels": len(self.maturity_levels),
        }
