"""
ingestion/domain_tables.py — Two-tier domain ID/name assignment for any control ID.

Tier 1: deterministic prefix table for known frameworks.
Tier 2: ID-prefix clustering for unknown/arbitrary frameworks.
"""
from __future__ import annotations

import re
from typing import Dict, Tuple


# ---------------------------------------------------------------------------
# Tier 1 — known framework prefix → (domain_id, domain_name)
#
# Keys are UPPERCASE prefixes extracted from control IDs.
# Longer keys (e.g. "A.5") are attempted before shorter ones (e.g. "A"),
# so ISO 27001 annex clauses resolve correctly.
# ---------------------------------------------------------------------------

_KNOWN_PREFIX_MAP: Dict[str, Tuple[str, str]] = {
    # ── NIST CSF 2.0 ─────────────────────────────────────────────────────
    "GV": ("GV", "Govern"),
    "ID": ("ID", "Identify"),
    "PR": ("PR", "Protect"),
    "DE": ("DE", "Detect"),
    "RS": ("RS", "Respond"),
    "RC": ("RC", "Recover"),

    # ── ISO 27001:2022 — Annex A clauses ─────────────────────────────────
    "A.5": ("A.5", "Organizational Controls"),
    "A.6": ("A.6", "People Controls"),
    "A.7": ("A.7", "Physical Controls"),
    "A.8": ("A.8", "Technological Controls"),

    # ── NIST SP 800-53 rev5 — family codes ───────────────────────────────
    "AC": ("AC", "Access Control"),
    "AT": ("AT", "Awareness and Training"),
    "AU": ("AU", "Audit and Accountability"),
    "CA": ("CA", "Assessment Authorization and Monitoring"),
    "CM": ("CM", "Configuration Management"),
    "CP": ("CP", "Contingency Planning"),
    "IA": ("IA", "Identification and Authentication"),
    "IR": ("IR", "Incident Response"),
    "MA": ("MA", "Maintenance"),
    "MP": ("MP", "Media Protection"),
    "PE": ("PE", "Physical and Environmental Protection"),
    "PL": ("PL", "Planning"),
    "PM": ("PM", "Program Management"),
    "PS": ("PS", "Personnel Security"),
    "PT": ("PT", "PII Processing and Transparency"),
    "RA": ("RA", "Risk Assessment"),
    "SA": ("SA", "System and Services Acquisition"),
    "SC": ("SC", "System and Communications Protection"),
    "SI": ("SI", "System and Information Integrity"),
    "SR": ("SR", "Supply Chain Risk Management"),
}

# ── PCI DSS v3/v4 — top-level requirement number ─────────────────────────────
# Kept SEPARATE from _KNOWN_PREFIX_MAP because numeric IDs ("1".."12") are
# ambiguous: CIS Controls, COBIT and many other frameworks also use numeric IDs.
# These are only applied when the framework context indicates PCI DSS (see
# assign_domain); otherwise the numeric prefix falls through to Tier 2 and the
# LLM-supplied domain name is used.
_PCI_NUMERIC_MAP: Dict[str, Tuple[str, str]] = {
    "1":  ("REQ-1",  "Network Security Controls"),
    "2":  ("REQ-2",  "Secure Configurations"),
    "3":  ("REQ-3",  "Account Data Protection"),
    "4":  ("REQ-4",  "Cryptography in Transit"),
    "5":  ("REQ-5",  "Anti-Malware"),
    "6":  ("REQ-6",  "Secure Development"),
    "7":  ("REQ-7",  "Access Control"),
    "8":  ("REQ-8",  "Authentication"),
    "9":  ("REQ-9",  "Physical Access"),
    "10": ("REQ-10", "Log Management"),
    "11": ("REQ-11", "Security Testing"),
    "12": ("REQ-12", "Information Security Policies"),
}


# ---------------------------------------------------------------------------
# Prefix extractor — Tier 1 and Tier 2 both use this
# ---------------------------------------------------------------------------

