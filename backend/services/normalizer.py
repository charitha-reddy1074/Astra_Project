"""
normalizer.py — Framework-agnostic normaliser.

Converts raw parsed content (JSON/XML dict or plain text) from
parser.py into the canonical internal framework structure:

  Framework
  └─ domains[]
     └─ controls[]
        ├─ control_id
        ├─ control_statement
        ├─ maturity_levels[]
        ├─ expected_evidence_types[]
        ├─ cross_references[]
        └─ questions[]   (populated later by QuestionnaireBuilder)

Supported inputs (produced by FormatParser / EvidenceParser):
  • dict  — JSON / XML already parsed to a Python dict
  • str   — plain text extracted from PDF / DOCX / XLSX / TXT / CSV

For plain-text input the normaliser calls an LLM extractor to derive
a structured Framework from the raw document text.  No built-in
catalogue is assumed; the framework name, version, domains, and
controls are all inferred from the content itself.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Union

from backend.core.utils import generate_id, logger, timestamp

# ─────────────────────────────────────────
# Canonical type aliases
# ─────────────────────────────────────────

Control   = Dict[str, Any]
Domain    = Dict[str, Any]
Framework = Dict[str, Any]

# Default maturity scale reused when a control omits its own.
DEFAULT_MATURITY_LEVELS = ["Initial", "Developing", "Defined", "Managed", "Optimizing"]

# Human-readable descriptions for each NIST CSF 2.0 domain (and common CIS/ISO domains).
# Used by questionnaire builders and exporters when a domain lacks its own description.
DOMAIN_DESCRIPTIONS: Dict[str, str] = {
    # ── NIST CSF 2.0 ──────────────────────────────────────────────────────
    "GV": "Govern — Establish and monitor the organisation's cybersecurity risk management strategy, expectations, and policy.",
    "ID": "Identify — Develop an organisational understanding of cybersecurity risk to systems, people, assets, data, and capabilities.",
    "PR": "Protect — Develop and implement appropriate safeguards to ensure delivery of critical services.",
    "DE": "Detect — Develop and implement appropriate activities to identify the occurrence of a cybersecurity event.",
    "RS": "Respond — Develop and implement appropriate activities to take action regarding a detected cybersecurity incident.",
    "RC": "Recover — Develop and implement appropriate activities to maintain plans for resilience and restore capabilities impaired by a cybersecurity incident.",
    # ── NIST CSF 2.0 full sub-domain codes ───────────────────────────────
    "GV.OC": "Organisational Context — The circumstances surrounding the organisation's cybersecurity risk management decisions are understood.",
    "GV.RM": "Risk Management Strategy — The organisation's priorities, constraints, risk tolerance, and assumptions are established and used to support operational risk decisions.",
    "GV.RR": "Roles, Responsibilities & Authorities — Cybersecurity roles, responsibilities, and authorities are established, communicated, and enforced.",
    "GV.PO": "Policy — Organisational cybersecurity policy is established, communicated, and enforced.",
    "GV.OV": "Oversight — Results of organisation-wide cybersecurity risk management activities and performance are used to inform and adjust the risk management strategy.",
    "GV.SC": "Cybersecurity Supply Chain Risk Management — Cyber supply chain risk management processes are identified, established, managed, monitored, and improved.",
    "ID.AM": "Asset Management — Assets are identified and managed consistent with their relative importance to organisational objectives and the risk strategy.",
    "ID.RA": "Risk Assessment — The organisation understands the cybersecurity risk to its operations, assets, and individuals.",
    "ID.IM": "Improvement — Improvements to organisational cybersecurity risk management processes, procedures, and activities are identified across all CSF functions.",
    "PR.AA": "Identity Management, Authentication & Access Control — Access to assets is limited to authorised users, services, and hardware.",
    "PR.AT": "Awareness and Training — The organisation's personnel are provided with cybersecurity awareness and training.",
    "PR.DS": "Data Security — Data are managed consistent with the organisation's risk strategy to protect the confidentiality, integrity, and availability of information.",
    "PR.PS": "Platform Security — The hardware, software, and services of physical and virtual platforms are managed consistent with risk.",
    "PR.IR": "Technology Infrastructure Resilience — Security architectures are managed with the organisation's risk strategy to protect asset confidentiality, integrity, and availability.",
    "DE.CM": "Continuous Monitoring — Assets are monitored to find anomalies, indicators of compromise, and other potentially adverse events.",
    "DE.AE": "Adverse Event Analysis — Anomalies, indicators of compromise, and other potentially adverse events are analysed to characterise the events and detect cybersecurity incidents.",
    "RS.MA": "Incident Management — Responses to detected cybersecurity incidents are managed.",
    "RS.AN": "Incident Analysis — Investigations are conducted to ensure effective response and support forensics and recovery activities.",
    "RS.CO": "Incident Response Reporting and Communication — Response activities are coordinated with internal and external stakeholders.",
    "RS.MI": "Incident Mitigation — Activities are performed to prevent expansion of an event and mitigate its effects.",
    "RC.RP": "Incident Recovery Plan Execution — Restoration activities are performed to ensure operational availability of systems and services affected by cybersecurity incidents.",
    "RC.CO": "Incident Recovery Communication — Restoration activities are coordinated with internal and external parties.",
    # ── CIS Controls v8 ───────────────────────────────────────────────────
    "CIS-1":  "Inventory and Control of Enterprise Assets.",
    "CIS-2":  "Inventory and Control of Software Assets.",
    "CIS-3":  "Data Protection.",
    "CIS-4":  "Secure Configuration of Enterprise Assets and Software.",
    "CIS-5":  "Account Management.",
    "CIS-6":  "Access Control Management.",
    "CIS-7":  "Continuous Vulnerability Management.",
    "CIS-8":  "Audit Log Management.",
    "CIS-9":  "Email and Web Browser Protections.",
    "CIS-10": "Malware Defenses.",
    "CIS-11": "Data Recovery.",
    "CIS-12": "Network Infrastructure Management.",
    "CIS-13": "Network Monitoring and Defense.",
    "CIS-14": "Security Awareness and Skills Training.",
    "CIS-15": "Service Provider Management.",
    "CIS-16": "Application Software Security.",
    "CIS-17": "Incident Response Management.",
    "CIS-18": "Penetration Testing.",
    # ── ISO 27001:2022 clause groups ──────────────────────────────────────
    "ISO-A5":  "Organisational Controls — Policies, roles, asset management, supplier relationships.",
    "ISO-A6":  "People Controls — Screening, terms, awareness, disciplinary process.",
    "ISO-A7":  "Physical Controls — Physical security perimeters, entry controls, equipment.",
    "ISO-A8":  "Technological Controls — User endpoints, privileged access, cryptography, monitoring.",
}

# Cross-reference patterns: (regex, canonical_framework_id)
_XREF_PATTERNS: List[tuple[str, str]] = [
    (r"^NIST\s+SP\s+800-53(?:\s+(.+))?$",  "nist-sp-800-53"),
    (r"^NIST\s+SP\s+800-39(?:\s+(.+))?$",  "nist-sp-800-39"),
    (r"^NIST\s+SP\s+800-61(?:\s+(.+))?$",  "nist-sp-800-61"),
    (r"^NIST\s+SP\s+800-63(?:\s+(.+))?$",  "nist-sp-800-63"),
    (r"^NIST\s+SP\s+800-34(?:\s+(.+))?$",  "nist-sp-800-34"),
    (r"^NIST\s+SP\s+800-161(?:\s+(.+))?$", "nist-sp-800-161"),
    (r"^NIST\s+SP\s+800-150(?:\s+(.+))?$", "nist-sp-800-150"),
    (r"^NIST\s+SP\s+800-100(?:\s+(.+))?$", "nist-sp-800-100"),
    (r"^ISO\s+27001:2022(?:\s+(.+))?$",     "iso-27001-2022"),
    (r"^ISO\s+27001:2013(?:\s+(.+))?$",     "iso-27001-2013"),
    (r"^ISO\s+27005:2022(?:\s+(.+))?$",     "iso-27005-2022"),
    (r"^ISO\s+27036(?:\s+(.+))?$",          "iso-27036"),
    (r"^GDPR\s+Art(?:icle)?(?:\s+(.+))?$",  "gdpr"),
    (r"^PCI[\s-]DSS(?:\s+(.+))?$",          "pci-dss"),
    (r"^SOC\s*2(?:\s+(.+))?$",              "soc2"),
    (r"^CIS(?:\s+(.+))?$",                  "cis"),
    (r"^COBIT(?:\s+(.+))?$",               "cobit"),
    (r"^HIPAA(?:\s+(.+))?$",               "hipaa"),
]

# ─────────────────────────────────────────
# LLM-assisted text extractor
# ─────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """\
You are a cybersecurity framework parser.  Given raw document text,
extract the framework structure and return ONLY a valid JSON object —
no markdown fences, no commentary.

