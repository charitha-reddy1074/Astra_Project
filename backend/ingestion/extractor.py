"""
ingestion/extractor.py — Groq LLM extractor adapted for HierarchicalChunker input.

Converts raw text chunks (List[RawChunk]) into a plain Framework dict that
chunker.HierarchicalChunker.chunk() consumes directly.

Key design decisions vs the original compliance_ingestion GroqCanonicalExtractor:
  1. Returns a plain dict (not a pydantic model) — chunker.py uses dict.get().
  2. Top-level key is "framework_name", not "name" (chunker.py:49 reads "framework_name").
  3. domain_id is assigned deterministically via domain_tables.assign_domain(),
     not as a random UUID — downstream metadata filtering on domain_id works.
  4. cross_references use keys "other_framework_id" / "other_control_id" because
     that is what chunker._cross_reference_text() expects (chunker.py:221-222).
  5. All other chunking (size, overlap, semantic grouping) is done by HierarchicalChunker
     — this module does NOT chunk; it only structures the text.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from groq import Groq

from backend.ingestion.domain_tables import assign_domain
from backend.core.utils import logger


# ---------------------------------------------------------------------------
# System prompt — identical to compliance_ingestion to preserve extraction quality
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are a compliance framework analyst. Your job is to extract security controls
from raw text chunks taken from official compliance documents and structure them
as canonical JSON.

STRICT RULES — violating any rule makes the output useless:

1. control_id MUST appear verbatim in the chunk text you are given.
   Do NOT invent, guess, or extrapolate IDs. If no valid ID is present, skip.

2. control_statement MUST be the actual REQUIREMENT text — sentences stating what
   MUST, SHALL, or IS REQUIRED to be done. Do NOT copy guidance, rationale, notes,
   good-practice commentary, or testing-procedure sub-steps (lines like "3.2.1.a
   Examine the policy…"). Those are NOT requirements.

3. Guidance markers such as "Note:", "Good Practice:", "Rationale:", "Guidance:",
   "Applicability Notes:", "Customized Approach Objective:" signal non-requirement
   content. Do NOT include text from those blocks.

4. Testing procedure lines matching the pattern X.X.X.[letter] (e.g. "3.2.1.a")
   are NOT controls — ignore them entirely.

5. domain_name: use the section heading hierarchy present in the chunks. For PCI DSS
   use the Requirement number to determine the domain:
     Req 1 → "Network Security Controls"
     Req 2 → "Secure Configurations"
     Req 3 → "Account Data Protection"
     Req 4 → "Cryptography in Transit"
     Req 5 → "Anti-Malware"
     Req 6 → "Secure Development"
     Req 7 → "Access Control"
     Req 8 → "Authentication"
     Req 9 → "Physical Access"
     Req 10 → "Log Management"
     Req 11 → "Security Testing"
     Req 12 → "Information Security Policies"
   For NIST CSF use the two-letter function prefix (GV→Govern, ID→Identify,
   PR→Protect, DE→Detect, RS→Respond, RC→Recover).

6. maturity_levels: provide exactly 5 strings, one per level.  These are your OWN
   analytical interpretation — they do not appear in the source document.
   Format: "Level N: <Title> - <one-sentence description specific to this control>"

7. cross_references: ONLY include if the source text EXPLICITLY says phrases like
   "maps to", "see also", "corresponds to", "aligned with", or names another
   framework standard (e.g. "ISO 27001 A.9.2.1"). Never infer cross-references.

8. expected_evidence_types: concrete artifact names (policy_document, access_logs,
   screenshots, audit_reports, configuration_files, risk_register, test_results,
   vulnerability_scan_reports, change_records, training_records, etc.).

Return ONLY valid JSON — no prose, no markdown fences — in this exact schema:
{
  "domains": [
    {
      "domain_name": "string",
      "controls": [
        {
          "control_id": "string",
          "control_statement": "string",
          "maturity_levels": [
            "Level 1: Ad-hoc - <brief>",
            "Level 2: Repeatable - <brief>",
            "Level 3: Defined - <brief>",
            "Level 4: Managed - <brief>",
            "Level 5: Optimized - <brief>"
          ],
          "expected_evidence_types": ["string"],
          "cross_references": [{"framework_id": "string", "control_id": "string"}]
        }
      ]
    }
  ]
}"""


