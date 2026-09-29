"""
chunker.py — Enterprise hierarchical + semantic chunker for frameworks.

Design goals:
1) Preserve framework hierarchy (domain -> control -> question).
2) Support multi-granularity retrieval (domain, control, question, segments).
3) Enrich every chunk with provenance and relationship metadata.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Iterable, List, Sequence

from langchain_core.documents import Document

from backend.core.utils import logger


class HierarchicalChunker:
    """Enterprise framework chunker with hierarchical and semantic layers."""

    def __init__(
        self,
        control_target_tokens: int = 500,
        overlap_ratio: float = 0.25,
        semantic_drop_threshold: float = 0.75,
    ) -> None:
        self._control_target_tokens = max(200, control_target_tokens)
        self._overlap_ratio = min(max(overlap_ratio, 0.0), 0.4)
        self._semantic_drop_threshold = min(max(semantic_drop_threshold, 0.0), 1.0)

    def chunk(
        self,
        framework: Dict[str, Any],
        include_levels: Sequence[str] = ("control",),
        semantic_refine: bool = True,
    ) -> List[Document]:
        """
        Build framework chunks at selected levels.

        include_levels can contain: domain, control, question, control_segment.
        """
        docs: List[Document] = []
        enabled = {x.strip().lower() for x in include_levels}

        fw_id   = framework.get("framework_id",   "unknown")
        fw_name = framework.get("framework_name", "Unknown Framework")
        version = framework.get("version",        "")
        source_format = framework.get("source_format", "")

        ingested_at = framework.get("ingested_at", "")

        for domain_idx, domain in enumerate(framework.get("domains", []), 1):
            domain_id   = domain.get("domain_id",   "")
            domain_name = domain.get("domain_name", "")
            controls = domain.get("controls", [])

            domain_chunk_id = self._new_chunk_id()
            domain_summary = self._build_domain_summary(domain_name, domain_id, controls)

            if "domain" in enabled:
                docs.append(
                    Document(
                        page_content=domain_summary,
                        metadata=self._base_metadata(
                            chunk_id=domain_chunk_id,
                            parent_chunk_id="",
                            chunk_level="domain",
                            fw_id=fw_id,
                            fw_name=fw_name,
                            version=version,
                            source_format=source_format,
                            ingested_at=ingested_at,
                            domain_id=domain_id,
                            domain_name=domain_name,
                        ),
                    )
                )

            for control_idx, control in enumerate(controls, 1):
                control_chunk_id = self._new_chunk_id()
                control_doc = self._build_control_document(
                    control=control,
                    fw_id=fw_id,
                    fw_name=fw_name,
                    version=version,
                    source_format=source_format,
                    ingested_at=ingested_at,
                    domain_id=domain_id,
                    domain_name=domain_name,
                    domain_summary=domain_summary,
                    parent_chunk_id=domain_chunk_id,
                    chunk_id=control_chunk_id,
                )

                if "control" in enabled:
                    docs.append(control_doc)

                if semantic_refine and "control_segment" in enabled:
                    docs.extend(
                        self._build_control_segments(
                            control_doc=control_doc,
                            parent_chunk_id=control_chunk_id,
                        )
                    )

                if "question" in enabled:
                    docs.extend(
                        self._build_question_documents(
                            control=control,
                            fw_id=fw_id,
                            fw_name=fw_name,
                            version=version,
                            source_format=source_format,
                            ingested_at=ingested_at,
                            domain_id=domain_id,
                            domain_name=domain_name,
                            parent_chunk_id=control_chunk_id,
                            order_hint=(domain_idx, control_idx),
                        )
                    )

        logger.info(
            "Chunked framework '%s' -> %d documents (levels=%s)",
            fw_name, len(docs), sorted(enabled),
        )
        return docs

    # ── Private ───────────────────────────────────────────────────────────

    def _new_chunk_id(self) -> str:
        return str(uuid.uuid4())

    def _token_count(self, text: str) -> int:
        return len(text.split())

    def _lexical_similarity(self, left: str, right: str) -> float:
        """Token-set Jaccard similarity as a light semantic proxy."""
        left_set = set(re.findall(r"[a-zA-Z0-9_.-]+", left.lower()))
        right_set = set(re.findall(r"[a-zA-Z0-9_.-]+", right.lower()))
        if not left_set or not right_set:
            return 0.0
        inter = len(left_set & right_set)
        union = len(left_set | right_set)
        return inter / union if union else 0.0

    def _with_overlap(self, left: str, right: str) -> str:
        overlap_tokens = int(self._control_target_tokens * self._overlap_ratio)
        if overlap_tokens <= 0:
            return right
        left_tokens = left.split()
        prefix = " ".join(left_tokens[-overlap_tokens:]).strip()
        if not prefix:
            return right
        return f"[context overlap]\n{prefix}\n\n{right}".strip()

    def _semantic_group(self, units: Sequence[str]) -> List[str]:
        """Group text units into semantically coherent windows."""
        groups: List[str] = []
        current: List[str] = []
        current_tokens = 0

        for unit in units:
            unit = unit.strip()
            if not unit:
                continue

            unit_tokens = self._token_count(unit)
            prospective_tokens = current_tokens + unit_tokens
            drop = 1.0
            if current:
                drop = self._lexical_similarity(current[-1], unit)

            should_cut = (
                current
                and prospective_tokens > self._control_target_tokens
                and drop < self._semantic_drop_threshold
            )
            if should_cut:
                groups.append("\n\n".join(current).strip())
                current = [unit]
                current_tokens = unit_tokens
            else:
                current.append(unit)
                current_tokens = prospective_tokens

        if current:
            groups.append("\n\n".join(current).strip())

        # Add sliding overlap so boundaries keep context.
        with_overlap: List[str] = []
        for idx, grp in enumerate(groups):
            if idx == 0:
                with_overlap.append(grp)
            else:
                with_overlap.append(self._with_overlap(groups[idx - 1], grp))
        return with_overlap

    def _build_domain_summary(
        self,
        domain_name: str,
        domain_id: str,
        controls: Sequence[Dict[str, Any]],
    ) -> str:
        lines = [
            f"Domain Summary: {domain_name} ({domain_id})",
            f"Control count: {len(controls)}",
            "Controls:",
        ]
        for ctrl in controls:
            cid = ctrl.get("control_id", "")
            stmt = (ctrl.get("control_statement", "") or "").strip()
            lines.append(f"- {cid}: {stmt}")
        return "\n".join(lines)

    @staticmethod
    def _cross_reference_text(refs: Any) -> str:
        parts: List[str] = []
        for ref in refs or []:
            if isinstance(ref, dict):
                fw = ref.get("other_framework_id", "")
                ctrl = ref.get("other_control_id", "")
                parts.append(f"{fw}:{ctrl}" if fw or ctrl else "")
            else:
                parts.append(str(ref))
        return ", ".join(p for p in parts if p)

    def _base_metadata(
        self,
        chunk_id: str,
        parent_chunk_id: str,
        chunk_level: str,
        fw_id: str,
        fw_name: str,
        version: str,
        source_format: str,
        ingested_at: str,
        domain_id: str,
        domain_name: str,
        control_id: str = "",
        control_statement: str = "",
        maturity_levels: str = "",
        evidence_types: str = "",
        cross_references: str = "",
        source_page: str = "",
    ) -> Dict[str, str]:
        return {
            "chunk_id": chunk_id,
            "parent_chunk_id": parent_chunk_id,
            "chunk_level": chunk_level,
            "framework_id": fw_id,
            "framework_name": fw_name,
            "version": version,
            "source_format": source_format,
            "ingested_at": ingested_at,
            "domain_id": domain_id,
            "domain_name": domain_name,
            "control_id": control_id,
            "control_statement": control_statement,
            "maturity_levels": maturity_levels,
            "evidence_types": evidence_types,
            "cross_references": cross_references,
            "source_page": source_page,
        }

    def _build_control_document(
        self,
        control:     Dict[str, Any],
        fw_id:       str,
        fw_name:     str,
        version:     str,
        source_format: str,
        ingested_at: str,
        domain_id:   str,
        domain_name: str,
        domain_summary: str,
        parent_chunk_id: str,
        chunk_id: str,
    ) -> Document:
        """Build a control-level chunk with rich context."""

        control_id   = control.get("control_id",        "")
        statement    = control.get("control_statement", "")
        maturity     = control.get("maturity_levels",         [])
        evidence     = control.get("expected_evidence_types", [])
        cross_refs   = self._cross_reference_text(control.get("cross_references", []))

        questions = control.get("questions", [])
        question_lines: List[str] = []
        for q in questions:
            if isinstance(q, dict):
                question_lines.append(str(q.get("question_text", "")).strip())
            else:
                question_lines.append(str(q).strip())

        question_text = "\n".join([f"- {q}" for q in question_lines if q])

        content_lines = [
            f"Framework    : {fw_name} {version}",
            f"Domain       : {domain_name} ({domain_id})",
            f"Control ID   : {control_id}",
            f"Statement    : {statement}",
            f"Maturity     : {', '.join(maturity)}",
            f"Evidence     : {', '.join(evidence)}",
            f"Cross-refs   : {cross_refs}",
            "",
            "Domain Summary:",
            domain_summary,
        ]
        if question_text:
            content_lines.extend(["", "Questions:", question_text])

        page_content = "\n".join(content_lines)

        metadata = self._base_metadata(
            chunk_id=chunk_id,
            parent_chunk_id=parent_chunk_id,
            chunk_level="control",
            fw_id=fw_id,
            fw_name=fw_name,
            version=version,
            source_format=source_format,
            ingested_at=ingested_at,
            domain_id=domain_id,
            domain_name=domain_name,
            control_id=control_id,
            control_statement=statement,
            maturity_levels=", ".join(maturity),
            evidence_types=", ".join(evidence),
            cross_references=", ".join(cross_refs),
        )

        return Document(page_content=page_content, metadata=metadata)

    def _build_control_segments(
        self,
        control_doc: Document,
        parent_chunk_id: str,
    ) -> List[Document]:
        """Semantic refinement for long controls."""
        raw = control_doc.page_content
        units = [u.strip() for u in re.split(r"\n\s*\n", raw) if u.strip()]
        if len(units) <= 1:
            return []

        grouped = self._semantic_group(units)
        if len(grouped) <= 1:
            return []

        docs: List[Document] = []
        for idx, grp in enumerate(grouped, 1):
            md = dict(control_doc.metadata)
            md["chunk_id"] = self._new_chunk_id()
            md["parent_chunk_id"] = parent_chunk_id
            md["chunk_level"] = "control_segment"
            md["segment_index"] = str(idx)
            docs.append(Document(page_content=grp, metadata=md))
        return docs

    def _extract_question_units(self, control: Dict[str, Any]) -> List[str]:
        """Extract explicit questions or derive requirement-level prompts."""
        questions = control.get("questions", [])
        extracted: List[str] = []

        for q in questions:
            if isinstance(q, dict):
                text = str(q.get("question_text", "")).strip()
            else:
                text = str(q).strip()
            if text:
                extracted.append(text)

        if extracted:
            return extracted

        # Derive fine-grained units from statement clauses if explicit
        # framework questions are not available.
        statement = str(control.get("control_statement", "")).strip()
        if not statement:
            return []
        clauses = re.split(r";|\.\s+|\band\b|\bor\b", statement)
        units = [c.strip(" .") for c in clauses if c.strip()]
        if not units:
            return []
        return [f"How is this requirement implemented: {u}?" for u in units[:4]]

    def _build_question_documents(
        self,
        control: Dict[str, Any],
        fw_id: str,
        fw_name: str,
        version: str,
        source_format: str,
        ingested_at: str,
        domain_id: str,
        domain_name: str,
        parent_chunk_id: str,
        order_hint: Sequence[int],
    ) -> List[Document]:
        questions = self._extract_question_units(control)
        if not questions:
            return []

        control_id = str(control.get("control_id", ""))
        statement = str(control.get("control_statement", ""))
        maturity = ", ".join(control.get("maturity_levels", []))
        evidence = ", ".join(control.get("expected_evidence_types", []))
        cross_refs = self._cross_reference_text(control.get("cross_references", []))

        docs: List[Document] = []
        for idx, q in enumerate(questions, 1):
            chunk_id = self._new_chunk_id()
            page_content = "\n".join([
                f"Framework    : {fw_name} {version}",
                f"Domain       : {domain_name} ({domain_id})",
                f"Control ID   : {control_id}",
                f"Statement    : {statement}",
                f"Question     : {q}",
            ])
            metadata = self._base_metadata(
                chunk_id=chunk_id,
                parent_chunk_id=parent_chunk_id,
                chunk_level="question",
                fw_id=fw_id,
                fw_name=fw_name,
                version=version,
                source_format=source_format,
                ingested_at=ingested_at,
                domain_id=domain_id,
                domain_name=domain_name,
                control_id=control_id,
                control_statement=statement,
                maturity_levels=maturity,
                evidence_types=evidence,
                cross_references=cross_refs,
            )
            metadata["question_index"] = f"{order_hint[0]}.{order_hint[1]}.{idx}"
            docs.append(Document(page_content=page_content, metadata=metadata))

        return docs

    # ── Evidence chunker ──────────────────────────────────────────────────

    def chunk_evidence(
        self, text: str, source_name: str = "evidence", chunk_size: int = 800
    ) -> List[Document]:
        """
        Split raw evidence text into overlapping chunks for retrieval.

        Uses a simple sliding-window approach (not semantic) because
        evidence files are free-form text without structural anchors.
        """
        overlap = 100
        chunks: List[Document] = []
        start = 0

        while start < len(text):
            end   = min(start + chunk_size, len(text))
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(
                    Document(
                        page_content = chunk,
                        metadata     = {"source": source_name, "type": "evidence"},
                    )
                )
            start += chunk_size - overlap

        logger.info(
            "Evidence '%s' → %d chunks", source_name, len(chunks)
        )
        return chunks
