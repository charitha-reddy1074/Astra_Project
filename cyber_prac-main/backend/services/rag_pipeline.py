"""
rag_pipeline.py — End-to-end LangChain RAG pipeline.

Flow
----
User Query
  → Embed Query
  → Chroma Vector Search (framework_controls)
  → Retrieved Control Documents
  → Build Prompt (context + query)
  → LLM
  → Questionnaire / Answer Response
Also exposes a direct Q&A chain for free-form queries about controls.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence
import uuid
from pathlib import Path

from langchain_core.documents import Document
from backend.core.utils import LLMClient, logger, timestamp, parse_llm_json, generate_id

# ── RAG answer prompt ─────────────────────────────────────────────────────
RAG_SYSTEM = """\
You are a senior NIST CSF 2.0 cybersecurity compliance advisor.

Use ONLY the retrieved framework control context below to answer the user's question.
If the context does not contain enough information, say so clearly — do not hallucinate.

When appropriate, suggest which NIST CSF controls apply and what evidence is required.
"""

RAG_HUMAN = """\
=== Retrieved NIST CSF Controls (context) ===
{{ context }}

=== User Question ===
{{ question }}

=== Instructions ===
Answer the question using only the controls above.
Reference control IDs explicitly (e.g. PR.AA-01, DE.CM-01).
"""

# ── Questionnaire-from-query prompt (UPDATED: structured Part 2 questions) ────
QAG_SYSTEM = """\
You are a cybersecurity compliance auditor specialising in NIST CSF 2.0.

Given the retrieved framework controls and the user's assessment topic, generate a
targeted assessment questionnaire in the Market Assessment 3-part format.

═══════════════════════════════════════════════════════
PART 1 — PRE-ASSESSMENT QUESTION (one per sub-topic)
═══════════════════════════════════════════════════════
Each sub-topic gets exactly ONE Part 1 question:
  - Short capability-check question naming the specific technology or process.
  - Maximum 20 words. Must NOT echo the control statement verbatim.
  - Response options are always: "Yes / No / Partial"
  - detail_prompt is always: "If yes or partial, specify the tool or technology in use."
  - Good examples: "Is MFA enforced for all administrative access?",
                   "Do you have a centralised SIEM collecting network logs?",
                   "Is a formal PAM solution in place for privileged accounts?"

═══════════════════════════════════════════════════════
PART 2 — STRUCTURED INTERVIEW QUESTIONS (4–6 per sub-topic)
═══════════════════════════════════════════════════════
Each question is a STRUCTURED OBJECT — not a plain string.
  - Ask HOW, WHO, WHEN, WHAT EVIDENCE, WHAT GAPS — not "Is X implemented?" (that is Part 1).
  - Use diverse question_type values: YES_NO, SCALE_1_5, FREE_TEXT, MULTI_CHOICE.
  - MULTI_CHOICE questions must include 3–4 realistic answer choices.
  - Assign weight (1–5) and maturity_level (1–5 integer).
  - Include expected_evidence_types as specific artefact names.
  - Do NOT repeat the control statement as the question text.

Output ONLY valid JSON (no markdown, no preamble):
{
  "topic": "<assessment topic from user query>",
  "applicable_controls": ["<control_id>", ...],
  "sub_topics": [
    {
      "sub_topic": "<sub-topic name, e.g. 'Identity Lifecycle & Directory'>",
      "control_ids": ["<control_id>", ...],
      "part1_question": {
        "question_id": "<uuid>",
        "question_text": "<short capability-check question naming the technology, max 20 words>",
        "question_type": "yes_no_with_detail",
        "response_options": "Yes / No / Partial",
        "detail_prompt": "If yes or partial, specify the tool or technology in use."
      },
      "questions": [
        {
          "question_id": "<uuid>",
          "control_id": "<related_control_id>",
          "text": "<investigative audit question — HOW/WHO/WHEN/WHAT, max 30 words>",
          "help_text": "<optional auditor guidance, max 20 words>",
          "question_type": "<YES_NO | SCALE_1_5 | FREE_TEXT | MULTI_CHOICE>",
          "choices": ["<choice 1>", "<choice 2>", "<choice 3>"],
          "weight": <1-5>,
          "maturity_level": <1-5 integer>,
          "expected_evidence_types": ["<artefact_name_1>", "<artefact_name_2>"]
        }
      ],
      "maturity_guide": {
        "mature": "<concrete description of a fully implemented state for this sub-topic>",
        "partial": "<typical partial state with common gap patterns>",
        "critical_gap": "<specific risk and consequence if this sub-topic is absent>"
      },
      "evidence_to_request": [
        "<artefact 1>",
        "<artefact 2>",
        "<artefact 3>"
      ],
      "red_flags": [
        {
          "critical_gap": "<specific critical gap finding>",
          "recommended_action": "<immediate remediation action>"
        }
      ]
    }
  ],
  "generated_at": "<ISO timestamp>"
}

