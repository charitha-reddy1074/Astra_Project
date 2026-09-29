"""
questionnaire.py — Questionnaire generator using LangChain + Groq LLM.

For each NIST CSF control, calls LLM (llama-3.3-70b-versatile) and
returns a structured JSON questionnaire with audit-style questions.

CHANGES (Market Assessment format):
  - Added MARKET_ASSESSMENT_SYSTEM_PROMPT: produces sub_topic grouping,
    Part-1-style yes/no+tool questions, maturity guide (Mature/Partial/
    Critical Gap), evidence list, and red_flags per control group.
  - Added MARKET_ASSESSMENT_HUMAN_PROMPT matching the new schema.
  - QuestionnaireBuilder.generate_for_control() now accepts an optional
    `output_format` kwarg. When set to "market_assessment" it uses the
    new prompt and returns sub_topic, maturity_guide, evidence_to_request,
    and red_flags alongside the existing questions list.
  - Added generate_market_assessment_for_domain() method that batches
    controls into sub-topic groups and returns the full 3-part structure.
"""

from __future__ import annotations

import json
import time
import os
import re
from typing import Any, Dict, List, Optional, Sequence
import uuid

try:
    from langchain_core.documents import Document
except ModuleNotFoundError:  # optional dep; only generate_from_retrieved_docs needs it
    Document = "Document"  # type: ignore  # placeholder for the unused type hint
from backend.core.utils import LLMClient, generate_id, logger, timestamp, parse_llm_json


def _render_template(template: str, values: Dict[str, Any]) -> str:
    return re.sub(r"{{\s*(\w+)\s*}}", lambda m: str(values.get(m.group(1), "")), template)


# ── Per-control system prompt ────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are a senior cybersecurity compliance auditor specialising in NIST CSF 2.0 assessments.

STRICT SCOPING RULES:
1. Generate questions addressing ONLY the specific control statement provided.
2. Do NOT generate questions about adjacent or related controls.
3. Each question must directly test whether the organisation satisfies this exact control outcome.
4. Map each question to a specific NIST SP 800-53 reference from the cross-references list.
5. Questions must be measurable and evidence-based.

QUESTION DEPTH RULES:
- Generate AS MANY questions as this control needs — at least 4, at most 10.
  A simple control may need 4-5; a broad or high-risk control should get more.
- The set of questions MUST together do three jobs:
  a. VERIFY GENUINITY OF EVIDENCE — probe whether the evidence the organisation
     would provide is authentic, current, and consistent (dates, system names,
     who produced it, whether it matches the claimed process).
  b. VERIFY THE POLICY — confirm a written, approved, reviewed policy exists
     (owner, approval date, review cycle, exceptions process).
  c. VERIFY THE IMPLEMENTATION — confirm the control operates in practice
     (how, who, how often, what breaks, how gaps are detected and fixed).
- Mix question types across the set: yes_no, maturity_rating, free_text,
  evidence_upload. Do not use a single type for every question.

IMPORTANT OUTPUT RULES:
- Output ONLY a valid JSON object. No preamble, no explanation, no markdown fences.
- Do NOT include <think> tags or reasoning text in your response.
- Your entire response must be parseable by json.loads().
- Keep question_text under 25 words. Keep expected_evidence under 15 words.
- The "questions" array must contain between 4 and 10 items.

Output ONLY this JSON (no extra fields):
{
    "control_id": "<control_id>",
    "control_statement": "<one-sentence summary>",
    "nist_800_53_refs": ["<ref1>", "<ref2>"],
    "questions": [
        {
            "question_id": "<uuid>",
            "question_text": "<audit question, max 25 words>",
            "question_type": "<yes_no | maturity_rating | free_text | evidence_upload>",
            "nist_800_53_ref": "<e.g. IA-4>",
            "expected_evidence": "<artefacts, max 15 words>",
            "maturity_level": "<Initial | Defined | Managed | Optimizing>",
            "weight": <1-5 integer>
        }
    ],
    "overall_risk_rating": "<Critical | High | Medium | Low>",
    "assessment_notes": "<one sentence guidance>"
}
"""

# ── NEW: Market Assessment style system prompt ────────────────────────────────
MARKET_ASSESSMENT_SYSTEM_PROMPT = """\
You are a senior cybersecurity assessor writing a Market Assessment document.

Given a set of controls for a named security domain sub-topic, produce output
that mirrors the three-section structure used in professional market assessments:

═══════════════════════════════════════════════════════
PART 1 — PRE-ASSESSMENT QUESTION (one per sub-topic)
═══════════════════════════════════════════════════════
  - ONE short yes/no pre-assessment question.
  - Must name the specific technology, process, or capability being assessed (e.g. "MFA", "SIEM", "PAM solution").
  - Maximum 20 words. Must NOT echo the control statement verbatim.
  - Always use this response format: "Yes / No / Partial"
  - Always include detail_prompt: "If yes or partial, specify the tool or technology in use."
  - This question is the SAME type every time: a capability-check question ("Do you have X?", "Is X enforced?", "Is X in place?")

═══════════════════════════════════════════════════════
PART 2 — STRUCTURED INTERVIEW QUESTIONS (exactly 10 per sub-topic)
═══════════════════════════════════════════════════════
  - Generate EXACTLY 10 structured question objects. No more, no fewer.
  - Each question MUST be SPECIFIC to the exact controls in this sub-topic. Do NOT generate generic questions.
  - Each question is a STRUCTURED OBJECT in the framework question format (not a plain string).
  - Questions must be investigative and open-ended — auditors ask these in a session.
  - Do NOT repeat the control statement text as the question.
  - Do NOT ask "is X implemented?" — that belongs in Part 1.
  - Instead, probe HOW, WHO, WHEN, WHAT EVIDENCE, and WHAT GAPS exist for THESE SPECIFIC controls.
  - Use diverse question_types across the 10: YES_NO, SCALE_1_5, FREE_TEXT, MULTI_CHOICE.
  - MULTI_CHOICE questions must include 3–4 realistic answer choices.
  - Assign realistic weight (1–5) and maturity_level (1–5 integer matching the maturity ladder).
  - Include expected_evidence_types as specific artefact names relevant to THIS sub-topic ONLY.
    For example: for identity controls use "iam_policy", "access_review_log", "mfa_config_screenshot".
    For data security controls use "encryption_policy", "tls_config", "data_classification_schema".
    Do NOT reuse the same evidence types across different sub-topics.
  - Each question's expected_evidence_types should match what that specific control would need.

  GOOD Part 2 question examples:
    - "How frequently are privileged account access rights reviewed and by whom?"
    - "What process triggers immediate credential revocation when an employee leaves?"
    - "Describe how exceptions to the MFA policy are tracked and approved."

  BAD Part 2 question examples (DO NOT generate these):
    - "Is MFA enforced?" (yes/no capability check — belongs in Part 1)
    - "Are identities managed?" (too vague, echoes control statement)

