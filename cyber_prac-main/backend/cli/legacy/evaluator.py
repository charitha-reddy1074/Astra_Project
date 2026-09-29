"""
evaluator.py — Textual evidence evaluation engine.

Flow
----
Evidence File (TXT / CSV / JSON)
  → EvidenceParser (parse)
  → HierarchicalChunker.chunk_evidence (split)
  → VectorDBManager.store_evidence_docs (embed)
  → VectorDBManager.search_evidence (retrieve per control)
  → LLM evaluate (openai/gpt-oss-120b)
  → Compliance verdict dict
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from backend.core.utils import LLMClient, logger, timestamp, parse_llm_json

# ── Evaluation prompt ─────────────────────────────────────────────────────
EVAL_SYSTEM = """\
You are a senior cybersecurity compliance evaluator specialising in NIST CSF 2.0.

You will be given:
1. A NIST CSF control statement and its expected evidence types.
2. Company-provided evidence text retrieved from internal documents.

Your task is to determine the compliance status and explain your reasoning.

Output ONLY a valid JSON object (no markdown fences, no preamble) with this exact structure:
{{
    "control_id": "<control_id>",
    "compliance_status": "<COMPLIANT | PARTIALLY_COMPLIANT | NON_COMPLIANT>",
    "confidence_score": <0.0 to 1.0>,
    "matched_evidence": ["<list of evidence elements that satisfy the control>"],
    "missing_requirements": ["<list of gaps or missing artefacts>"],
    "reasoning": "<concise explanation of the verdict>",
    "recommendations": ["<actionable steps to close gaps>"],
    "evaluated_at": "<ISO timestamp>"
}}

Be specific, objective, and base your verdict ONLY on the provided evidence text.
"""

EVAL_HUMAN = """\
=== CONTROL ===
Control ID   : {control_id}
Domain       : {domain_name}
Statement    : {control_statement}
Expected Evidence Types: {evidence_types}

=== RETRIEVED COMPANY EVIDENCE ===
{evidence_text}

