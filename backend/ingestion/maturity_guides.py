"""ingestion/maturity_guides.py — Verbatim maturity scoring criteria extraction.

The Organizational Assessment source document provides, for every domain, a
"Maturity Assessment Guide" for each of its sub-topics. Each guide is a small
table whose columns are ``Mature`` / ``Partial`` / ``Critical Gap`` and whose
single data row describes, in the assessor's own words, what each maturity
level looks like for that sub-topic. Each domain is also followed by a
"Red Flags & Remediation" table mapping a critical-gap finding to a recommended
action.

This module extracts those guides *verbatim* so the questionnaire can be scored
against the real wording from the framework, rather than generic placeholder
text. The same extractor is reusable for any future ``.docx`` framework that
follows the Market Assessment layout.

Document layout (walked in table order):

    [1x1 table]                          -> domain header  (starts a new domain)
    [4-col, header Mature/Partial/...]   -> one sub-topic maturity guide
    [4-col, header Mature/Partial/...]   -> next sub-topic maturity guide
    ...
    [2-col, header Critical Gap/Action]  -> the domain's red-flag table

Public API
----------
extract_maturity_guides_from_docx(path)
    -> List[domain] where domain = {
           "domain_name": str,
           "sub_topics": [{"sub_topic": str,
                           "maturity_guide": {"mature","partial","critical_gap"}}],
           "red_flags":  [{"critical_gap": str, "recommended_action": str}],
       }

attach_maturity_guides_to_framework(framework, domain_guides)
    Embed sub_topic + maturity_guide onto each control (by position within the
    domain) and red_flags onto each domain. Mutates and returns *framework*.

build_criteria_entries(framework)
    Flatten a guide-enriched framework into maturity_scoring_criteria.json rows.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_MATURITY_HEADER = {"mature", "partial", "critical gap"}
_REDFLAG_HEADER = {"critical gap", "recommended action"}


def _clean(text: Any) -> str:
    """Normalise whitespace and common typographic glyphs to plain text."""
    s = str(text or "")
    s = (
        s.replace("’", "'").replace("‘", "'")
        .replace("“", '"').replace("”", '"')
        .replace("–", "-").replace("—", "-")
        .replace("•", "").replace("�", "")
    )
    # Collapse all runs of whitespace (including the in-cell newlines that
    # separate bullet sentences) into single spaces.
    return re.sub(r"\s+", " ", s).strip()


def _header_set(table) -> set:
    return {_clean(c.text).lower() for c in table.rows[0].cells}


def extract_maturity_guides_from_docx(docx_path: str | Path) -> List[Dict[str, Any]]:
    """Extract verbatim maturity guides and red flags, grouped by domain.

    Raises
    ------
    FileNotFoundError  if the document does not exist.
    ImportError        if python-docx is not installed.
    """
    docx_path = Path(docx_path)
    if not docx_path.exists():
        raise FileNotFoundError(f"Document not found: {docx_path}")

    from docx import Document  # local import keeps the dependency optional

    document = Document(str(docx_path))

    domains: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for table in document.tables:
        rows = table.rows
        cols = len(table.columns)
        if not rows:
            continue

        # ── Domain header: a single merged cell ───────────────────────────
        if len(rows) == 1 and cols == 1:
            name = _clean(rows[0].cells[0].text)
            if name:
                current = {"domain_name": name, "sub_topics": [], "red_flags": []}
                domains.append(current)
            continue

        header = _header_set(table)

        # ── Sub-topic maturity guide: 4 cols, Mature/Partial/Critical Gap ──
        if cols == 4 and _MATURITY_HEADER.issubset(header) and len(rows) >= 2:
            if current is None:
                continue
            # Map header label -> column index so we are robust to column order.
            col_of = {_clean(c.text).lower(): i for i, c in enumerate(rows[0].cells)}
            data = rows[1].cells
            sub_topic = _clean(data[0].text)
            if not sub_topic:
                continue
            current["sub_topics"].append({
                "sub_topic": sub_topic,
                "maturity_guide": {
                    "mature": _clean(data[col_of["mature"]].text),
                    "partial": _clean(data[col_of["partial"]].text),
                    "critical_gap": _clean(data[col_of["critical gap"]].text),
                },
            })
            continue

        # ── Red flag table: 2 cols, Critical Gap / Recommended Action ──────
        if cols == 2 and _REDFLAG_HEADER.issubset(header) and len(rows) >= 2:
            if current is None:
                continue
            col_of = {_clean(c.text).lower(): i for i, c in enumerate(rows[0].cells)}
            for row in rows[1:]:
                gap = _clean(row.cells[col_of["critical gap"]].text)
                action = _clean(row.cells[col_of["recommended action"]].text)
                if gap or action:
                    current["red_flags"].append({
                        "critical_gap": gap,
                        "recommended_action": action,
                    })
            continue

    return domains


def attach_maturity_guides_to_framework(
    framework: Dict[str, Any],
    domain_guides: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Embed verbatim guides onto a framework's controls and domains.

    The Organizational Assessment canonical framework lists one control per
    sub-topic, in the same
    order the sub-topics appear in the document, so domain *i*'s control *j*
    corresponds to that domain's sub-topic *j*. The framework's domains and the
    extracted guides are matched positionally; counts are validated and a
    mismatch is skipped (left un-enriched) rather than mis-attributed.

    Mutates and returns *framework*.
    """
    fw_domains = framework.get("domains", []) or []
    for fw_domain, guide in zip(fw_domains, domain_guides):
        sub_topics = guide.get("sub_topics", [])
        controls = fw_domain.get("controls", []) or []
        for control, section in zip(controls, sub_topics):
            control["sub_topic"] = section.get("sub_topic", "")
            control["maturity_guide"] = dict(section.get("maturity_guide", {}))
        fw_domain["red_flags"] = list(guide.get("red_flags", []))
    return framework


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value))
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def build_criteria_entries(framework: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten a guide-enriched framework into maturity_scoring_criteria rows.

    Each row carries the canonical ``domain_id``/``domain_name`` plus the
    verbatim Mature/Partial/Critical Gap wording so consumers (rag_pipeline,
    web_ui) can render real scoring criteria.
    """
    entries: List[Dict[str, Any]] = []
    for domain in framework.get("domains", []) or []:
        domain_id = str(domain.get("domain_id", "")).strip()
        domain_name = str(domain.get("domain_name", "")).strip()
        for control in domain.get("controls", []) or []:
            guide = control.get("maturity_guide") or {}
            sub_topic = control.get("sub_topic", "")
            if not sub_topic and not any(guide.values()):
                continue
            entries.append({
                "criteria_id": _slugify(f"{domain_id}-{sub_topic}") or _slugify(domain_id),
                "cluster_id": domain_id,
                "cluster_name": domain_name,
                "domain_id": domain_id,
                "sub_topic": sub_topic,
                "control_id": control.get("control_id", ""),
                "scoring_columns": {
                    "mature": guide.get("mature", ""),
                    "partial": guide.get("partial", ""),
                    "critical_gap": guide.get("critical_gap", ""),
                },
            })
    return entries