NOTE: The "choices" field is only required when question_type is MULTI_CHOICE. Omit it for other types.
"""

QAG_HUMAN = """\
=== Retrieved NIST CSF Controls ===
{{ context }}

=== Assessment Topic ===
{{ question }}

Generate the assessment questionnaire now.
"""



class RAGPipeline:
    """
    LangChain RAG pipeline over the NIST CSF 2.0 control knowledge base.

    Provides two modes:
      1. ask()       — free-form Q&A with cited control references
      2. generate()  — structured 3-part JSON questionnaire from a topic query
    """

    def __init__(
        self,
        vectordb,           # VectorDBManager instance
        llm_api_key: str,
        model:        str = "openai/gpt-oss-120b",
        k:            int = 5,
    ) -> None:
        self._vectordb = vectordb
        self._k        = k
        self._cluster_map: Optional[Dict[str, Any]] = None
        # Path to the cluster map JSON; overridable for testing
        self.CLUSTER_MAP_PATH: Path = (
            Path(__file__).resolve().parents[2] / "data" / "market_domain_clusters.json"
        )
        self.MATURITY_SCORING_PATH: Path = (
            Path(__file__).resolve().parents[2] / "data" / "outputs" / "maturity_scoring_criteria.json"
        )
        self._retriever = vectordb
        self._chain = LLMClient(llm_api_key, model) if llm_api_key else None
        logger.info("RAGPipeline initialised (model: %s, k: %d)", model, k)

    # ── Public API ─────────────────────────────────────────────────────────

    def regenerate_questionnaire(self, control_id: str, framework_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch a specific control from TryChroma and regenerate its questionnaire.
        
        This is useful when you want to update questions for an existing control
        without re-parsing the original framework source.
        """
        from backend.services.questionnaire import QuestionnaireBuilder
        from backend.data_access.vectordb import framework_collection_name
        
        logger.info("Regenerating questionnaire for ingested control: %s", control_id)
        
        col_name = None
        if framework_key:
            col_name = framework_collection_name(framework_key)

        # Retrieve the specific document from the vector store
        docs = self._vectordb.get_framework_docs(collection_name=col_name, where={"control_id": control_id}, limit=1)
        
        if not docs:
            logger.warning("Control ID %s not found in TryChroma.", control_id)
            return {"error": f"Control {control_id} not found in database."}
            
        if not self._chain:
            raise RuntimeError("LLM client not initialized.")

        # Use the specialized builder to generate audit-style questions
        builder = QuestionnaireBuilder(llm_api_key=self._chain.api_key, model=self._chain.model)
        return builder.generate_for_control(docs[0].metadata)

    def ask(self, query: str) -> Dict[str, Any]:
        """
        Answer a free-form compliance question using retrieved controls.

        Returns
        -------
        {
          "query":             str,
          "answer":            str,
          "retrieved_controls": List[str],
          "timestamp":         str
        }
        """
        logger.info("RAG Q&A → '%s'", query[:80])

        # Retrieve docs separately so we can surface control IDs
        retrieved_docs = self._vectordb.search_framework(query, k=self._k)
        retrieved_ids = [d.metadata.get("control_id", "?") for d in retrieved_docs]

        if not self._chain: #
            raise RuntimeError("LLM chain not initialised; set GROQ_API_KEY")

        context = self._format_docs(retrieved_docs)
        user_prompt = RAG_HUMAN.replace("{{ context }}", context).replace("{{ question }}", query) #
        raw = self._chain.invoke({"user_prompt": user_prompt, "system_prompt": RAG_SYSTEM})

        return {
            "query": query,
            "answer": raw,
            "retrieved_controls": retrieved_ids,
            "timestamp": timestamp(),
        }

    def generate(self, topic: str) -> Dict[str, Any]:
        """
        Generate a structured 3-part assessment questionnaire for a topic.

        Returns
        -------
        The full Market Assessment structure:
          - part1_checklist   : one yes/no+detail row per sub-topic
          - question_groups   : Part 2 groups with sub_topic sections
          - part3_tracker     : evidence rows grouped by (domain, sub_topic)
          - red_flags_by_domain: critical gap + remediation per domain
        """
        logger.info("RAG Questionnaire (3-part) → '%s'", topic[:80])

        if not self._chain:
            raise RuntimeError("LLM chain not initialised; set GROQ_API_KEY")

        retrieved_docs = self._vectordb.search_framework(topic, k=self._k)
        context = self._format_docs(retrieved_docs)
        user_prompt = QAG_HUMAN.replace("{{ context }}", context).replace("{{ question }}", topic)
        raw = self._chain.invoke({"user_prompt": user_prompt, "system_prompt": QAG_SYSTEM})
        parsed = self._parse_json(raw, topic)

        sub_topics = parsed.get("sub_topics", [])

        # ── Part 1: one checklist row per sub-topic ────────────────────────
        part1_checklist = self._build_part1_checklist(sub_topics)

        # ── Part 2: question_groups with interview questions + maturity ─────
        question_groups = self._build_question_groups(topic, sub_topics)

        # ── Part 3: evidence tracker grouped by (domain, sub_topic) ─────────
        part3_tracker = self._build_part3_tracker(topic, sub_topics)

        # ── Red Flags & Remediation per domain ────────────────────────────
        red_flags_by_domain = self._build_red_flags_by_domain(topic, sub_topics)

        # ── Flat atomic questions for backward compatibility ───────────────
        atomic_questions: List[Dict[str, Any]] = []
        for st in sub_topics:
            for q in st.get("questions", []):
                atomic_questions.append({
                    "question_id": q.get("question_id") or generate_id(),
                    # support both 'text' (new format) and 'question_text' (legacy)
                    "question_text": q.get("text") or q.get("question_text", ""),
                    "question_type": q.get("question_type", "FREE_TEXT"),
                    "control_id": q.get("control_id", ""),
                    "sub_topic": st.get("sub_topic", ""),
                    "expected_evidence": ", ".join(q.get("expected_evidence_types", [])),
                    "maturity_level": q.get("maturity_level", 3),
                    "choices": q.get("choices", []),
                    "weight": int(q.get("weight", 3)),
                })

        controls_dict: Dict[str, Dict[str, Any]] = {}
        for q in atomic_questions:
            cid = q.get("control_id") or "unknown"
            if cid not in controls_dict:
                controls_dict[cid] = {
                    "control_id": cid,
                    "control_statement": "RAG-generated context.",
                    "maturity_levels": [q.get("maturity_level", "Defined")],
                    "expected_evidence_types": [q.get("expected_evidence", "Audit Evidence")],
                    "cross_references": [],
                    "Questions": [],
                }
            controls_dict[cid]["Questions"].append({
                "question_id": q.get("question_id") or generate_id(),
                "question_text": q.get("question_text", ""),
                "question_type": q.get("question_type", "free_text"),
                "weight": q.get("weight", 3),
            })

        return {
            "framework_id": str(uuid.uuid5(uuid.NAMESPACE_URL, topic)),
            "name": f"Topic: {topic}",
            "version": "1.0",
            "source_format": "rag_generated",
            "ingested_at": timestamp(),
            # ── 3-part structure ───────────────────────────────────────────
            "part1_checklist": part1_checklist,
            "question_groups": question_groups,
            "part3_tracker": part3_tracker,
            "red_flags_by_domain": red_flags_by_domain,
            # ── Flat backward-compat structure ─────────────────────────────
            "question_count": len(atomic_questions),
            "questions": atomic_questions,
            "Domains": [
                {
                    "domain_id": "RAG",
                    "domain_name": topic,
                    "Controls": list(controls_dict.values())
                }
            ]
        }

    def generate_for_cluster(
        self,
        cluster_id: str,
        frameworks: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Generate a full 3-part questionnaire for a Market Assessment cluster.

        Resolves cluster_id → domain_keys via market_domain_clusters.json,
        then delegates to framework_exporter.build_questionnaire_from_selection().

        Parameters
        ----------
        cluster_id : str
            One of: IAM, NETWORK, CLOUD, M365, AI, APPSEC, ENDPOINT, DEFENSE, TPRM, DATA
        frameworks : optional pre-loaded framework catalog list.
            If not provided, load_framework_catalog() is called automatically.

        Returns
        -------
        Full 3-part questionnaire dict from build_questionnaire_from_selection(),
        augmented with cluster metadata.
        """
        cluster = self._get_cluster(cluster_id)
        domain_keys: List[str] = cluster.get("domain_keys", [])

        if not domain_keys:
            raise ValueError(
                f"Cluster '{cluster_id}' has no domain_keys in market_domain_clusters.json"
            )

        logger.info(
            "Generating 3-part questionnaire for cluster '%s' → %d domain_keys",
            cluster_id,
            len(domain_keys),
        )

        # Load the framework catalog if not provided
        if frameworks is None:
            from backend.services.framework_exporter import load_framework_catalog
            frameworks = load_framework_catalog()

        # Filter only domain_keys whose framework is in the catalog
        available_fw_keys = {fw.get("framework_key") for fw in frameworks}
        resolved_keys = [
            dk for dk in domain_keys
            if dk.split(":")[0] in available_fw_keys
        ]

        if not resolved_keys:
            raise ValueError(
                f"None of the domain_keys for cluster '{cluster_id}' match loaded frameworks. "
                f"Available: {sorted(available_fw_keys)}"
            )

        skipped = [dk for dk in domain_keys if dk not in resolved_keys]
        if skipped:
            logger.warning(
                "Skipped %d domain_keys for cluster '%s' (framework not loaded): %s",
                len(skipped), cluster_id, skipped,
            )

        from backend.services.framework_exporter import build_questionnaire_from_selection
        questionnaire = build_questionnaire_from_selection(frameworks, resolved_keys)

        # Augment with cluster metadata so the frontend can render correctly
        questionnaire["cluster_id"] = cluster_id
        questionnaire["cluster_name"] = cluster.get("cluster_name", cluster_id)
        questionnaire["cluster_description"] = cluster.get("description", "")
        questionnaire["cluster_sub_topics"] = cluster.get("sub_topics", [])
        questionnaire["cluster_part1_question_ids"] = cluster.get("part1_question_ids", [])
        questionnaire["maturity_scoring_criteria"] = self._get_maturity_criteria_for_cluster(cluster_id)

        return questionnaire

    def retrieve(self, query: str) -> List[Document]:
        """Return raw retrieved Documents for a query (useful for inspection)."""
        return self._vectordb.search_framework(query, k=self._k)

    def retrieve_with_scores(self, query: str) -> List[tuple]:
        """Return (Document, score) tuples for a query."""
        return self._vectordb.search_framework_with_scores(query, k=self._k)

    def list_clusters(self) -> List[Dict[str, Any]]:
        """
        Return the list of available market clusters from market_domain_clusters.json.
        Useful for populating the frontend cluster selector.
        """
        cluster_map = self._load_cluster_map()
        return [
            {
                "cluster_id": c.get("cluster_id"),
                "cluster_name": c.get("cluster_name"),
                "description": c.get("description"),
                "icon": c.get("icon"),
                "sub_topics": c.get("sub_topics", []),
                "domain_key_count": len(c.get("domain_keys", [])),
                "part1_question_ids": c.get("part1_question_ids", []),
            }
            for c in cluster_map.get("clusters", [])
        ]

    # ── Private: 3-part assembly helpers ──────────────────────────────────

    @staticmethod
    def _format_docs(docs: List[Document], max_chars: int = 6000) -> str:
        parts = []
        total = 0
        for d in docs:
            text = d.page_content
            if total + len(text) > max_chars:
                remaining = max_chars - total
                if remaining > 200:
                    parts.append(text[:remaining])
                break
            parts.append(text)
            total += len(text)
        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _parse_json(raw: str, fallback_topic: str) -> Dict[str, Any]:
        """Extract and parse JSON from model output."""
        try:
            return parse_llm_json(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("JSON parse error in RAG generate: %s", exc)
            return {
                "topic":       fallback_topic,
                "questions":   [],
                "raw_response":str(raw)[:500],
                "parse_error": str(exc),
            }

    @staticmethod
    def _build_part1_checklist(sub_topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build Part 1 checklist rows from LLM-generated sub-topic sections."""
        checklist: List[Dict[str, Any]] = []
        for i, st in enumerate(sub_topics, 1):
            p1q = st.get("part1_question") or {}
            checklist.append({
                "number": i,
                "question_id": p1q.get("question_id") or generate_id(),
                "question_text": p1q.get("question_text", f"Is {st.get('sub_topic', '')} implemented?"),
                "sub_topic": st.get("sub_topic", ""),
                "control_ids": st.get("control_ids", []),
                "answer_format": "yes_no_with_detail",
                "response_options": p1q.get("response_options", "Yes / No / Partial"),
                "detail_prompt": p1q.get("detail_prompt", "If yes or partial, specify the tool or technology in use."),
            })
        return checklist

    @staticmethod
    def _build_question_groups(
        topic: str, sub_topics: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build Part 2 question_groups with sub-topic sections.
        
        Reads the new structured 'questions' array (framework JSON format:
        text, question_type, weight, maturity_level, expected_evidence_types).
        Also preserves backward-compat maturity_guide, evidence_to_request, red_flags.
        """
        return [
            {
                "domain_name": topic,
                "sub_topic": st.get("sub_topic", ""),
                "control_ids": st.get("control_ids", []),
                "maturity_guide": st.get("maturity_guide", {
                    "mature": "",
                    "partial": "",
                    "critical_gap": "",
                }),
                "evidence_to_request": st.get("evidence_to_request", []),
                "red_flags": st.get("red_flags", []),
                "questions": [
                    {
                        "question_id": q.get("question_id") or generate_id(),
                        # support both 'text' (new format) and 'question_text' (legacy)
                        "question_text": q.get("text") or q.get("question_text", ""),
                        "help_text": q.get("help_text", ""),
                        "question_type": q.get("question_type", "FREE_TEXT"),
                        "choices": q.get("choices", []),
                        "control_id": q.get("control_id", ""),
                        "expected_evidence_types": q.get("expected_evidence_types", []),
                        "maturity_level": q.get("maturity_level", 3),
                        "weight": int(q.get("weight", 3)),
                    }
                    for q in st.get("questions", [])
                ],
            }
            for st in sub_topics
        ]

    @staticmethod
    def _build_part3_tracker(
        domain_name: str, sub_topics: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build Part 3 evidence tracker rows grouped by (domain, sub_topic)."""
        rows: List[Dict[str, Any]] = []
        seen: set = set()
        for st in sub_topics:
            sub_topic = st.get("sub_topic", "")
            for ev in st.get("evidence_to_request", []):
                if not ev:
                    continue
                key = (domain_name, sub_topic, ev)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "domain": domain_name,
                    "sub_topic": sub_topic,
                    "control_ids": ", ".join(st.get("control_ids", [])),
                    "evidence_request": ev,
                    "answer_format": "status_text",
                    "status": "",
                })
        return rows

    @staticmethod
    def _build_red_flags_by_domain(
        domain_name: str, sub_topics: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Collect red flags from all sub-topics and group by domain."""
        all_red_flags: List[Dict[str, Any]] = []
        for st in sub_topics:
            for rf in st.get("red_flags", []):
                if rf.get("critical_gap") or rf.get("recommended_action"):
                    all_red_flags.append({
                        **rf,
                        "sub_topic": st.get("sub_topic", ""),
                    })
        if not all_red_flags:
            return []
        return [{"domain_name": domain_name, "red_flags": all_red_flags}]

    # ── Private: cluster resolution ─────────────────────────────────────

    def _load_cluster_map(self) -> Dict[str, Any]:
        """Load and cache the market_domain_clusters.json file."""
        if self._cluster_map is not None:
            return self._cluster_map

        # Try the canonical path first, then fall back to the outputs directory
        search_paths = [
            self.CLUSTER_MAP_PATH,
            Path(__file__).resolve().parents[2] / "data" / "outputs" / "market_domain_clusters.json",
            Path(__file__).resolve().parents[2] / "market_domain_clusters.json",
        ]

        for path in search_paths:
            if path.exists():
                with open(path, "r", encoding="utf-8") as fh:
                    self._cluster_map = json.load(fh)
                logger.info("Loaded cluster map from %s", path)
                return self._cluster_map

        raise FileNotFoundError(
            f"market_domain_clusters.json not found. Searched: {[str(p) for p in search_paths]}"
        )

    def _load_maturity_scoring(self) -> Dict[str, Any]:
        """Load the static maturity_scoring_criteria.json file."""
        search_paths = [
            self.MATURITY_SCORING_PATH,
            Path(__file__).resolve().parents[2] / "data" / "maturity_scoring_criteria.json",
            Path(__file__).resolve().parents[2] / "maturity_scoring_criteria.json",
        ]

        for path in search_paths:
            if path.exists():
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh)

        logger.warning(
            "maturity_scoring_criteria.json not found. Searched: %s",
            [str(p) for p in search_paths],
        )
        return {"criteria": []}

    def _get_maturity_criteria_for_cluster(self, cluster_id: str) -> List[Dict[str, Any]]:
        scoring = self._load_maturity_scoring()
        cluster_id = cluster_id.upper()
        return [
            row for row in scoring.get("criteria", [])
            if str(row.get("cluster_id", "")).upper() == cluster_id
        ]

    def _get_cluster(self, cluster_id: str) -> Dict[str, Any]:
        """Return the cluster dict for the given cluster_id."""
        cluster_map = self._load_cluster_map()
        cluster = next(
            (c for c in cluster_map.get("clusters", []) if c.get("cluster_id") == cluster_id.upper()),
            None,
        )
        if not cluster:
            available = [c.get("cluster_id") for c in cluster_map.get("clusters", [])]
            raise ValueError(
                f"Unknown cluster_id: '{cluster_id}'. Available: {available}"
            )
        return cluster

    # ── Console print ──────────────────────────────────────────────────────

    @staticmethod
    def print_answer(result: Dict[str, Any]) -> None:
        """Pretty-print a Q&A result."""
        print(f"\n  Query    : {result.get('query','')}")
        print(f"  Controls : {', '.join(result.get('retrieved_controls',[]))}")
        print(f"\n  Answer")
        print(f"  {'─'*54}")
        answer = result.get("answer", "")
        # Word-wrap at 80 chars
        for line in answer.split("\n"):
            while len(line) > 78:
                print(f"  {line[:78]}")
                line = line[78:]
            print(f"  {line}")

    @staticmethod
    def print_questionnaire(q: Dict[str, Any]) -> None:
        """Pretty-print a RAG-generated 3-part questionnaire."""
        print(f"\n  ┌─ Assessment: {q.get('name')} {'─'*30}")

        # Part 1
        print(f"  │")
        print(f"  │  PART 1 — Pre-Assessment Checklist ({len(q.get('part1_checklist', []))} items)")
        for row in q.get("part1_checklist", []):
            print(f"  │   {row.get('number', '?')}. [{row.get('sub_topic', '')}] {row.get('question_text', '')[:90]}")

        # Part 2
        print(f"  │")
        print(f"  │  PART 2 — Interview Questions ({len(q.get('question_groups', []))} sub-topics)")
        for group in q.get("question_groups", []):
            sub = group.get("sub_topic", "?")
            iq_count = len(group.get("questions", []))
            print(f"  │   ▸ {sub} ({iq_count} interview questions)")
            mg = group.get("maturity_guide", {})
            if mg.get("mature"):
                print(f"  │     Mature    : {mg['mature'][:80]}")
            if mg.get("critical_gap"):
                print(f"  │     Crit Gap  : {mg['critical_gap'][:80]}")

        # Part 3
        print(f"  │")
        print(f"  │  PART 3 — Evidence Tracker ({len(q.get('part3_tracker', []))} items)")
        for row in q.get("part3_tracker", [])[:5]:
            print(f"  │   [{row.get('sub_topic', '')}] {row.get('evidence_request', '')[:80]}")

        print(f"  └{'─'*50}")