The JSON must match this schema exactly:
{
  "name": "<framework name>",
  "version": "<version string or empty string>",
  "publisher": "<publishing organisation or empty string>",
  "domains": [
    {
      "domain_id": "<short code, e.g. GV / AC / CM>",
      "domain_name": "<full name>",
      "description": "<one-sentence description or empty string>",
      "controls": [
        {
          "control_id": "<id>",
          "control_statement": "<full statement>",
          "maturity_levels": [],
          "expected_evidence_types": [],
          "cross_references": [],
          "questions": []
        }
      ]
    }
  ]
}

Rules:
• Extract every domain and control present in the text.
• If the document lacks explicit domains, group controls logically and
  assign short domain codes.
• Keep control_statement verbatim where possible.
• Leave maturity_levels, expected_evidence_types, cross_references,
  and questions as empty lists unless the text explicitly provides them.
• Return ONLY the JSON object.
"""


def _llm_extract(text: str, framework_name_hint: str = "") -> Dict[str, Any]:
    """
    Call an LLM to extract a structured Framework dict from plain text.

    Expects the LLM client to be importable as `llm_client` from utils,
    or falls back to a lightweight stub that returns an empty skeleton.
    Replace / wire-up the actual client as needed for your stack.
    """
    prompt = text
    if framework_name_hint:
        prompt = f"Framework hint: {framework_name_hint}\n\n{text}"

    try:
        from backend.core.utils import llm_client  # type: ignore[import]
        raw_json = llm_client.complete(
            system=_EXTRACTION_SYSTEM_PROMPT,
            user=prompt,
        )
        # Strip accidental markdown fences
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_json.strip(), flags=re.DOTALL)
        return json.loads(clean)
    except ImportError:
        logger.warning(
            "llm_client not available in utils — returning empty framework skeleton. "
            "Wire up an LLM client for plain-text extraction."
        )
        return {
            "name": framework_name_hint or "Unknown Framework",
            "version": "",
            "publisher": "",
            "domains": [],
        }
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("LLM extraction failed: %s", exc)
        return {
            "name": framework_name_hint or "Unknown Framework",
            "version": "",
            "publisher": "",
            "domains": [],
        }


# ─────────────────────────────────────────
# Normaliser
# ─────────────────────────────────────────

class FrameworkNormalizer:
    """
    Convert raw parsed content into the canonical Framework dict.

    Accepts output directly from ``FormatParser.parse()``:
      • dict  → JSON / XML path  → validated & canonicalised
      • str   → text path        → LLM-extracted then canonicalised

    No built-in catalogue is assumed.  Every framework is derived
    entirely from the supplied content.
    """

    # ── Public API ────────────────────────────────────────────────────────

    def normalize(
        self,
        content: Union[Dict[str, Any], str],
        fmt: str,
        framework_name: str = "",
    ) -> Framework:
        """
        Produce a canonical Framework dict from *content*.

        Parameters
        ----------
        content        : parsed output from FormatParser — dict or str
        fmt            : source format string ('json', 'pdf', 'docx', …)
        framework_name : optional display-name hint (used for plain-text
                         input to guide LLM extraction)
        """
        logger.info("Normalising framework (fmt=%s, hint=%r)", fmt, framework_name)

        if isinstance(content, dict):
            framework = self._from_dict(content, fmt)
        else:
            framework = self._from_text(str(content), fmt, framework_name)

        # ── Stamp metadata ────────────────────────────────────────────────
        now = timestamp()
        name    = framework.get("name") or framework.get("framework_name") or framework_name or "Unknown Framework"
        version = framework.get("version", "")

        framework["name"]           = name
        framework["framework_name"] = framework.get("framework_name") or name
        framework["version"]        = version
        framework["source_format"]  = fmt
        framework["ingested_at"]    = now
        framework["framework_id"]   = self._canonical_framework_id(
            framework.get("framework_id"), name, version
        )

        total_controls = sum(
            len(d.get("controls", [])) for d in framework.get("domains", [])
        )
        logger.info(
            "Normalised '%s' v%s — %d domain(s), %d control(s)",
            name, version or "?",
            len(framework.get("domains", [])),
            total_controls,
        )
        return framework

    # ── Flat helper ───────────────────────────────────────────────────────

    @staticmethod
    def flat_controls(framework: Framework) -> List[Dict]:
        """
        Return a flat list of controls enriched with domain / framework
        metadata — suitable for chunking and vectorisation.
        """
        flat: List[Dict] = []
        for domain in framework.get("domains", []):
            for control in domain.get("controls", []):
                flat.append({
                    **control,
                    "domain_id":          domain.get("domain_id", ""),
                    "domain_name":        domain.get("domain_name", ""),
                    "domain_description": domain.get("description", ""),
                    "framework_id":       framework.get("framework_id", ""),
                    "framework_name":     framework.get("framework_name", framework.get("name", "")),
                    "name":               framework.get("name", ""),
                    "version":            framework.get("version", ""),
                })
        return flat

    # ── Private: input-type dispatch ─────────────────────────────────────

    def _from_dict(self, raw: Dict[str, Any], fmt: str) -> Framework:
        """
        Handle JSON / XML input (already a Python dict).

        Accepts two shapes:
          1. Canonical — has ``domains`` list where each entry has ``controls``
          2. Unknown   — falls back to LLM extraction after JSON-serialising
        """
        if "domains" in raw and isinstance(raw["domains"], list):
            if all(isinstance(d, dict) and "controls" in d for d in raw["domains"]):
                logger.info("Recognised canonical domain/control structure — fast path.")
                return self._canonicalize_framework(raw, fmt)

        # Unrecognised JSON shape — serialise and let the LLM restructure it
        logger.warning(
            "JSON/XML structure not in canonical form — attempting LLM extraction."
        )
        text = json.dumps(raw, ensure_ascii=False, indent=2)
        hint = raw.get("name") or raw.get("framework_name") or raw.get("title") or ""
        return self._from_text(text, fmt, hint)

    def _from_text(
        self,
        text: str,
        fmt: str,
        framework_name_hint: str = "",
    ) -> Framework:
        """
        Handle plain-text input (PDF, DOCX, XLSX, TXT, CSV).
        Uses the LLM extractor to derive structure, then canonicalises.
        """
        logger.info("Plain-text input — using LLM extractor.")
        extracted = _llm_extract(text, framework_name_hint)
        return self._canonicalize_framework(extracted, fmt)

    # ── Canonicalisation ─────────────────────────────────────────────────

    def _canonicalize_framework(self, raw: Dict[str, Any], fmt: str) -> Framework:
        name    = raw.get("name") or raw.get("framework_name") or ""
        version = raw.get("version") or ""

        domains: List[Domain] = [
            self._canonicalize_domain(d) for d in raw.get("domains", [])
        ]

        return {
            "framework_id":   self._canonical_framework_id(raw.get("framework_id"), name, version),
            "name":           name,
            "framework_name": raw.get("framework_name") or name,
            "version":        version,
            "source_format":  fmt,
            "ingested_at":    timestamp(),
            "publisher":      raw.get("publisher", ""),
            "domains":        domains,
        }

    def _canonicalize_domain(self, domain: Dict[str, Any]) -> Domain:
        domain_id = (
            domain.get("canonical_domain_id")
            or domain.get("domain_id")
            or ""
        )
        controls = [
            self._canonicalize_control(c) for c in domain.get("controls", [])
        ]
        return {
            "domain_id":   domain_id,
            "domain_name": domain.get("domain_name", ""),
            "description": domain.get("description", ""),
            "controls":    controls,
        }

    def _canonicalize_control(self, control: Dict[str, Any]) -> Control:
        return {
            "control_id":              control.get("control_id", ""),
            "control_statement":       control.get("control_statement", ""),
            "maturity_levels":         list(
                control.get("maturity_levels") or DEFAULT_MATURITY_LEVELS
            ),
            "expected_evidence_types": list(control.get("expected_evidence_types", [])),
            "cross_references":        self._canonical_cross_references(
                control.get("cross_references", [])
            ),
            "questions":               self._canonicalize_questions(
                control.get("questions", [])
            ),
        }

    def _canonicalize_questions(self, questions: Any) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for q in questions or []:
            if isinstance(q, dict):
                result.append({
                    "question_id":   q.get("question_id") or generate_id(),
                    "question_text": q.get("question_text", ""),
                    "question_type": q.get("question_type", "free_text"),
                    "weight":        q.get("weight", 3),
                })
            elif str(q).strip():
                result.append({
                    "question_id":   generate_id(),
                    "question_text": str(q).strip(),
                    "question_type": "free_text",
                    "weight":        3,
                })
        return result

    def _canonical_cross_references(self, refs: Any) -> List[Dict[str, str]]:
        result: List[Dict[str, str]] = []
        for ref in refs or []:
            if isinstance(ref, dict):
                result.append({
                    "other_framework_id": str(
                        ref.get("other_framework_id")
                        or ref.get("framework_id")
                        or ref.get("framework")
                        or ""
                    ),
                    "other_control_id": str(
                        ref.get("other_control_id")
                        or ref.get("control_id")
                        or ref.get("control")
                        or ""
                    ),
                })
            else:
                parsed = self._parse_cross_reference(str(ref))
                if parsed:
                    result.append(parsed)
        return result

    @staticmethod
    def _parse_cross_reference(ref_text: str) -> Optional[Dict[str, str]]:
        text = ref_text.strip()
        if not text:
            return None
        for pattern, framework_id in _XREF_PATTERNS:
            m = re.match(pattern, text, flags=re.IGNORECASE)
            if m:
                return {
                    "other_framework_id": framework_id,
                    "other_control_id":   (m.group(1) or "").strip(),
                }
        # Fallback: split on whitespace, treat last token as control id
        parts = text.split()
        if len(parts) > 1:
            return {
                "other_framework_id": " ".join(parts[:-1]),
                "other_control_id":   parts[-1],
            }
        return {
            "other_framework_id": "unknown",
            "other_control_id":   text,
        }

    @staticmethod
    def _canonical_framework_id(
        existing_id: Any,
        name: str,
        version: str,
    ) -> str:
        candidate = str(existing_id or "").strip()
        _uuid_re = re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}"
            r"-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
        )
        if candidate and _uuid_re.match(candidate):
            return candidate
        slug = f"{name or 'unknown'}|{version or '0'}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, slug))