"""Catalog of platform frameworks, domains and expected evidence from canonical JSON.

Gives Ask CyberAI grounded knowledge of the platform's own data (ordered domain
lists, expected evidence types per domain) so questions like "what evidence is
required for the 4th domain of the market assessment framework" can be answered
even when vector retrieval over control text misses.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from backend.api.config import settings

# Lowercase aliases that identify a framework in a user message, keyed by file stem.
_ALIASES = {
    "cis_controls_v8_1_2": ["cis", "cis controls"],
    "framework_pci_dss_4_0": ["pci", "pci dss", "pci-dss"],
    "iso_27001_2022": ["iso", "iso 27001", "27001"],
    "market_assessment_canonical": ["market", "market assessment", "organizational assessment"],
    "nist_csf_2_0": ["nist", "csf", "nist csf"],
}
_FALLBACK_NAMES = {
    "framework_pci_dss_4_0": "PCI DSS 4.0",
    "market_assessment_canonical": "Market Assessment",
}

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
}

_EVIDENCE_WORDS = re.compile(
    r"\b(evidence|evidences|document|documents|artifact|artefact|artifacts|"
    r"proof|upload|uploads|deliverable|deliverables|required|require)\b"
)


def _iter_controls(domain: dict):
    """Yield controls whether nested directly, under categories, or sub_domains."""
    for ctrl in domain.get("controls") or []:
        yield ctrl
    for cat in domain.get("categories") or []:
        for ctrl in cat.get("controls") or []:
            yield ctrl
    for sub in domain.get("sub_domains") or []:
        for ctrl in sub.get("controls") or []:
            yield ctrl


@lru_cache(maxsize=1)
def load_catalog() -> tuple:
    frameworks = []
    for path in sorted(Path(settings.CANONICAL_FOLDER).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("domains"):
            continue
        stem = path.stem
        name = data.get("name") or _FALLBACK_NAMES.get(stem) or stem.replace("_", " ").title()
        domains = []
        for idx, dom in enumerate(data.get("domains") or [], start=1):
            evidence: list[str] = []
            for ctrl in _iter_controls(dom):
                for ev in ctrl.get("expected_evidence_types") or []:
                    if ev not in evidence:
                        evidence.append(ev)
            domains.append({
                "index": idx,
                "id": dom.get("domain_id") or "",
                "name": dom.get("domain_name") or dom.get("name") or "",
                "description": dom.get("description") or "",
                "evidence": evidence,
            })
        frameworks.append({
            "stem": stem,
            "name": name,
            "aliases": _ALIASES.get(stem, []),
            "domains": domains,
        })
    return tuple(frameworks)


def _matched_frameworks(message: str) -> list[dict]:
    msg = message.lower()
    hits = []
    for fw in load_catalog():
        terms = [fw["name"].lower()] + fw["aliases"]
        if any(re.search(rf"\b{re.escape(t)}\b", msg) for t in terms):
            hits.append(fw)
    return hits


def _matched_domains(message: str, fw: dict) -> list[dict]:
    msg = message.lower()
    indexes = set()
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+domain\b", msg):
        indexes.add(int(m.group(1)))
    for m in re.finditer(r"\bdomain\s+(\d{1,2})\b", msg):
        indexes.add(int(m.group(1)))
    for word, num in _ORDINALS.items():
        if re.search(rf"\b{word}\s+domain\b", msg):
            indexes.add(num)
    hits = []
    for dom in fw["domains"]:
        if dom["index"] in indexes:
            hits.append(dom)
            continue
        dom_id = dom["id"].lower()
        if dom_id and re.search(rf"\b{re.escape(dom_id)}\b", msg):
            hits.append(dom)
            continue
        if dom["name"] and dom["name"].lower() in msg:
            hits.append(dom)
    return hits


def build_platform_context(message: str) -> str:
    """Compact, always-correct platform context for the Ask CyberAI prompt."""
    parts = ["Frameworks loaded in this platform, with their domains in order:"]
    for fw in load_catalog():
        doms = "; ".join(
            f"{d['index']}. {d['id']} {d['name']}".strip() for d in fw["domains"]
        )
        parts.append(f"- {fw['name']}: {doms}")

    wants_evidence = bool(_EVIDENCE_WORDS.search(message.lower()))
    for fw in _matched_frameworks(message):
        matched = _matched_domains(message, fw)
        if not matched and not wants_evidence:
            continue
        detail = matched or fw["domains"]
        parts.append(f"\nExpected evidence / required documents — {fw['name']}:")
        for d in detail:
            label = f"Domain {d['index']} ({d['id']} — {d['name']})".replace("( — ", "(")
            if d["description"]:
                parts.append(f"{label}: {d['description']}")
            ev = "; ".join(d["evidence"]) if d["evidence"] else "(none listed in canonical data)"
            parts.append(f"  Expected evidence: {ev}")
    return "\n".join(parts)