# ---------------------------------------------------------------------------
# Control-ID format validators
# ---------------------------------------------------------------------------

_VALID_ID_RES = [
    re.compile(r"^\d{1,2}\.\d+(\.\d+)*$"),                   # PCI: 1.2.3
    re.compile(r"^[A-Z]{2,4}-\d+(\s*\(\d+\))?$"),            # NIST 800-53: AC-2
    re.compile(r"^[A-Z]{2,3}\.[A-Z]{2,4}-\d+(\.\d+)?$"),     # NIST CSF: ID.AM-1
    re.compile(r"^[A-Z]\.\d+(\.\d+){1,3}$"),                 # ISO 27001: A.9.2.1
    re.compile(r"^(?:CIS|Control|Safeguard)\s+\d+(\.\d+)+$", re.I),
    re.compile(r"^Requirement\s+\d+(\.\d+)+$", re.I),
    re.compile(r"^[A-Z]{2,}\d+\.\d+$"),                      # SOC 2 CC6.1
]


def _is_valid_id_format(cid: str) -> bool:
    return any(p.match(cid) for p in _VALID_ID_RES)


def _id_in_text(cid: str, texts: List[str]) -> bool:
    haystack = " ".join(texts)
    return bool(re.search(re.escape(cid), haystack, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class DynamicFrameworkExtractor:
    """
    Groq-powered extractor that produces a Framework dict for HierarchicalChunker.

    Output dict shape (matches chunker.py's dict.get() calls exactly):
    {
      "framework_id":   str,   # UUID
      "framework_name": str,   # chunker.py:49 reads this key
      "version":        str,
      "source_format":  str,
      "ingested_at":    str,   # ISO 8601
      "domains": [
        {
          "domain_id":   str,  # deterministic from domain_tables (not UUID)
          "domain_name": str,
          "controls": [
            {
              "control_id":              str,
              "control_statement":       str,
              "maturity_levels":         List[str],
              "expected_evidence_types": List[str],
              "cross_references": [
                {
                  "other_framework_id": str,  # chunker.py:221 reads this key
                  "other_control_id":   str,  # chunker.py:222 reads this key
                }
              ]
            }
          ]
        }
      ]
    }
    """

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        batch_size: int = 12,
    ) -> None:
        self._client = Groq(api_key=api_key)
        self._model = model
        self._batch_size = batch_size

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def extract(
        self,
        raw_chunks: List,                                       # List[RawChunk]
        framework_name: str,
        framework_version: str,
        source_format: str,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        """
        Extract controls from raw_chunks and return a Framework dict.

        progress_cb(message, current_batch, total_batches)
        """
        texts      = [c.text for c in raw_chunks]
        chunk_meta = [
            {"source_section": c.source_section, "source_page": c.source_page}
            for c in raw_chunks
        ]

        seen_ids: set = set()
        # domain_name → {"domain_id": str, "controls": List[dict]}
        merged: Dict[str, Dict[str, Any]] = {}

        batches = [
            texts[i: i + self._batch_size]
            for i in range(0, len(texts), self._batch_size)
        ]
        total = len(batches)

        for idx, batch in enumerate(batches, start=1):
            if progress_cb:
                progress_cb(f"LLM batch {idx}/{total}", idx, total)

            batch_meta = chunk_meta[(idx - 1) * self._batch_size: idx * self._batch_size]
            partial = self._call_groq(batch, framework_name, framework_version, batch_meta)
            if partial is None:
                continue

            for domain_data in partial.get("domains", []):
                llm_domain = (domain_data.get("domain_name") or "General").strip() or "General"

                for ctrl_raw in domain_data.get("controls", []):
                    ctrl = self._build_control(ctrl_raw)
                    if ctrl is None:
                        continue

                    # ID must appear verbatim in the batch text — reject hallucinations
                    if not _id_in_text(ctrl["control_id"], batch):
                        continue

                    # ID must match a known format — reject free-text strings
                    if not _is_valid_id_format(ctrl["control_id"]):
                        continue

                    # Global dedup — first occurrence across all batches wins
                    if ctrl["control_id"] in seen_ids:
                        continue
                    seen_ids.add(ctrl["control_id"])

                    # Deterministic domain assignment (Tier 1 known table, Tier 2 prefix cluster).
                    # framework_name disambiguates numeric IDs (PCI vs CIS/COBIT/etc.).
                    domain_id, domain_name = assign_domain(
                        ctrl["control_id"], llm_domain, framework_name
                    )

                    if domain_name not in merged:
                        merged[domain_name] = {"domain_id": domain_id, "controls": []}
                    merged[domain_name]["controls"].append(ctrl)

        domains = [
            {
                "domain_id":   info["domain_id"],
                "domain_name": name,
                "controls":    info["controls"],
            }
            for name, info in sorted(merged.items())
        ]

        n_controls = sum(len(d["controls"]) for d in domains)
        logger.info(
            "Extractor: %d controls across %d domains for '%s %s'",
            n_controls, len(domains), framework_name, framework_version,
        )

        return {
            "framework_id":   str(uuid.uuid4()),
            "framework_name": framework_name,                   # chunker reads "framework_name"
            "version":        framework_version,
            "source_format":  source_format,
            "ingested_at":    datetime.now(timezone.utc).isoformat(),
            "domains":        domains,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _call_groq(
        self,
        batch: List[str],
        name: str,
        version: str,
        batch_meta: Optional[List[dict]] = None,
    ) -> Optional[Dict]:
        sections: set = set()
        if batch_meta:
            for m in batch_meta:
                s = (m or {}).get("source_section")
                if s:
                    sections.add(s)
        section_hint = (
            f"\nDetected sections in this batch: {', '.join(sorted(sections))}\n"
            if sections else ""
        )
        user_msg = (
            f"Framework: {name} {version}{section_hint}\n\n"
            "Text chunks to analyse (each separated by ---):\n\n"
            + "\n\n---\n\n".join(batch)
        )
        for attempt in range(4):
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user",   "content": user_msg},
                    ],
                    temperature=0.05,
                    max_tokens=8192,
                    response_format={"type": "json_object"},
                )
                content = resp.choices[0].message.content
                try:
                    return json.loads(content)
                except json.JSONDecodeError as je:
                    # Likely a truncated response (hit max_tokens) or stray prose.
                    # Log it — silently dropping a batch loses up to batch_size
                    # controls — and retry once before giving up on the batch.
                    logger.warning(
                        "DynamicFrameworkExtractor: invalid JSON on attempt %d (%s); "
                        "%d chars returned. Retrying batch.",
                        attempt + 1, je, len(content or ""),
                    )
                    if attempt < 3:
                        continue
                    return None
            except Exception as exc:
                err = str(exc).lower()
                if "rate" in err or "429" in err:
                    time.sleep(2 ** attempt)
                    continue
                logger.warning("DynamicFrameworkExtractor error (attempt %d): %s", attempt + 1, exc)
                return None
        return None

    @staticmethod
    def _build_control(raw: Dict) -> Optional[Dict[str, Any]]:
        """Validate and normalise a single control dict from the LLM response."""
        cid  = str(raw.get("control_id")       or "").strip()
        stmt = str(raw.get("control_statement") or "").strip()
        if not cid or not stmt:
            return None

        # Reject guidance text masquerading as requirements
        guidance_phrases = (
            "alone is generally insufficient",
            "good practice",
            "note:",
            "rationale:",
            "guidance:",
        )
        if any(p in stmt.lower() for p in guidance_phrases):
            return None

        # Remap cross_reference keys to what chunker._cross_reference_text() reads:
        #   "other_framework_id" and "other_control_id"  (chunker.py:221-222)
        refs: List[Dict[str, str]] = []
        for cr in raw.get("cross_references", []):
            fid  = (cr.get("framework_id") or "").strip()
            ccid = (cr.get("control_id")   or "").strip()
            if fid and ccid:
                refs.append({"other_framework_id": fid, "other_control_id": ccid})

        return {
            "control_id":              cid,
            "control_statement":       stmt,
            "maturity_levels":         list(raw.get("maturity_levels",         []) or []),
            "expected_evidence_types": list(raw.get("expected_evidence_types", []) or []),
            "cross_references":        refs,
        }