def _extract_prefix(control_id: str) -> str:
    """
    Extract the coarse prefix of a control ID.

    Priority order (first match wins):
      1. Letter(s) then .digit — ISO-style: A.5, A.8
      2. Two-or-more letters + optional trailing digit(s) — NIST CSF PR,
         SP 800-53 AC, SOC 2 CC6, HIPAA
      3. Single leading digit(s) — PCI DSS requirement number: 1, 10
      4. Fallback: first segment before any separator

    Examples:
      "PR.AA-01"       → "PR"
      "ID.AM-1"        → "ID"
      "A.5.1.1"        → "A.5"
      "A.8.1.1"        → "A.8"
      "AC-2"           → "AC"
      "CC6.1"          → "CC6"
      "1.2.3"          → "1"
      "10.1.2"         → "10"
      "HIPAA-164.312"  → "HIPAA"
      "CIS-1.1"        → "CIS"
    """
    # ISO-style: single/double letter + .digit (A.5, A.10, B.1)
    m = re.match(r'^([A-Za-z]{1,3}\.\d{1,2})(?:[.\-\s]|$)', control_id)
    if m:
        candidate = m.group(1).upper()
        # Only use this form if it's in the known map (avoids false positives
        # like "GV.OC" being split as "GV.O")
        if candidate in _KNOWN_PREFIX_MAP:
            return candidate

    # Alpha/alphanumeric prefix: PR, AC, CC6, HIPAA (2+ alpha chars, optional trailing digits)
    m = re.match(r'^([A-Za-z]{2,}\d*)', control_id)
    if m:
        return m.group(1).upper()

    # Single-letter prefix (rare)
    m = re.match(r'^([A-Za-z])(?:[.\-\s\d]|$)', control_id)
    if m:
        return m.group(1).upper()

    # Pure numeric prefix: PCI DSS 1.x, 10.x
    m = re.match(r'^(\d{1,2})[\.\-\s]', control_id)
    if m:
        return m.group(1)

    # Fallback: first token before any separator
    first = re.split(r'[\.\-\s]', control_id)[0]
    return first.upper() or "GENERAL"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assign_domain(
    control_id: str,
    llm_domain_name: str = "",
    framework_name: str = "",
) -> Tuple[str, str]:
    """
    Return (domain_id, domain_name) for a given control_id.

    Tier 1 — known alpha prefix table (deterministic):
        "PR.AA-01"   → ("PR",    "Protect")
        "AC-2"       → ("AC",    "Access Control")
        "A.5.1.1"    → ("A.5",   "Organizational Controls")

    PCI numeric table — only when framework_name indicates PCI DSS:
        "1.2.3"      → ("REQ-1", "Network Security Controls")   [PCI context]

    Tier 2 — ID-prefix clustering for unknown frameworks:
        "CC6.1"      → ("CC6",   llm_domain_name or "CC6")
        "HIPAA-164"  → ("HIPAA", llm_domain_name or "HIPAA")
        "1.1"        → ("1",     llm_domain_name or "1")        [non-PCI numeric]

    The llm_domain_name is used as the human-readable label in Tier 2 when the
    LLM's suggestion is available; it is never used for Tier 1 (table always wins).
    """
    prefix = _extract_prefix(control_id)
    if prefix in _KNOWN_PREFIX_MAP:
        return _KNOWN_PREFIX_MAP[prefix]

    # Numeric prefixes are PCI-specific: only apply the PCI map when the
    # framework is actually PCI DSS. Otherwise fall through to Tier 2 so the
    # LLM-derived domain name (not "Network Security Controls" etc.) is used.
    if prefix in _PCI_NUMERIC_MAP and "pci" in str(framework_name or "").lower():
        return _PCI_NUMERIC_MAP[prefix]

    # Tier 2: prefix is the domain_id; use LLM label if reasonable, else the prefix
    domain_name = (llm_domain_name or "").strip() or prefix
    return (prefix, domain_name)