═══════════════════════════════════════════════════════
PART 3 — MATURITY GUIDE, EVIDENCE, RED FLAGS
═══════════════════════════════════════════════════════
  3. MATURITY ASSESSMENT GUIDE with exactly three levels:
     - "mature": what a fully implemented, well-governed control looks like.
     - "partial": partially deployed, with common gap patterns described.
     - "critical_gap": the specific risk and consequence if absent.
     All three must be domain-specific, not generic.

  4. EVIDENCE TO REQUEST: 3–6 specific artefact names the auditor must collect.

  5. RED FLAGS with remediation actions:
     - 2–4 critical findings specific to this sub-topic.
     - Each has: "critical_gap" (one sentence) and "recommended_action" (one sentence).

IMPORTANT OUTPUT RULES:
- Output ONLY valid JSON. No preamble, no explanation, no markdown fences.
- Do NOT include <think> tags or reasoning text.
- Your entire response must be parseable by json.loads().
- question_id values in questions must be valid UUID strings.
- The "questions" array MUST contain EXACTLY 10 items — never fewer, never more.
- The "part1_question" MUST be exactly 1 item.
- The "choices" field is only required when question_type is MULTI_CHOICE. Omit it for other types.

Output EXACTLY this JSON schema (no extra fields):
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
      "text": "<investigative audit question — HOW/WHO/WHEN/WHAT, max 30 words>",
      "help_text": "<optional auditor guidance, max 20 words>",
      "question_type": "<YES_NO | SCALE_1_5 | FREE_TEXT | MULTI_CHOICE>",
      "choices": ["<choice 1>", "<choice 2>", "<choice 3>"],
      "weight": <1-5 integer>,
      "maturity_level": <1-5 integer>,
      "expected_evidence_types": ["<artefact_name_1>", "<artefact_name_2>"]
    }
  ],
  "maturity_guide": {
    "mature": "<concrete description of mature state for this sub-topic>",
    "partial": "<concrete description of partial state with typical gap patterns>",
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
"""

MARKET_ASSESSMENT_HUMAN_PROMPT = """\
Framework   : {{ framework_name }}
Domain      : {{ domain_name }} ({{ domain_id }})
Sub-topic   : {{ sub_topic }}
Control IDs : {{ control_ids }}
Statements  : {{ control_statements }}
Evidence    : {{ evidence_types }}
Cross-refs  : {{ cross_references }}

Generate the market assessment section for this sub-topic now. Output only valid JSON.
"""

# ── NEW: Maturity-guide-driven DIAGNOSTIC question generation ─────────────────
# These prompts power generate_diagnostic_questions_for_domain(). Unlike the
# prompts above (which invent questions from control statements alone), this set
# is GIVEN the per-sub-topic maturity scoring criteria (Mature / Partial /
# Critical Gap descriptions) and is told to write questions whose answers reveal
# WHERE on that ladder the organisation sits and WHICH gap they have — the same
# way a teacher writes a quiz that pinpoints exactly which concept a student is
# missing.
MATURITY_DIAGNOSTIC_SYSTEM_PROMPT = """\
You are a senior cybersecurity assessor designing a DIAGNOSTIC interview for one
security domain. Your job is NOT to ask whether a control exists. Your job is to
write questions whose answers reveal, for each sub-topic, whether the
organisation is MATURE, PARTIAL, or at a CRITICAL GAP — and which specific gap
they have.

Think like a teacher who has been told the marking rubric in advance. You are
given, for every sub-topic, three rubric columns:
  - "mature"       : what a fully implemented, well-governed control looks like.
  - "partial"      : what a half-implemented control with typical gaps looks like.
  - "critical_gap" : the specific risk/consequence when the control is absent.
You must write questions so that a respondent's answer can be slotted into
exactly one of those three columns, and so that a weak answer exposes the precise
gap described in "partial" or "critical_gap".

RULES
1. Generate EXACTLY {{ count }} questions for the whole domain.
2. Distribute the questions across the sub-topics roughly in proportion to how
   many sub-topics there are. Cover every sub-topic at least once before adding a
   second question to any sub-topic.
3. Tag every question with the control_id and sub_topic it diagnoses (use the
   exact control_id and sub_topic strings provided).
4. Each question must DISCRIMINATE between the three maturity columns of its
   sub-topic. Probe HOW, WHO, HOW OFTEN, WHAT EVIDENCE, and WHAT BREAKS — never
   a bare "do you have X?".
5. Use the SAMPLE QUESTIONS only as a style/tone reference. Do NOT copy them
   verbatim; write sharper, gap-seeking versions grounded in the rubric wording.
6. For every question, fill "maturity_signals" with the concrete answer you would
   expect from an organisation at each level FOR THIS SPECIFIC QUESTION, derived
   from that sub-topic's rubric. Fill "gap_if_deficient" with the single most
   important gap a weak answer would reveal.
7. Use diverse question_types: FREE_TEXT, SCALE_1_5, YES_NO, MULTI_CHOICE.
   MULTI_CHOICE must include 3–4 realistic choices ordered worst→best so the
   choice itself maps to a maturity level. Omit "choices" for other types.
8. expected_evidence_types must be specific artefacts that would PROVE the
   answer for that sub-topic.

OUTPUT RULES
- Output ONLY valid JSON. No preamble, no markdown fences, no <think> tags.
- The entire response must be parseable by json.loads().
- The "questions" array MUST contain EXACTLY {{ count }} items.
- question_id values must be valid UUID strings.

Output EXACTLY this JSON schema (no extra fields):
{
  "domain": "<domain name>",
  "questions": [
    {
      "question_id": "<uuid>",
      "text": "<diagnostic question, max 30 words>",
      "question_type": "<FREE_TEXT | SCALE_1_5 | YES_NO | MULTI_CHOICE>",
      "choices": ["<worst>", "<...>", "<best>"],
      "control_id": "<the control_id this diagnoses>",
      "sub_topic": "<the sub_topic this diagnoses>",
      "weight": <1-5 integer>,
      "expected_evidence_types": ["<artefact_1>", "<artefact_2>"],
      "maturity_signals": {
        "mature": "<what a mature answer to THIS question sounds like>",
        "partial": "<what a partial answer to THIS question sounds like>",
        "critical_gap": "<what a critical-gap answer to THIS question sounds like>"
      },
      "gap_if_deficient": "<the specific gap a weak answer reveals>"
    }
  ]
}
"""

MATURITY_DIAGNOSTIC_HUMAN_PROMPT = """\
Framework   : {{ framework_name }}
Domain      : {{ domain_name }}
Description : {{ domain_description }}
Generate    : {{ count }} diagnostic questions distributed across the sub-topics below.

For each sub-topic you are given its maturity scoring rubric (Mature / Partial /
Critical Gap) and a few sample questions for style only.

{{ subtopics_block }}

Write the {{ count }} diagnostic questions now. Output only valid JSON matching the schema.
"""

# ── Original domain system prompt (unchanged) ─────────────────────────────────
DOMAIN_SYSTEM_PROMPT = """\
You are a senior cybersecurity compliance auditor specialising in NIST CSF 2.0.

STRICT DOMAIN RULES:
1. Generate exactly {{ question_count }} questions for the selected domain.
2. Every question must belong to the selected domain and map to one of the
   provided controls.
3. Do NOT ask generic cybersecurity questions.
4. Each question must be specific, auditable, and evidence-based.
5. Attach a single NIST SP 800-53 reference to each question.

IMPORTANT OUTPUT RULES:
- Output ONLY valid JSON. No preamble, no explanation, no markdown.
- Do NOT wrap the JSON in ```json``` fences.
- Do NOT include <think> or reasoning text in your response.
- Your entire response must be parseable by json.loads().

Output ONLY valid JSON with this structure:
{
    "questionnaire_type": "domain",
    "questionnaire_id": "<domain_questionnaire_id>",
    "framework_name": "<framework_name>",
    "domain_id": "<domain_id>",
    "domain_name": "<domain_name>",
    "domain_description": "<one line description>",
    "question_count": <integer>,
    "questions": [
        {
            "question_id": "<uuid>",
            "question_text": "<specific audit question>",
            "question_type": "<yes_no | maturity_rating | free_text | evidence_upload>",
            "control_id": "<related control id>",
            "nist_800_53_ref": "<single reference>",
            "expected_evidence": "<exact artefacts that prove compliance>",
            "maturity_level": "<Initial | Defined | Managed | Optimizing>",
            "risk_description": "<specific risk if this domain control fails>",
            "validation_criteria": "<how to judge a satisfactory answer>",
            "weight": <1-5 integer>
        }
    ],
    "overall_risk_rating": "<Critical | High | Medium | Low>",
    "assessment_notes": "<auditor guidance specific to this domain>"
}

Generate exactly {{ question_count }} questions. Distribute questions across
the provided controls so the whole domain is covered.
"""

HUMAN_PROMPT = """\
Framework  : {{ framework_name }}
Domain     : {{ domain_name }} ({{ domain_id }})
Control ID : {{ control_id }}
Statement  : {{ control_statement }}
Maturity   : {{ maturity_levels }}
Evidence   : {{ evidence_types }}
Cross-refs : {{ cross_references }}

Generate the assessment questionnaire now. Output only valid JSON.
"""

# ── Sub-topic grouping heuristics ─────────────────────────────────────────────
# Maps NIST CSF control-id prefixes to human-readable sub-topic names.
# Controls not matched here fall back to their domain name.
_SUBTOPIC_MAP: Dict[str, str] = {
    # Govern
    "GV.OC": "Organizational Context",
    "GV.RM": "Risk Management Strategy",
    "GV.RR": "Roles & Responsibilities",
    "GV.PO": "Policy",
    "GV.OV": "Oversight",
    "GV.SC": "Supply Chain Risk",
    # Identify
    "ID.AM": "Asset Management",
    "ID.RA": "Risk Assessment",
    "ID.IM": "Improvement",
    # Protect
    "PR.AA": "Identity & Access Management",
    "PR.AT": "Awareness & Training",
    "PR.DS": "Data Security",
    "PR.PS": "Platform Security",
    "PR.IR": "Technology Infrastructure Resilience",
    # Detect
    "DE.CM": "Continuous Monitoring",
    "DE.AE": "Adverse Event Analysis",
    # Respond
    "RS.MA": "Incident Management",
    "RS.AN": "Incident Analysis",
    "RS.CO": "Incident Response Reporting",
    "RS.MI": "Incident Mitigation",
    # Recover
    "RC.RP": "Incident Recovery Plan",
    "RC.CO": "Recovery Communications",
}


def _sub_topic_for_control(control_id: str, domain_name: str) -> str:
    """Return a human-readable sub-topic label for a given control ID."""
    prefix = ".".join(control_id.split(".")[:2]) if "." in control_id else control_id[:5]
    return _SUBTOPIC_MAP.get(prefix, domain_name)


def _group_controls_by_subtopic(
    controls: List[Dict[str, Any]], domain_name: str
) -> Dict[str, List[Dict[str, Any]]]:
    """Group controls by sub-topic label, preserving insertion order."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for control in controls:
        cid = control.get("control_id", "")
        label = _sub_topic_for_control(cid, domain_name)
        groups.setdefault(label, []).append(control)
    return groups


class QuestionnaireBuilder:
    """
    Builds assessment questionnaires for NIST CSF controls via Groq LLM.

    Usage
    -----
    builder = QuestionnaireBuilder(llm_api_key="...")

    # Original per-control mode (unchanged):
    questionnaire = builder.generate_for_control(control_metadata)

    # NEW: Market Assessment format for a whole domain:
    ma_sections = builder.generate_market_assessment_for_domain(
        framework, domain_id="PR"
    )
    """

    def __init__(self, llm_api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self._chain: Optional[LLMClient] = None
        self._model = model
        if llm_api_key:
            self._chain = LLMClient(llm_api_key, model)
        logger.info("QuestionnaireBuilder initialised (model: %s)", model)

    # ── Public API ─────────────────────────────────────────────────────────

    def generate_for_control(
        self,
        control_metadata: Dict[str, Any],
        output_format: str = "standard",
    ) -> Dict[str, Any]:
        """
        Generate a questionnaire for a single control.

        Parameters
        ----------
        control_metadata : flat dict from FrameworkNormalizer.flat_controls()
                           or from a Chroma Document's metadata field.
        output_format    : "standard" (default, unchanged behaviour) or
                           "market_assessment" (new Market Assessment style).
        """
        control_id = control_metadata.get("control_id", "UNKNOWN")
        logger.info("Generating questionnaire for %s (format: %s)…", control_id, output_format)

        if output_format == "market_assessment":
            return self._generate_market_assessment_for_control(control_metadata)

        # ── Original standard path (unchanged) ────────────────────────────
        payload = {
            "framework_name":    control_metadata.get("framework_name", control_metadata.get("name", "NIST CSF 2.0")),
            "domain_id":         control_metadata.get("domain_id",        ""),
            "domain_name":       control_metadata.get("domain_name",      ""),
            "control_id":        control_id,
            "control_statement": control_metadata.get("control_statement", ""),
            "maturity_levels":   control_metadata.get("maturity_levels",  ""),
            "evidence_types":    control_metadata.get("evidence_types",   ""),
            "cross_references":  self._cross_reference_text(control_metadata.get("cross_references", "")),
        }

        if not self._chain:
            raise RuntimeError("LLM API key not provided; cannot generate questionnaire")

        user_prompt = _render_template(HUMAN_PROMPT, payload)
        raw = self._chain.invoke({
            "user_prompt": user_prompt,
            "system_prompt": SYSTEM_PROMPT,
            "max_tokens": 2500,
        })

        logger.debug("RAW LLM output for %s: %r", control_id, raw[:500] if raw else "EMPTY")
        questionnaire_data = self._parse_json(raw, control_id)

        return {
            "framework_id": control_metadata.get("framework_id", str(uuid.uuid4())),
            "name": control_metadata.get("framework_name", control_metadata.get("name", "NIST CSF 2.0")),
            "version": control_metadata.get("version", "2.0"),
            "source_format": control_metadata.get("source_format", "json"),
            "ingested_at": control_metadata.get("ingested_at", timestamp()),
            "Domains": [
                {
                    "domain_id": control_metadata.get("domain_id", ""),
                    "domain_name": control_metadata.get("domain_name", ""),
                    "Controls": [
                        {
                            "control_id": control_id,
                            "control_statement": control_metadata.get("control_statement", ""),
                            "maturity_levels": control_metadata.get("maturity_levels", []),
                            "expected_evidence_types": control_metadata.get("expected_evidence_types", []),
                            "cross_references": control_metadata.get("cross_references", []),
                            "Questions": [
                                {
                                    "question_id": q.get("question_id") or generate_id(),
                                    "question_text": q.get("question_text", ""),
                                    "question_type": q.get("question_type", "free_text"),
                                    "weight": int(q.get("weight", 3)),
                                }
                                # LLM decides the count per control; cap at 10.
                                for q in questionnaire_data.get("questions", [])[:10]
                            ],
                        }
                    ],
                }
            ],
        }

    def generate_market_assessment_for_domain(
        self,
        framework: Dict[str, Any],
        domain_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Generate Market Assessment sections for every sub-topic in a domain.

        Each returned dict is one sub-topic section containing:
          - sub_topic            : label (e.g. "Identity Lifecycle & Directory")
          - control_ids          : list of controls in this sub-topic
          - part1_question       : single yes/no+detail pre-assessment row (Part 1)
          - questions            : list of structured question objects in framework
                                   JSON format (text, question_type, weight,
                                   maturity_level, expected_evidence_types) — Part 2
          - maturity_guide       : {mature, partial, critical_gap}
          - evidence_to_request  : list of artefact names
          - red_flags            : [{critical_gap, recommended_action}, ...]

        Returns a list of sub-topic dicts, one per sub-topic in the domain.
        """
        target_domain_id = domain_id.upper()
        domain = next(
            (d for d in framework.get("domains", []) if d.get("domain_id") == target_domain_id),
            None,
        )
        if not domain:
            raise ValueError(f"Domain not found: {domain_id}")

        domain_name = domain.get("domain_name", target_domain_id)
        controls = list(domain.get("controls", []))
        if not controls:
            raise ValueError(f"No controls in domain: {domain_id}")

        subtopic_groups = _group_controls_by_subtopic(controls, domain_name)
        sections: List[Dict[str, Any]] = []

        _INTER_CALL_DELAY = 2.0      # seconds between sub-topic LLM calls
        _RATE_LIMIT_BACKOFF = 62.0   # seconds to wait on a 429 before one retry

        subtopic_items = list(subtopic_groups.items())
        for loop_idx, (sub_topic_label, group_controls) in enumerate(subtopic_items):
            logger.info("Generating MA section → %s / %s", domain_name, sub_topic_label)
            meta = {
                "framework_name":     framework.get("framework_name", framework.get("name", "NIST CSF 2.0")),
                "domain_id":          target_domain_id,
                "domain_name":        domain_name,
                "sub_topic":          sub_topic_label,
                "control_ids":        [c.get("control_id", "") for c in group_controls],
                "control_statements": " | ".join(
                    c.get("control_statement", "") for c in group_controls
                ),
                "evidence_types": ", ".join(
                    ev
                    for c in group_controls
                    for ev in (c.get("expected_evidence_types") or []) # type: ignore
                ),
                "cross_references": self._cross_reference_text(
                    [xr for c in group_controls for xr in (c.get("cross_references") or [])]
                ),
            }

            # ── One retry on 429; otherwise use stub ──────────────────────
            for attempt in range(2):
                try:
                    section = self._generate_market_assessment_for_subtopic(meta)
                    sections.append(section)
                    break
                except Exception as exc:
                    err_str = str(exc)
                    is_rate_limited = "429" in err_str or "rate limit" in err_str.lower()
                    if is_rate_limited and attempt == 0:
                        logger.warning(
                            "MA 429 rate-limit on %s / %s — waiting %.0fs then retrying",
                            domain_name, sub_topic_label, _RATE_LIMIT_BACKOFF,
                        )
                        time.sleep(_RATE_LIMIT_BACKOFF)
                        continue   # retry
                    logger.warning(
                        "MA section generation failed for %s / %s: %s",
                        domain_name, sub_topic_label, exc,
                    )
                    sections.append(self._stub_market_assessment_section(meta))
                    break

            # Throttle between calls, but not after the final one
            if loop_idx < len(subtopic_items) - 1:
                time.sleep(_INTER_CALL_DELAY)

        return sections

    def generate_diagnostic_questions_for_domain(
        self,
        domain_name: str,
        sub_topic_guides: List[Dict[str, Any]],
        domain_description: str = "",
        framework_name: str = "",
        count: int = 10,
        extra_instructions: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Generate ``count`` DIAGNOSTIC questions for a domain from maturity guides.

        Unlike generate_market_assessment_for_domain (which invents questions from
        control statements), this method is GIVEN the per-sub-topic maturity
        scoring rubric and writes questions whose answers reveal where on the
        Mature / Partial / Critical-Gap ladder the organisation sits and which
        gap it has.

        Parameters
        ----------
        domain_name        : human-readable domain name.
        sub_topic_guides   : one dict per sub-topic/control, each with:
            { "control_id", "sub_topic", "control_statement",
              "maturity_guide": {"mature","partial","critical_gap"},
              "expected_evidence_types": [...],
              "sample_questions": ["...", ...] }
        domain_description : optional one-line domain description.
        framework_name     : optional framework label for prompt context.
        count              : number of questions to produce for the domain.

        Returns a flat list of ``count`` question dicts. Each is tagged with
        control_id/sub_topic and carries maturity_signals + gap_if_deficient.
        Raises RuntimeError when no LLM is configured (callers fall back).
        """
        if not self._chain:
            raise RuntimeError("LLM API key not provided; cannot generate diagnostic questions")

        guides = [g for g in sub_topic_guides if any((g.get("maturity_guide") or {}).values())]
        if not guides:
            raise ValueError("No maturity guides provided for diagnostic generation")

        subtopics_block = self._render_subtopics_block(guides)
        description = domain_description or f"{domain_name} domain"
        # Fold the assessment designer's custom instructions into the domain
        # description so they reach the LLM as additional generation guidance.
        if extra_instructions:
            description = (
                f"{description}\n\nADDITIONAL INSTRUCTIONS FROM THE ASSESSMENT "
                f"DESIGNER (follow these when writing the questions):\n{extra_instructions}"
            )
        payload = {
            "framework_name":     framework_name or "Market Assessment",
            "domain_name":        domain_name,
            "domain_description": description,
            "count":              count,
            "subtopics_block":    subtopics_block,
        }
        system_prompt = _render_template(MATURITY_DIAGNOSTIC_SYSTEM_PROMPT, {"count": count})
        user_prompt = _render_template(MATURITY_DIAGNOSTIC_HUMAN_PROMPT, payload)

        raw = self._chain.invoke({
            "user_prompt": user_prompt,
            "system_prompt": system_prompt,
            "max_tokens": 4096,
        })
        logger.debug("Diagnostic raw output for %s: %r", domain_name, raw[:500] if raw else "EMPTY")
        parsed = self._parse_json(raw, domain_name)

        questions = parsed.get("questions", []) if isinstance(parsed, dict) else []
        valid_cids = {g.get("control_id", "") for g in guides}
        guide_by_cid = {g.get("control_id", ""): g for g in guides}
        primary = guides[0]

        normalised: List[Dict[str, Any]] = self._normalise_diagnostic_questions(
            questions, valid_cids, guide_by_cid, primary
        )

        # Pad with deterministic, rubric-derived stubs / trim to exactly `count`
        if len(normalised) < count:
            normalised.extend(
                self._diagnostic_stub_questions(guides, count - len(normalised), start_idx=len(normalised))
            )
        return normalised[:count]

    @staticmethod
    def _render_subtopics_block(guides: List[Dict[str, Any]]) -> str:
        """Render the per-sub-topic rubric + sample questions block for the prompt."""
        blocks: List[str] = []
        for idx, g in enumerate(guides, 1):
            guide = g.get("maturity_guide") or {}
            samples = g.get("sample_questions") or []
            sample_text = "\n".join(f"      - {s}" for s in samples[:4]) or "      (none)"
            evidence = ", ".join(g.get("expected_evidence_types") or []) or "(none listed)"
            blocks.append(
                f"SUB-TOPIC {idx}: {g.get('sub_topic', g.get('control_id', ''))} "
                f"[control_id: {g.get('control_id', '')}]\n"
                f"  Control statement: {g.get('control_statement', '')}\n"
                f"  Rubric — MATURE       : {guide.get('mature', '')}\n"
                f"  Rubric — PARTIAL      : {guide.get('partial', '')}\n"
                f"  Rubric — CRITICAL GAP : {guide.get('critical_gap', '')}\n"
                f"  Expected evidence: {evidence}\n"
                f"  Sample questions (style only):\n{sample_text}"
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _normalise_diagnostic_questions(
        questions: List[Dict[str, Any]],
        valid_cids: set,
        guide_by_cid: Dict[str, Dict[str, Any]],
        primary: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Clean LLM diagnostic output: fix ids, control tags, and fill signals."""
        out: List[Dict[str, Any]] = []
        for q in questions:
            if not isinstance(q, dict):
                continue
            cid = str(q.get("control_id", "")).strip()
            if cid not in valid_cids:
                cid = primary.get("control_id", "")
            guide = guide_by_cid.get(cid, primary)
            rubric = guide.get("maturity_guide") or {}
            signals = q.get("maturity_signals") or {}
            qtype = str(q.get("question_type", "FREE_TEXT")).upper()
            clean: Dict[str, Any] = {
                "question_id": q.get("question_id") or generate_id(),
                "text": q.get("text") or q.get("question_text", ""),
                "question_type": qtype,
                "control_id": cid,
                "sub_topic": q.get("sub_topic") or guide.get("sub_topic", cid),
                "weight": int(q.get("weight", 3)) if str(q.get("weight", "")).strip().isdigit() else 3,
                "expected_evidence_types": q.get("expected_evidence_types")
                    or list(guide.get("expected_evidence_types") or []),
                "maturity_signals": {
                    "mature": signals.get("mature") or rubric.get("mature", ""),
                    "partial": signals.get("partial") or rubric.get("partial", ""),
                    "critical_gap": signals.get("critical_gap") or rubric.get("critical_gap", ""),
                },
                "gap_if_deficient": q.get("gap_if_deficient") or rubric.get("critical_gap", ""),
            }
            if qtype == "MULTI_CHOICE" and q.get("choices"):
                clean["choices"] = q.get("choices")
            if not clean["text"]:
                continue
            out.append(clean)
        return out

    @staticmethod
    def _diagnostic_stub_questions(
        guides: List[Dict[str, Any]],
        needed: int,
        start_idx: int = 0,
    ) -> List[Dict[str, Any]]:
        """Deterministic rubric-derived questions used to pad/replace LLM output."""
        stubs: List[Dict[str, Any]] = []
        if not guides:
            return stubs
        i = start_idx
        while len(stubs) < needed:
            g = guides[i % len(guides)]
            guide = g.get("maturity_guide") or {}
            sub = g.get("sub_topic") or g.get("control_id", "this area")
            cid = g.get("control_id", "")
            stubs.append({
                "question_id": generate_id(),
                "text": (
                    f"For {sub}, describe how it is implemented, who owns it, how often "
                    f"it is reviewed, and what evidence proves it — so we can place you "
                    f"between '{(guide.get('critical_gap') or '')[:60]}' and "
                    f"'{(guide.get('mature') or '')[:60]}'."
                ),
                "question_type": "FREE_TEXT",
                "control_id": cid,
                "sub_topic": sub,
                "weight": 3,
                "expected_evidence_types": list(g.get("expected_evidence_types") or []),
                "maturity_signals": {
                    "mature": guide.get("mature", ""),
                    "partial": guide.get("partial", ""),
                    "critical_gap": guide.get("critical_gap", ""),
                },
                "gap_if_deficient": guide.get("critical_gap", ""),
            })
            i += 1
        return stubs

    def generate_for_domain(
        self,
        framework: Dict[str, Any],
        domain_id: str,
        question_count: int = 10,
    ) -> Dict[str, Any]:
        """Generate a domain-level questionnaire with exactly *question_count* questions (original)."""
        from backend.services.normalizer import DOMAIN_DESCRIPTIONS

        target_domain_id = domain_id.upper()
        domain = next(
            (d for d in framework.get("domains", []) if d.get("domain_id") == target_domain_id),
            None,
        )
        if not domain:
            raise ValueError(f"Domain not found: {domain_id}")

        domain_name        = domain.get("domain_name", target_domain_id)
        domain_description = domain.get("description") or f"{domain_name} domain"
        controls           = list(domain.get("controls", []))
        if not controls:
            raise ValueError(f"No controls available for domain: {domain_id}")

        domain_qid         = f"domain-{target_domain_id}"
        generated_questions: List[Dict[str, Any]] = []
        question_types     = ["yes_no", "free_text", "evidence_upload", "maturity_rating"]

        for idx in range(question_count):
            control    = controls[idx % len(controls)]
            qtype      = question_types[idx % len(question_types)]
            control_id = control.get("control_id", "UNKNOWN")
            statement  = control.get("control_statement", "")
            ref        = self._primary_nist_ref(control)

            generated_questions.append({
                "question_id":        generate_id(),
                "question_text":      self._domain_question_text(domain_name, domain_description, control_id, statement, qtype),
                "question_type":      qtype,
                "control_id":         control_id,
                "nist_800_53_ref":    ref,
                "expected_evidence":  ", ".join(control.get("expected_evidence_types", [])),
                "maturity_level":     control.get("maturity_levels", ["Defined"])[0] if control.get("maturity_levels") else "Defined",
                "risk_description":   f"Weak implementation of {control_id} can reduce the effectiveness of the {domain_name} domain.",
                "validation_criteria": "Review policy, operational evidence, and implementation details for consistency.",
                "weight":             3 if qtype in ("free_text", "evidence_upload") else 4,
            })

        return {
            "framework_id": framework.get("framework_id", ""),
            "name": framework.get("framework_name", framework.get("name", "NIST CSF 2.0")),
            "version": framework.get("version", "2.0"),
            "source_format": "domain_gen",
            "ingested_at": timestamp(),
            "Domains": [
                {
                    "domain_id": target_domain_id,
                    "domain_name": domain_name,
                    "Controls": [
                        {
                            "control_id": q.get("control_id"),
                            "control_statement": "Generated control context.",
                            "maturity_levels": [q.get("maturity_level")],
                            "expected_evidence_types": [q.get("expected_evidence")],
                            "cross_references": [{"other_framework_id": "nist-sp-800-53", "other_control_id": q.get("nist_800_53_ref")}],
                            "Questions": [q],
                        }
                        for q in generated_questions
                    ],
                }
            ],
        }

    @staticmethod
    def attach_questionnaires_to_framework(
        framework: Dict[str, Any],
        questionnaires: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Return a canonical framework dict with question lists attached to controls."""
        q_index: Dict[str, List[Dict[str, Any]]] = {}
        for qset in questionnaires:
            for domain in qset.get("Domains", []):
                for control in domain.get("Controls", []):
                    cid = control.get("control_id")
                    if cid:
                        q_index[cid] = control.get("Questions", [])

        return {
            "framework_id": framework.get("framework_id", str(uuid.uuid4())),
            "name": framework.get("framework_name", framework.get("name", "NIST CSF 2.0")),
            "version": framework.get("version", "2.0"),
            "source_format": framework.get("source_format", "json"),
            "ingested_at": framework.get("ingested_at", timestamp()),
            "Domains": [
                {
                    "domain_id": domain.get("domain_id", ""),
                    "domain_name": domain.get("domain_name", ""),
                    "Controls": [
                        {
                            "control_id": control.get("control_id", ""),
                            "control_statement": control.get("control_statement", ""),
                            "maturity_levels": control.get("maturity_levels", []),
                            "expected_evidence_types": control.get("expected_evidence_types", []),
                            "cross_references": control.get("cross_references", []),
                            "Questions": q_index.get(control.get("control_id", ""), []),
                        }
                        for control in domain.get("controls", [])
                    ],
                }
                for domain in framework.get("domains", [])
            ],
        }

    def generate_for_framework(
        self,
        framework: Dict[str, Any],
        max_controls: Optional[int] = None,
        domain_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Generate questionnaires for all (or filtered) controls in a framework."""
        from backend.services.normalizer import FrameworkNormalizer
        flat = FrameworkNormalizer.flat_controls(framework)

        if domain_filter:
            flat = [c for c in flat if c.get("domain_id") == domain_filter.upper()]
            logger.info("Domain filter '%s' → %d controls", domain_filter, len(flat))

        if max_controls:
            flat = flat[:max_controls]

        questionnaires: List[Dict[str, Any]] = []
        total = len(flat)

        for idx, control in enumerate(flat, 1):
            print(f"  [{idx:02d}/{total}] Generating → {control.get('control_id')}", flush=True)
            try:
                q = self.generate_for_control(control)
                questionnaires.append(q)
            except Exception as exc:
                logger.warning(
                    "Failed to generate questionnaire for %s: %s",
                    control.get("control_id"), exc,
                )

        logger.info("Generated %d questionnaires out of %d controls.", len(questionnaires), total)
        return questionnaires

    def generate_from_retrieved_docs(self, docs: List[Document]) -> List[Dict[str, Any]]:
        """Generate questionnaires from a list of Chroma-retrieved Documents."""
        questionnaires: List[Dict[str, Any]] = []
        for doc in docs:
            try:
                q = self.generate_for_control(doc.metadata)
                questionnaires.append(q)
            except Exception as exc:
                logger.warning("Questionnaire gen error: %s", exc)
        return questionnaires

    # ── Private: Market Assessment helpers ────────────────────────────────

    def _generate_market_assessment_for_control(
        self, control_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Single-control wrapper that calls the MA prompt for a lone control."""
        control_id = control_metadata.get("control_id", "UNKNOWN")
        domain_name = control_metadata.get("domain_name", "")
        meta = {
            "framework_name":      control_metadata.get("framework_name", "NIST CSF 2.0"),
            "domain_id":           control_metadata.get("domain_id", ""),
            "domain_name":         domain_name,
            "sub_topic":           _sub_topic_for_control(control_id, domain_name),
            "control_ids":         control_id,
            "control_statements":  control_metadata.get("control_statement", ""),
            "evidence_types":      ", ".join(control_metadata.get("expected_evidence_types") or []),
            "cross_references":    self._cross_reference_text(control_metadata.get("cross_references", "")),
        }
        return self._generate_market_assessment_for_subtopic(meta)

    def _generate_market_assessment_for_subtopic(
        self, meta: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Call the LLM with the Market Assessment prompt and return parsed result."""
        if not self._chain:
            raise RuntimeError("LLM API key not provided; cannot generate market assessment section")

        # Serialise control_ids list for the template
        cids = meta.get("control_ids", [])
        if isinstance(cids, list):
            meta = {**meta, "control_ids": ", ".join(cids)}

        user_prompt = _render_template(MARKET_ASSESSMENT_HUMAN_PROMPT, meta) #
        raw = self._chain.invoke({
            "user_prompt": user_prompt,
            "system_prompt": MARKET_ASSESSMENT_SYSTEM_PROMPT,
            "max_tokens": 3000,  # increased to support 10 questions
        })

        logger.debug("MA raw LLM output for %s: %r", meta.get("sub_topic"), raw[:500] if raw else "EMPTY")
        parsed = self._parse_json(raw, meta.get("sub_topic", "unknown"))

        # Ensure question_id is present on the part1 question
        if "part1_question" in parsed:
            if not parsed["part1_question"].get("question_id"):
                parsed["part1_question"]["question_id"] = generate_id()
        else:
            # Synthesise a fallback Part 1 question if the LLM omitted it
            sub = parsed.get("sub_topic", meta.get("sub_topic", "this control area"))
            parsed["part1_question"] = {
                "question_id": generate_id(),
                "question_text": f"Do you have a {sub} capability in place?",
                "question_type": "yes_no_with_detail",
                "response_options": "Yes / No / Partial",
                "detail_prompt": "If yes or partial, specify the tool or technology in use.",
            }

        # Ensure question_id is present on every Part 2 structured question
        for q in parsed.get("questions", []):
            if not q.get("question_id"):
                q["question_id"] = generate_id()

        # Enforce exactly 10 Part 2 questions — pad with stubs if LLM returned fewer
        TARGET_Q = 10
        qs = parsed.get("questions", [])
        sub = parsed.get("sub_topic", meta.get("sub_topic", "this area"))
        _pad_types = ["FREE_TEXT", "SCALE_1_5", "YES_NO", "MULTI_CHOICE", "FREE_TEXT",
                      "FREE_TEXT", "SCALE_1_5", "YES_NO", "FREE_TEXT", "FREE_TEXT"]
        while len(qs) < TARGET_Q:
            idx = len(qs)
            qs.append({
                "question_id": generate_id(),
                "text": f"Describe the key controls and evidence available for {sub} (area {idx + 1}).",
                "question_type": _pad_types[idx % len(_pad_types)],
                "weight": 2,
                "maturity_level": 3,
                "expected_evidence_types": ["process_doc"],
            })
        parsed["questions"] = qs[:TARGET_Q]  # trim if LLM returned more than 10

        return parsed

    @staticmethod
    def _stub_market_assessment_section(meta: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return a control-specific stub when LLM generation fails.

        Derives one question per control from the actual control statements
        passed in meta["control_statements"], then pads to exactly 10 with
        cross-cutting operational probes that reference the sub-topic name.
        Evidence types are derived from meta["evidence_types"] rather than
        generic placeholders.
        """
        cids = meta.get("control_ids", [])
        if isinstance(cids, str):
            cids = [c.strip() for c in cids.split(",") if c.strip()]

        sub = meta.get("sub_topic", "this control area")

        # ── Parse control statements → short names ────────────────────────
        # meta["control_statements"] is a " | " joined string of full statements.
        raw_stmts = meta.get("control_statements", "")
        stmt_list: List[str] = [s.strip() for s in raw_stmts.split(" | ") if s.strip()]

        # Build a short label for each control: text before the first colon or
        # first sentence boundary, max 60 chars.
        def _short_label(stmt: str) -> str:
            # CIS controls format: "Short Title: long description..."
            if ":" in stmt:
                label = stmt.split(":", 1)[0].strip()
                if label:
                    return label[:80]
            return stmt[:80].rstrip(".")

        # Pair each control_id with its short label (zip stops at shorter list)
        ctrl_labels = list(zip(cids, [_short_label(s) for s in stmt_list]))
        # Fall back to just the id if we have more IDs than statements
        for extra_cid in cids[len(ctrl_labels):]:
            ctrl_labels.append((extra_cid, extra_cid))

        # ── Evidence types ────────────────────────────────────────────────
        raw_ev = meta.get("evidence_types", "")
        ev_list = [e.strip() for e in raw_ev.split(",") if e.strip()] or [
            "policy_document", "implementation_record", "review_log"
        ]

        # ── Part 1 question ───────────────────────────────────────────────
        # Use first control label to name the technology/process
        first_label = ctrl_labels[0][1] if ctrl_labels else sub
        part1_q = {
            "question_id": generate_id(),
            "question_text": f"Is {first_label} formally established and in active use?",
            "question_type": "yes_no_with_detail",
            "response_options": "Yes / No / Partial",
            "detail_prompt": "If yes or partial, specify the tool or technology in use.",
        }

        # ── Per-control Part 2 questions ──────────────────────────────────
        _ctrl_templates = [
            # (question_type, template_fn, evidence_fn)
            ("YES_NO",
             lambda cid, lbl: f"Is {lbl} ({cid}) fully documented and reviewed on a regular schedule?",
             lambda ev: [ev[0]] if ev else ["policy_document"]),
            ("FREE_TEXT",
             lambda cid, lbl: f"Describe how {lbl} ({cid}) is operationally enforced. What tooling or process is in place?",
             lambda ev: ev[:2] if ev else ["implementation_record"]),
            ("SCALE_1_5",
             lambda cid, lbl: f"Rate the maturity of {lbl} ({cid}) on a scale of 1 (ad-hoc) to 5 (optimised).",
             lambda ev: [ev[-1]] if ev else ["maturity_evidence"]),
            ("FREE_TEXT",
             lambda cid, lbl: f"Who owns {lbl} ({cid}) and how are exceptions to it approved?",
             lambda ev: ["raci_matrix", "exception_log"]),
            ("FREE_TEXT",
             lambda cid, lbl: f"What evidence exists to demonstrate {lbl} ({cid}) was applied in the last 90 days?",
             lambda ev: ev[:2] if ev else ["audit_evidence"]),
        ]

        questions: List[Dict[str, Any]] = []
        for i, (ctrl_id, ctrl_lbl) in enumerate(ctrl_labels):
            tpl_idx = i % len(_ctrl_templates)
            qtype, q_fn, ev_fn = _ctrl_templates[tpl_idx]
            ev_items = ev_list[tpl_idx % max(len(ev_list), 1):]
            questions.append({
                "question_id": generate_id(),
                "text": q_fn(ctrl_id, ctrl_lbl),
                "question_type": qtype,
                "weight": 4 if qtype in ("YES_NO", "FREE_TEXT") else 3,
                "maturity_level": 3,
                "expected_evidence_types": ev_fn(ev_list),
            })

        # ── Pad to 10 with cross-cutting operational probes ───────────────
        _pad_probes = [
            (f"How frequently is {sub} tested or assessed, and who reviews the results?",
             "FREE_TEXT", ["review_record", "test_report"]),
            (f"What metrics or KPIs track the effectiveness of {sub}?",
             "FREE_TEXT", ["metrics_dashboard", "kpi_report"]),
            (f"How are gaps or failures in {sub} detected, escalated, and remediated?",
             "FREE_TEXT", ["incident_log", "gap_register"]),
            (f"What changes have been made to {sub} following the most recent audit or incident?",
             "FREE_TEXT", ["change_log", "remediation_record"]),
            ("Which statement best describes the current governance of this control area?",
             "MULTI_CHOICE", ["governance_doc"]),
        ]
        _pad_choices = [
            "Formally owned, documented, and regularly reviewed",
            "Informally managed with no documented owner",
            "Partially implemented with known gaps",
            "Not implemented",
        ]
        pad_idx = 0
        while len(questions) < 10:
            probe_text, probe_type, probe_ev = _pad_probes[pad_idx % len(_pad_probes)]
            q: Dict[str, Any] = {
                "question_id": generate_id(),
                "text": probe_text,
                "question_type": probe_type,
                "weight": 3,
                "maturity_level": 3,
                "expected_evidence_types": probe_ev,
            }
            if probe_type == "MULTI_CHOICE":
                q["choices"] = _pad_choices
            questions.append(q)
            pad_idx += 1

        questions = questions[:10]

        # ── Evidence to request: deduplicated union of all evidence types ─
        seen_ev: set = set()
        evidence_to_request: List[str] = []
        for q in questions:
            for ev in q.get("expected_evidence_types", []):
                if ev and ev not in seen_ev:
                    seen_ev.add(ev)
                    evidence_to_request.append(ev)

        return {
            "sub_topic": sub,
            "control_ids": cids,
            "part1_question": part1_q,
            "questions": questions,
            "maturity_guide": {
                "mature": f"{sub} controls are fully implemented, documented, and regularly reviewed.",
                "partial": f"Some {sub} controls are in place but coverage or review cadence has gaps.",
                "critical_gap": f"Key {sub} controls are absent or unenforced. Immediate remediation required.",
            },
            "evidence_to_request": evidence_to_request,
            "red_flags": [
                {
                    "critical_gap": f"{sub} is not implemented or not consistently enforced.",
                    "recommended_action": "Establish the control, assign ownership, and schedule a review cadence.",
                }
            ],
            "_stub": True,
        }

    # ── Private helpers (unchanged) ────────────────────────────────────────

    @staticmethod
    def _parse_json(raw: str, control_id: str) -> Dict[str, Any]:
        try:
            return parse_llm_json(raw)
        except (json.JSONDecodeError, ValueError, RuntimeError) as exc:
            logger.warning("JSON parse failed for %s (%s). Returning stub.", control_id, exc)
            return {
                "control_id":   control_id,
                "questions":    [],
                "raw_response": str(raw)[:500] if raw else "",
                "parse_error":  str(exc),
            }

    @staticmethod
    def _cross_reference_text(cross_references: Any) -> str:
        if isinstance(cross_references, str):
            return cross_references
        if not cross_references:
            return ""
        parts: List[str] = []
        for ref in cross_references:
            if isinstance(ref, dict):
                fw   = ref.get("other_framework_id", "")
                ctrl = ref.get("other_control_id", "")
                parts.append(f"{fw}:{ctrl}" if fw or ctrl else "")
            else:
                parts.append(str(ref))
        return ", ".join(p for p in parts if p)

    @staticmethod
    def _primary_nist_ref(control: Dict[str, Any]) -> str:
        refs = control.get("cross_references", []) or []
        for ref in refs:
            if isinstance(ref, dict):
                fw = str(ref.get("other_framework_id", "")).lower()
                if "800-53" in fw or "nist" in fw:
                    return str(ref.get("other_control_id", ""))
                continue
            if "800-53" in str(ref):
                ref_text = str(ref)
                return ref_text.split("NIST SP 800-53", 1)[-1].strip() or ref_text
        if refs:
            first = refs[0]
            return first.get("other_control_id", "") if isinstance(first, dict) else str(first)
        return ""

    @staticmethod
    def _domain_question_text(
        domain_name: str,
        domain_description: str,
        control_id: str,
        control_statement: str,
        question_type: str,
    ) -> str:
        base = f"{domain_name} domain control {control_id}: {control_statement}"
        if question_type == "yes_no":
            return f"Is the organization implementing and operating the {base.lower()} requirement as documented?"
        if question_type == "maturity_rating":
            return f"What maturity level best describes how the organization implements {base.lower()}?"
        if question_type == "evidence_upload":
            return f"Upload evidence that demonstrates the organization satisfies {base.lower()}."
        return (
            f"Describe how the organization implements {base.lower()} and how the {domain_name} domain"
            f" description applies: {domain_description}"
        )

    # ── Console print (unchanged) ──────────────────────────────────────────

    @staticmethod
    def print_questionnaire(q: Dict[str, Any]) -> None:
        """Pretty-print a single questionnaire to the console."""
        print(f"\n  ┌─ Framework: {q.get('name')} ({q.get('framework_id')}) {'─'*30}")
        for domain in q.get("Domains", []):
            print(f"  │ Domain: {domain.get('domain_name')} ({domain.get('domain_id')})")
            for control in domain.get("Controls", []):
                print(f"  │   Control: {control.get('control_id')}")
                print(f"  │   Statement: {control.get('control_statement','')[:80]}...")
                for i, question in enumerate(control.get("Questions", []), 1):
                    print(f"  │     Q{i}. [{question.get('question_type','?')}] {question.get('question_text','')[:100]}")
                    print(f"  │       Weight: {question.get('weight','')}")
            print(f"  │")
        print(f"  └{'─'*50}")