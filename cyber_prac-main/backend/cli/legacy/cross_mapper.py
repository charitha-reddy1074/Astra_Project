"""
cross_mapper.py — Semantic cross-framework control mapper.

For the NIST-only scope this module maps NIST CSF 2.0 controls to a
lightweight NIST CSF 1.1 reference catalogue using cosine similarity
between embeddings (the same model already loaded by VectorDBManager).

IMPORTANT — these mappings are *suggestions*, not an authoritative crosswalk.
Similarity tells you two controls are topically close; it does NOT tell you the
relationship direction (equivalent / subset / superset) or that satisfying one
satisfies the other. Every mapping is therefore tagged ``status="suggested"``
and ``review_required=True``, carries a ``confidence`` score and a coarse
``confidence_band``, and defaults its ``relationship`` to ``"RELATED"``. For
authoritative crosswalks, ingest a published mapping file via
``ingestion.cross_mapping`` instead.

Output format per mapping:
{
  "source_framework":  "NIST CSF 2.0",
  "source_control_id": "PR.AA-01",
  "target_framework":  "NIST CSF 1.1",
  "target_control_id": "PR.AC-1",
  "similarity_score":  0.89,
  "confidence":        0.89,
  "confidence_band":   "high",
  "relationship":      "RELATED",
  "method":            "semantic-similarity (lexical)",
  "status":            "suggested",
  "review_required":   True,
  "rationale":         "<brief textual reason>"
}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

import numpy as np

from backend.core.utils import logger

# Coarse confidence bands for auto-derived (non-authoritative) similarity scores.
# (cutoff, label) ordered high→low; the first cutoff a score meets wins.
_CONFIDENCE_BANDS = ((0.80, "high"), (0.65, "medium"), (0.0, "low"))


def _confidence_band(score: float) -> str:
    for cutoff, label in _CONFIDENCE_BANDS:
        if score >= cutoff:
            return label
    return "low"

# ── Lightweight NIST CSF 1.1 reference catalogue ──────────────────────────
# (selected controls only, sufficient for demo cross-mapping)
NIST_CSF_11_CATALOGUE: List[Dict[str, str]] = [
    # IDENTIFY
    {"control_id": "ID.AM-1",  "statement": "Physical devices and systems within the organization are inventoried."},
    {"control_id": "ID.AM-2",  "statement": "Software platforms and applications within the organization are inventoried."},
    {"control_id": "ID.AM-5",  "statement": "Resources (e.g. hardware, devices, data, time, personnel, and software) are prioritized based on their classification, criticality, and business value."},
    {"control_id": "ID.RA-1",  "statement": "Asset vulnerabilities are identified and documented."},
    {"control_id": "ID.RA-2",  "statement": "Cyber threat intelligence is received from information sharing forums and sources."},
    {"control_id": "ID.RA-5",  "statement": "Threats, vulnerabilities, likelihoods, and impacts are used to determine risk."},
    # PROTECT
    {"control_id": "PR.AC-1",  "statement": "Identities and credentials are issued, managed, verified, revoked, and audited for authorized devices, users and processes."},
    {"control_id": "PR.AC-3",  "statement": "Remote access is managed."},
    {"control_id": "PR.AC-4",  "statement": "Access permissions and authorizations are managed, incorporating the principles of least privilege and separation of duties."},
    {"control_id": "PR.AT-1",  "statement": "All users are informed and trained."},
    {"control_id": "PR.DS-1",  "statement": "Data-at-rest is protected."},
    {"control_id": "PR.DS-2",  "statement": "Data-in-transit is protected."},
    {"control_id": "PR.IP-1",  "statement": "A baseline configuration of information technology/industrial control systems is created and maintained incorporating security principles."},
    {"control_id": "PR.IP-3",  "statement": "Configuration change control processes are in place."},
    # DETECT
    {"control_id": "DE.CM-1",  "statement": "The network is monitored to detect potential cybersecurity events."},
    {"control_id": "DE.CM-3",  "statement": "Personnel activity is monitored to detect potential cybersecurity events."},
    {"control_id": "DE.AE-2",  "statement": "Detected events are analyzed to understand attack targets and methods."},
    {"control_id": "DE.AE-5",  "statement": "Incident alert thresholds are established."},
    # RESPOND
    {"control_id": "RS.RP-1",  "statement": "Response plan is executed during or after an incident."},
    {"control_id": "RS.AN-1",  "statement": "Notifications from detection systems are investigated."},
    {"control_id": "RS.CO-2",  "statement": "Incidents are reported consistent with established criteria."},
    {"control_id": "RS.MI-2",  "statement": "Incidents are mitigated."},
    # RECOVER
    {"control_id": "RC.RP-1",  "statement": "Recovery plan is executed during or after a cybersecurity incident."},
    {"control_id": "RC.CO-3",  "statement": "Recovery activities are communicated to internal and external stakeholders."},
]


class CrossFrameworkMapper:
    """
    Compute semantic similarity between NIST CSF 2.0 controls (source)
    and a reference catalogue of NIST CSF 1.1 controls (target).

    Similarity is computed with a lightweight lexical fallback so the
    project does not depend on local sentence-transformer embeddings.
    """

    def __init__(self, threshold: float = 0.60) -> None:
        """
        Parameters
        ----------
        threshold : minimum cosine similarity to include in output mappings.
        """
        self._threshold = threshold
        self._target_catalogue = NIST_CSF_11_CATALOGUE
        self._embedder = None
        self._target_vectors = None
        self._method_label = "semantic-similarity (lexical)"

        logger.info("CrossFrameworkMapper initialised in lexical-only mode.")

    # ── Suggestion builder ──────────────────────────────────────────────────

    def _suggestion(
        self,
        source_fw: str,
        source_id: str,
        source_stmt: str,
        target_id: str,
        target_stmt: str,
        score: float,
    ) -> Dict[str, Any]:
        """Build a decorated, clearly-non-authoritative mapping suggestion."""
        band = _confidence_band(score)
        return {
            "source_framework":  source_fw,
            # Both naming conventions kept for backward compatibility.
            "source_control_id": source_id,
            "source_control":    source_id,
            "source_statement":  (source_stmt or "")[:120],
            "target_framework":  "NIST CSF 1.1",
            "target_control_id": target_id,
            "target_control":    target_id,
            "target_statement":  (target_stmt or "")[:120],
            "similarity_score":  round(float(score), 4),
            "confidence":        round(float(score), 4),
            "confidence_band":   band,
            # Similarity cannot determine direction; default to the safe,
            # non-committal relationship and require human review.
            "relationship":      "RELATED",
            "method":            self._method_label,
            "status":            "suggested",
            "review_required":   True,
            "rationale": (
                f"Auto-derived from {self._method_label}; topical similarity "
                f"{score:.2f} ({band} confidence). Not an authoritative crosswalk "
                "— confirm relationship direction (equivalent/subset/superset) "
                "before relying on it."
            ),
        }

    # ── Public API ─────────────────────────────────────────────────────────

    def map_framework(
        self, framework: Dict[str, Any], top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Map every control in *framework* (NIST CSF 2.0) to the closest
        NIST CSF 1.1 control(s).

        Parameters
        ----------
        framework : canonical Framework dict
        top_k     : maximum number of target matches per source control

        Returns
        -------
        List of mapping dicts sorted by similarity (descending).
        """
        from backend.services.normalizer import FrameworkNormalizer
        flat_controls = FrameworkNormalizer.flat_controls(framework)
        source_fw = framework.get("framework_name", framework.get("name", "NIST CSF 2.0"))

        mappings: List[Dict[str, Any]] = []
        use_embeddings = bool(self._embedder and self._target_vectors is not None)
        source_vectors = (
            self._embedder.embed_documents([c.get("control_statement", "") for c in flat_controls])
            if use_embeddings else None
        )

        for idx, ctrl in enumerate(flat_controls):
            if use_embeddings:
                best = self._find_top_k(np.array(source_vectors[idx]), top_k=top_k)
            else:
                best = self._lexical_top_k(ctrl.get("control_statement", ""), top_k=top_k)
            for match in best:
                if match["similarity_score"] >= self._threshold:
                    mappings.append(self._suggestion(
                        source_fw,
                        ctrl.get("control_id", ""),
                        ctrl.get("control_statement", ""),
                        match["control_id"],
                        match["statement"],
                        match["similarity_score"],
                    ))

        # Deduplicate and sort
        mappings.sort(key=lambda m: m["similarity_score"], reverse=True)
        logger.info("Cross-mapping complete: %d suggested mapping(s) found (review required).", len(mappings))
        return mappings

    def map_controls(
        self,
        source_controls: List[Dict[str, Any]],
        source_fw_name:  str = "NIST CSF 2.0",
        top_k:           int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Map a flat list of source controls to the target catalogue.
        Returns one best-match per source control (top_k=1 by default).
        """
        mappings: List[Dict[str, Any]] = []
        use_embeddings = bool(self._embedder and self._target_vectors is not None)
        vectors = (
            self._embedder.embed_documents([c.get("control_statement", "") for c in source_controls])
            if use_embeddings else None
        )

        for idx, ctrl in enumerate(source_controls):
            if use_embeddings:
                best = self._find_top_k(np.array(vectors[idx]), top_k=top_k)
            else:
                best = self._lexical_top_k(ctrl.get("control_statement", ""), top_k=top_k)
            for match in best:
                if match["similarity_score"] >= self._threshold:
                    mappings.append(self._suggestion(
                        source_fw_name,
                        ctrl.get("control_id", ""),
                        ctrl.get("control_statement", ""),
                        match["control_id"],
                        match["statement"],
                        match["similarity_score"],
                    ))
        return mappings

    # ── Private helpers ────────────────────────────────────────────────────

    def _embed_catalogue(self, catalogue: List[Dict[str, str]]) -> np.ndarray:
        texts = [c["statement"] for c in catalogue]
        vecs  = self._embedder.embed_documents(texts)
        return np.array(vecs)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if token}

    def _lexical_top_k(self, source_text: str, top_k: int) -> List[Dict[str, Any]]:
        source_tokens = self._tokenize(source_text)
        scored: List[Dict[str, Any]] = []
        for tgt in self._target_catalogue:
            target_tokens = self._tokenize(tgt["statement"])
            if not source_tokens or not target_tokens:
                score = 0.0
            else:
                overlap = len(source_tokens & target_tokens)
                union = len(source_tokens | target_tokens)
                score = overlap / union if union else 0.0
            scored.append({
                "control_id": tgt["control_id"],
                "statement": tgt["statement"],
                "similarity_score": score,
            })

        scored.sort(key=lambda item: item["similarity_score"], reverse=True)
        return scored[:top_k]

    def _find_top_k(
        self, src_vec: np.ndarray, top_k: int
    ) -> List[Dict[str, Any]]:
        """
        Compute cosine similarity between *src_vec* and all target vectors.
        Returns the top-k matches as dicts.
        """
        # target_vectors is (N, D), src_vec is (D,) — both L2-normalised
        sims: np.ndarray = self._target_vectors @ src_vec  # cosine sim

        top_indices = np.argsort(sims)[::-1][:top_k]
        results: List[Dict[str, Any]] = []
        for idx in top_indices:
            tgt = self._target_catalogue[idx]
            results.append(
                {
                    "control_id":        tgt["control_id"],
                    "statement":         tgt["statement"],
                    "similarity_score":  float(sims[idx]),
                }
            )
        return results

    # ── Console print ──────────────────────────────────────────────────────

    @staticmethod
    def print_mappings(mappings: List[Dict[str, Any]], limit: int = 20) -> None:
        """Pretty-print top N cross-framework mappings."""
        print(f"\n  {'Source (CSF 2.0)':<18} {'Target (CSF 1.1)':<18} {'Score':>7}")
        print(f"  {'─'*18} {'─'*18} {'─'*7}")
        for m in mappings[:limit]:
            src   = m.get("source_control_id") or m.get("source_control", "")
            tgt   = m.get("target_control_id") or m.get("target_control", "")
            score = m.get("similarity_score", 0)
            print(f"  {src:<18} {tgt:<18} {score:>7.4f}")