=== TASK ===
Evaluate whether the company evidence satisfies this NIST CSF control.
"""


class EvidenceEvaluator:
    """
    Evaluate company evidence against NIST CSF controls.

    Parameters
    ----------
    llm_api_key   : LLM API key (Groq)
    vectordb      : VectorDBManager instance (used to embed & retrieve evidence)
    chunker       : HierarchicalChunker instance (used to split evidence text)
    model         : LLM model name
    """

    def __init__(
        self,
        llm_api_key: str,
        vectordb,       # VectorDBManager (avoid circular import)
        chunker,        # HierarchicalChunker
        model: str = "openai/gpt-oss-120b",
    ) -> None:
        self._vectordb = vectordb
        self._chunker  = chunker
        self._chain = LLMClient(llm_api_key, model, temperature=0.1) if llm_api_key else None
        logger.info("EvidenceEvaluator initialised (model: %s)", model)

    # ── Public API ─────────────────────────────────────────────────────────

    def evaluate_file(
        self,
        evidence_path: Path,
        controls:      List[Dict[str, Any]],
        top_k:         int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Full evaluation pipeline for one evidence file against a list of controls.

        Parameters
        ----------
        evidence_path : path to TXT / CSV / JSON evidence file
        controls      : list of control dicts (from FrameworkNormalizer.flat_controls)
        top_k         : number of evidence chunks to retrieve per control

        Returns
        -------
        List of verdict dicts, one per control.
        """
        from backend.services.parser import EvidenceParser

        # 1. Parse evidence
        parser = EvidenceParser()
        evidence_text = parser.parse(evidence_path)

        # 2. Chunk evidence
        evidence_name = Path(evidence_path).stem
        chunks = self._chunker.chunk_evidence(evidence_text, source_name=evidence_name)

        # 3. Store evidence in temporary Chroma collection
        self._vectordb.clear_evidence_store()
        self._vectordb.store_evidence_docs(chunks)

        # 4. Evaluate each control
        verdicts: List[Dict[str, Any]] = []
        total = len(controls)

        for idx, control in enumerate(controls, 1):
            control_id = control.get("control_id", "UNKNOWN")
            print(
                f"  [{idx:02d}/{total}] Evaluating → {control_id}",
                flush=True,
            )
            verdict = self._evaluate_control(
                control        = control,
                evidence_text  = evidence_text,
                top_k          = top_k,
            )
            verdicts.append(verdict)

        logger.info(
            "Evaluated %d controls against '%s'",
            len(verdicts), evidence_name,
        )
        return verdicts

    def evaluate_text(
        self,
        evidence_text: str,
        control:       Dict[str, Any],
        top_k:         int = 3,
    ) -> Dict[str, Any]:
        """
        Evaluate raw evidence text against a single control.
        Useful for direct API-style access.
        """
        return self._evaluate_control(
            control       = control,
            evidence_text = evidence_text,
            top_k         = top_k,
        )

    # ── Private helpers ────────────────────────────────────────────────────

    def _evaluate_control(
        self,
        control:       Dict[str, Any],
        evidence_text: str,
        top_k:         int,
    ) -> Dict[str, Any]:
        """
        Retrieve relevant evidence chunks for this control, then call LLM.
        """
        control_id = control.get("control_id", "UNKNOWN")

        # Retrieve evidence most relevant to this control's statement
        query = (
            f"{control.get('control_statement','')} "
            f"{control.get('evidence_types', '')}"
        )
        try:
            retrieved_docs = self._vectordb.search_evidence(query, k=top_k)
            retrieved_text = "\n---\n".join(
                d.page_content for d in retrieved_docs
            ) if retrieved_docs else evidence_text[:2000]
        except Exception:
            retrieved_text = evidence_text[:2000]

        # Build LLM input
        payload = {
            "control_id":       control_id,
            "domain_name":      control.get("domain_name",      ""),
            "control_statement":control.get("control_statement",""),
            "evidence_types":   control.get("evidence_types",   ""),
            "evidence_text":    retrieved_text,
        }

        try:
            if self._chain:
                user_prompt = EVAL_HUMAN.format(**payload)
                raw = self._chain.invoke({"user_prompt": user_prompt, "system_prompt": EVAL_SYSTEM})
                verdict = self._parse_json(raw, control_id)
            else:
                raise RuntimeError("No LLM chain available")
        except Exception as exc:
            logger.warning("LLM evaluation failed for %s: %s", control_id, exc)
            verdict = self._error_verdict(control_id, str(exc))

        verdict["control_id"]    = control_id
        verdict["evaluated_at"]  = timestamp()
        return verdict

    @staticmethod
    def _parse_json(raw: str, control_id: str) -> Dict[str, Any]:
        """Extract and parse JSON from LLM output."""
        try:
            return parse_llm_json(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("JSON parse error for %s: %s", control_id, exc)
            return {
                "compliance_status": "UNKNOWN",
                "confidence_score":  0.0,
                "matched_evidence":  [],
                "missing_requirements": ["Parse error — could not decode LLM response"],
                "reasoning":         raw[:300],
                "parse_error":       str(exc),
            }

    @staticmethod
    def _error_verdict(control_id: str, error: str) -> Dict[str, Any]:
        return {
            "control_id":          control_id,
            "compliance_status":   "UNKNOWN",
            "confidence_score":    0.0,
            "matched_evidence":    [],
            "missing_requirements":["Evaluation error"],
            "reasoning":           f"Error during evaluation: {error}",
            "recommendations":     ["Retry with a valid evidence file."],
        }

    # ── Summary ────────────────────────────────────────────────────────────

    @staticmethod
    def summarise(verdicts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute aggregate compliance statistics from a list of verdicts."""
        total   = len(verdicts)
        counts  = {"COMPLIANT": 0, "PARTIALLY_COMPLIANT": 0, "NON_COMPLIANT": 0, "UNKNOWN": 0}

        scores: List[float] = []
        for v in verdicts:
            status = v.get("compliance_status", "UNKNOWN")
            counts[status] = counts.get(status, 0) + 1
            score = v.get("confidence_score", 0)
            if isinstance(score, (int, float)):
                scores.append(float(score))

        avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0

        return {
            "total_controls_evaluated": total,
            "compliant":                counts["COMPLIANT"],
            "partially_compliant":      counts["PARTIALLY_COMPLIANT"],
            "non_compliant":            counts["NON_COMPLIANT"],
            "unknown":                  counts.get("UNKNOWN", 0),
            "average_confidence_score": avg_score,
            "compliance_rate_pct": round(
                (counts["COMPLIANT"] / total * 100) if total else 0, 1
            ),
        }
