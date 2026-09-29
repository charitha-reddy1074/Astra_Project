"""Framework export and questionnaire assembly helpers.

This module centralises the logic for:
1. Loading NIST and CIS framework sources.
2. Enriching each control with two control-specific questions.
3. Saving individual framework JSON exports.
4. Building selection-specific questionnaire JSON files.

CHANGES (Market Assessment format):
  - _build_part1_checklist() now emits one row per CONTROL (not per domain
    group), using the yes/no+detail format from the Market Assessment doc.
    If a group was enriched with Market Assessment sub-topic sections the
    part1_question from each section is used directly; otherwise a compact
    question is derived from the control statement.
  - _enrich_group_for_part2() now:
      * Calls QuestionnaireBuilder.generate_market_assessment_for_domain()
        when a Groq API key is available, to produce LLM-generated
        sub-topic sections with proper maturity guides.
      * Falls back to the static template when the LLM is unavailable.
      * Stores sub-topic sections in group["sub_topics"] so the template
        can render them as named sub-sections.
  - _build_part3_tracker() now groups evidence by (domain, sub_topic)
    rather than (domain, control_id), matching the Market Assessment layout.
  - NEW _build_red_flags_by_domain() collects red_flags from sub-topic
    sections and groups them by domain for Part 2 rendering.
  - build_questionnaire_from_selection() includes the new
    "red_flags_by_domain" key in its return dict.
"""

from __future__ import annotations

import json
import os
import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
import re
import uuid
import time

import openpyxl
import requests
from requests.exceptions import RequestException
from pypdf import PdfReader

from backend.services.normalizer import DOMAIN_DESCRIPTIONS, FrameworkNormalizer

from backend.core.utils import load_json, logger, save_json, timestamp, LLMClient


import backend.config.settings  # noqa: F401  (loads backend/.env once)

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"

_LLM_CACHE_DIR = DATA_DIR / "llm_cache"


def _cache_key(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def _read_cache(key: str) -> Optional[str]:
    path = _LLM_CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None
    return None

def _write_cache(key: str, value: str) -> None:
    _LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (_LLM_CACHE_DIR / f"{key}.json").write_text(value, encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
FRAMEWORK_JSON_DIR = DATA_DIR / "frameworks" / "json"
OUTPUTS_DIR = DATA_DIR / "outputs"

NIST_EXPORT_PATH = FRAMEWORK_JSON_DIR / "nist_csf_2_0.json"
CIS_EXPORT_PATH = FRAMEWORK_JSON_DIR / "cis_controls_v8_1_2.json"
ISO_EXPORT_PATH = FRAMEWORK_JSON_DIR / "iso_27001_2022.json"
ISO_PDF_PATH = BASE_DIR / "data" / "frameworks" / "pdf" / "ISO_IEC-270012022-ed.3.pdf"
CIS_WORKBOOK_PATH = BASE_DIR / "data" / "frameworks" / "xlsx" / "CIS_Controls_Version_8.1.2___March_2025.xlsx"
CIS_NIST_MAPPING_PATH = BASE_DIR / "data" / "frameworks" / "xlsx" / "CIS_Controls_v8.1__Mapping_to_NIST_CSF_v2.0__6_24_2024_Final__1_.xlsx"
MARKET_ASSESSMENT_PATH = DATA_DIR / "outputs" / "canonical_frameworks" / "market_assessment_canonical.json"
MARKET_ASSESSMENT_EXPORT_PATH = FRAMEWORK_JSON_DIR / "market_assessment.json"
MARKET_ASSESSMENT_DOCX_PATH = DATA_DIR / "frameworks" / "docx" / "Market_Assessment_51.docx"
MATURITY_SCORING_PATH = OUTPUTS_DIR / "maturity_scoring_criteria.json"


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value))
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "questionnaire"


def _stable_uuid(*parts: str) -> str:
    seed = "|".join(str(part) for part in parts if part is not None)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


# ── Constants for LLM merge batching ──────────────────────────────────────────
_MERGE_BATCH_SIZE   = 8          # max items per LLM call
_MERGE_MAX_CHARS    = 28_000      # ~7 000 tokens — safe under Groq's limit
# Fields that carry large free-text but are NOT needed for semantic merge
# decisions.  Removing them shrinks each item by ~60 %.
_MERGE_STRIP_FIELDS = {
    "related_embedding_context",
    "source_questions",
    "control_statement",
}
def _slim_item(item: dict) -> dict:
    """Return a copy of *item* with heavy fields removed for the merge payload."""
    return {k: v for k, v in item.items() if k not in _MERGE_STRIP_FIELDS}

def _normalise_framework(framework: Dict[str, Any]) -> Dict[str, Any]:
    normalised = deepcopy(framework)
    normalised.setdefault("name", normalised.get("framework_name", ""))
    normalised.setdefault("framework_name", normalised.get("name", ""))
    normalised.setdefault("version", "")
    normalised.setdefault("source_format", "json")
    normalised.setdefault("ingested_at", timestamp())
    return normalised


def _framework_label(framework: Dict[str, Any]) -> str:
    return str(framework.get("framework_name") or framework.get("name") or "")


def _canonicalize_control(control: Dict[str, Any]) -> Dict[str, Any]:
    canonical = {
        "control_id": str(control.get("control_id", "")).strip(),
        "control_statement": str(control.get("control_statement", "")).strip(),
        "maturity_levels": list(control.get("maturity_levels", []) or []),
        "expected_evidence_types": list(control.get("expected_evidence_types", []) or []),
        "cross_references": list(control.get("cross_references", []) or []),
        "questions": list(control.get("questions", []) or []),
    }
    # Preserve verbatim maturity scoring criteria when present (e.g. the Market
    # Assessment) so exports remain scoreable against the source wording.
    if control.get("sub_topic"):
        canonical["sub_topic"] = str(control.get("sub_topic", "")).strip()
    if control.get("maturity_guide"):
        canonical["maturity_guide"] = dict(control.get("maturity_guide", {}) or {})
    return canonical


def _canonicalize_domain(domain: Dict[str, Any]) -> Dict[str, Any]:
    canonical = {
        "domain_id": str(domain.get("domain_id", "")).strip(),
        "domain_name": str(domain.get("domain_name", "")).strip(),
        "description": str(domain.get("description", "")).strip(),
        "controls": [_canonicalize_control(control) for control in domain.get("controls", []) or []],
    }
    if domain.get("red_flags"):
        canonical["red_flags"] = list(domain.get("red_flags", []) or [])
    return canonical


def canonicalize_framework(framework: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "framework_id": str(framework.get("framework_id", "")).strip(),
        "name": _framework_label(framework),
        "version": str(framework.get("version", "")).strip(),
        "source_format": str(framework.get("source_format", "")).strip(),
        "ingested_at": str(framework.get("ingested_at", timestamp())).strip(),
        "domains": [_canonicalize_domain(domain) for domain in framework.get("domains", []) or []],
    }


def _question_text(control_statement: str, question_type: str, evidence_types: Sequence[str]) -> str:
    statement = (control_statement or "").strip().rstrip(".")
    if question_type == "yes_no":
        return f"Is the organization implementing and operating the control: {statement}?"
    if question_type == "evidence_upload":
        if evidence_types:
            evidence_text = ", ".join(evidence_types)
            return f"Upload evidence such as {evidence_text} that demonstrates: {statement}."
        return f"Upload evidence that demonstrates the organization implements: {statement}."
    if question_type == "maturity_rating":
        return f"What maturity level best describes how consistently the organization implements: {statement}?"
    return f"Describe how the organization implements: {statement}."


def _clean_cis_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.replace("\u2019", "'").replace("\u2018", "'").replace("\u2013", "-").replace("\u2014", "-").replace("?", "'")


def _normalize_cis_id(value: Any) -> str:
    text = _clean_cis_text(value)
    match = re.match(r"^(\d+(?:\.\d+)?)", text)
    return match.group(1) if match else text


def _implementation_groups(*flags: Any) -> List[str]:
    groups: List[str] = []
    for index, flag in enumerate(flags, 1):
        if str(flag or "").strip().lower() == "x":
            groups.append(f"IG{index}")
    return groups


def _load_cis_nist_mapping(mapping_path: Path) -> Dict[str, List[Dict[str, str]]]:
    mapping_records = load_cis_nist_crosswalk_records(mapping_path)
    mapping: Dict[str, List[Dict[str, str]]] = {}
    for record in mapping_records:
        mapping.setdefault(record["safeguard_id"], []).append(
            {
                "other_framework_id": "nist-csf-2-0",
                "other_control_id": record["nist_subcategory"],
                "relationship": record["relationship"],
                "description": record["nist_description"],
            }
        )
    return mapping


def load_cis_nist_crosswalk_records(mapping_path: Path = CIS_NIST_MAPPING_PATH) -> List[Dict[str, str]]:
    if not mapping_path.exists():
        return []

    workbook = openpyxl.load_workbook(str(mapping_path), read_only=True, data_only=True)
    if "All CIS Controls & Safeguards" not in workbook.sheetnames:
        return []

    worksheet = workbook["All CIS Controls & Safeguards"]
    records: List[Dict[str, str]] = []

    for row in worksheet.iter_rows(min_row=2, values_only=True):
        values = list(row[:13]) if row else []
        while len(values) < 13:
            values.append(None)

        safeguard_id = _normalize_cis_id(values[2])
        relationship = _clean_cis_text(values[10])
        nist_subcategory = _clean_cis_text(values[11])
        nist_description = _clean_cis_text(values[12])

        if not safeguard_id or not nist_subcategory:
            continue

        records.append(
            {
                "safeguard_id": safeguard_id,
                "relationship": relationship,
                "nist_subcategory": nist_subcategory,
                "nist_description": nist_description,
            }
        )

    return records


def _question_type_for_control(control: Dict[str, Any], question_index: int) -> str:
    evidence_types = control.get("expected_evidence_types", []) or []
    if question_index == 0:
        return "yes_no"
    if evidence_types:
        return "evidence_upload"
    return "free_text"


def _format_answer_formats(question_type: str) -> List[str]:
    return [question_type] if question_type else ["free_text"]


def _build_merge_candidate(
    framework: Dict[str, Any],
    domain: Dict[str, Any],
    control: Dict[str, Any],
    question: Dict[str, Any],
) -> Dict[str, Any]:
    answer_formats = question.get("answer_formats") or _format_answer_formats(question.get("question_type", "free_text"))
    source_questions = question.get("source_questions") or [
        {
            "question_id": question.get("question_id", ""),
            "question_text": question.get("question_text", ""),
            "question_type": question.get("question_type", "free_text"),
            "framework_name": framework.get("framework_name", ""),
            "framework_key": framework.get("framework_key", ""),
            "domain_id": domain.get("domain_id", ""),
            "domain_name": domain.get("domain_name", ""),
            "control_id": control.get("control_id", ""),
            "cross_references": question.get("cross_references", []),
            "expected_evidence": question.get("expected_evidence", ""),
        }
    ]

    return {
        "item_id": question.get("item_id") or _stable_uuid(
            framework.get("framework_id", ""),
            domain.get("domain_id", ""),
            control.get("control_id", ""),
            question.get("question_id", ""),
        ),
        "question_id": question.get("question_id", ""),
        "question_text": question.get("question_text", ""),
        "question_type": question.get("question_type", "free_text"),
        "answer_formats": list(dict.fromkeys(answer_formats)),
        "framework_name": question.get("framework_name", framework.get("framework_name", "")),
        "framework_key": question.get("framework_key", framework.get("framework_key", "")),
        "domain_id": question.get("domain_id", domain.get("domain_id", "")),
        "domain_name": question.get("domain_name", domain.get("domain_name", "")),
        "control_id": question.get("control_id", control.get("control_id", "")),
        "control_statement": question.get("control_statement", control.get("control_statement", "")),
        "expected_evidence": question.get("expected_evidence", ""),
        "maturity_level": question.get("maturity_level", "Defined"),
        "cross_references": question.get("cross_references", []),
        "source_questions": source_questions,
        "source_item_ids": list(dict.fromkeys(question.get("source_item_ids") or [question.get("item_id") or question.get("question_id", "")])),
    }


MERGE_SYSTEM_PROMPT = """\
You merge audit questions from multiple control-based frameworks.

Rules:
1. Merge only questions that ask for the same underlying evidence, state, or implementation data.
2. If two questions are only loosely related, keep them separate.
3. When questions use different answer formats, preserve every distinct format in answer_formats.
4. Never drop a source item.
5. Return each source_item_id in exactly one merged group.
6. Keep the merged question specific, auditable, and concise.
7. Use related_embedding_context and cross_references to identify matching NIST/CIS controls, but do not merge unrelated controls just because they are in the same framework domain.

Output ONLY valid JSON with this exact structure:
{
  "merged_questions": [
    {
      "merged_question_text": "<single merged question>",
      "answer_formats": ["yes_no", "evidence_upload"],
      "source_item_ids": ["<id>", "<id>"],
      "merge_reason": "<why these items were merged>",
      "primary_question_type": "<yes_no | maturity_rating | free_text | evidence_upload | merged>"
    }
  ]
}

If a source item should remain alone, return it as a group with a single source_item_id.
"""


MERGE_HUMAN_PROMPT = """\
Merge these questionnaire items into non-overlapping groups.

Question items:
{{ items_json }}

Remember:
- Every source_item_id must appear exactly once across all groups.
- answer_formats must include every distinct question_type from the grouped items.
- The merged question must still let an auditor answer all required formats in one place.
 - Preserve original keywords and technical terms from source questions; do not paraphrase away
     any domain-specific keywords (e.g., 'authentication', 'MFA', 'asset inventory').
 - For each merged group return the frameworks, domains, and control ids represented.
 - Use the TryChroma retrieval context attached to each item as supporting evidence for whether NIST and CIS items share the same audit context.
"""


def _create_merge_chain(llm_api_key: str, model: str) -> Optional[LLMClient]:
    if not llm_api_key:
        return None
    return LLMClient(llm_api_key, model, temperature=0.2)


def _strip_merge_wrappers(text: str) -> str:
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        return match.group(0).strip()
    return text.strip()


def _parse_merge_groups(raw: str) -> List[Dict[str, Any]]:
    cleaned = _strip_merge_wrappers(raw)
    payload = json.loads(cleaned)

    if isinstance(payload, list):
        return [g for g in payload if isinstance(g, dict)]

    if isinstance(payload, dict):
        groups = payload.get("merged_questions")
        if isinstance(groups, list):
            return [g for g in groups if isinstance(g, dict)]
        return [payload]

    raise ValueError("LLM response did not contain a valid JSON object or list")


def _merge_question_group(items: List[Dict[str, Any]], group: Dict[str, Any]) -> Dict[str, Any]:
    source_ids = [str(item_id) for item_id in group.get("source_item_ids", []) if str(item_id).strip()]
    item_map = {str(item.get("item_id", "")): item for item in items}
    members = [item_map[item_id] for item_id in source_ids if item_id in item_map]
    if not members:
        members = items[:1]
        source_ids = [str(items[0].get("item_id", items[0].get("question_id", "")))]

    merged_source_questions: List[Dict[str, Any]] = []
    answer_formats: List[str] = []
    frameworks: List[str] = []
    framework_keys: List[str] = []
    domain_ids: List[str] = []
    domain_names: List[str] = []
    control_ids: List[str] = []
    control_statements: List[str] = []
    expected_evidence: List[str] = []
    maturity_levels: List[str] = []

    for item in members:
        answer_formats.extend(item.get("answer_formats", []) or [item.get("question_type", "free_text")])
        if item.get("framework_name"):
            frameworks.append(str(item["framework_name"]))
        if item.get("framework_key"):
            framework_keys.append(str(item["framework_key"]))
        if item.get("domain_id"):
            domain_ids.append(str(item["domain_id"]))
        if item.get("domain_name"):
            domain_names.append(str(item["domain_name"]))
        if item.get("control_id"):
            control_ids.append(str(item["control_id"]))
        if item.get("control_statement"):
            control_statements.append(str(item["control_statement"]))
        if item.get("expected_evidence"):
            expected_evidence.append(str(item["expected_evidence"]))
        if item.get("maturity_level"):
            maturity_levels.append(str(item["maturity_level"]))
        for src in item.get("source_questions", []) or []:
            merged_source_questions.append(src)

    cross_refs: List[Dict[str, Any]] = []
    seen_xrefs: set = set()
    for m in members:
        for xr in m.get("cross_references", []) or []:
            other_fw = str(xr.get("other_framework_id", "")).strip()
            other_ctrl = str(xr.get("other_control_id", "")).strip()
            key = (other_fw, other_ctrl)
            if key not in seen_xrefs and other_fw:
                seen_xrefs.add(key)
                cross_refs.append({"other_framework_id": other_fw, "other_control_id": other_ctrl})

    deduped_source_questions: List[Dict[str, Any]] = []
    seen_question_ids: set = set()
    for src in merged_source_questions:
        src_id = str(src.get("question_id", ""))
        if src_id and src_id not in seen_question_ids:
            seen_question_ids.add(src_id)
            deduped_source_questions.append(src)

    merged_type = str(group.get("primary_question_type") or "merged")
    merged_item_id = _stable_uuid(*sorted(source_ids))

    explicit = str(group.get("merged_question_text") or "").strip()
    if explicit:
        merged_question_text = explicit
    else:
        texts = [str(m.get("question_text", "") or "") for m in members]
        best_idx = 0
        best_score = -1.0
        for i, t in enumerate(texts):
            score = sum(_lexical_similarity(t, o) for j, o in enumerate(texts) if j != i)
            if score > best_score:
                best_score = score
                best_idx = i
        merged_question_text = texts[best_idx].strip()

    return {
        "item_id": merged_item_id,
        "question_id": merged_item_id,
        "question_text": merged_question_text,
        "question_type": merged_type,
        "answer_formats": list(dict.fromkeys(fmt for fmt in answer_formats if fmt)),
        "framework_name": ", ".join(dict.fromkeys(frameworks)),
        "framework_key": ", ".join(dict.fromkeys(framework_keys)),
        "domain_id": ", ".join(dict.fromkeys(domain_ids)),
        "domain_name": ", ".join(dict.fromkeys(domain_names)),
        "control_id": ", ".join(dict.fromkeys(control_ids)),
        "control_statement": " ".join(dict.fromkeys(control_statements)),
        "expected_evidence": ", ".join(dict.fromkeys(expected_evidence)),
        "maturity_level": next((level for level in maturity_levels if level), "Defined"),
        "merge_reason": group.get("merge_reason", ""),
        "source_item_ids": list(dict.fromkeys(source_ids)),
        "source_questions": deduped_source_questions,
        "cross_references": cross_refs,
    }


def _clean_group_label(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^Is the organization implementing and operating the control:\s*", "", cleaned)
    cleaned = re.sub(r"^Upload evidence such as .*? that demonstrates:\s*", "", cleaned)
    cleaned = cleaned.replace("Table A.1 (continued)Table A.1 (continued).", "").strip()
    return cleaned.rstrip(".?") or "Question group"


def _derive_group_id(item: Dict[str, Any]) -> str:
    source_questions = item.get("source_questions") or []
    source_ids = [str(src.get("question_id", "")).strip() for src in source_questions if str(src.get("question_id", "")).strip()]
    control_ids = [str(src.get("control_id", item.get("control_id", ""))).strip() for src in source_questions if str(src.get("control_id", item.get("control_id", ""))).strip()]
    if len(set(control_ids)) > 1:
        return f"grp-{slugify('-'.join(sorted(set(control_ids))))}"
    if source_ids:
        return f"grp-{slugify('-'.join(sorted(source_ids)))}"
    if item.get("control_id"):
        return f"grp-{slugify(str(item.get('framework_key', '')) + '-' + str(item.get('domain_id', '')) + '-' + str(item.get('control_id', '')))}"
    return f"grp-{slugify(str(item.get('question_id') or item.get('item_id') or 'question'))}"


def _derive_group_label(item: Dict[str, Any]) -> str:
    source_questions = item.get("source_questions") or []
    labels: List[str] = []
    if source_questions:
        for src in source_questions:
            text = src.get("question_text") or item.get("control_statement") or ""
            labels.append(_clean_group_label(text))
    else:
        labels.append(_clean_group_label(item.get("question_text") or item.get("control_statement") or ""))

    deduped = list(dict.fromkeys(label for label in labels if label))
    if not deduped:
        return "Question group"
    if len(deduped) == 1:
        return deduped[0]
    return " & ".join(deduped[:3])


def _expand_grouped_questions(items: List[Dict[str, Any]]) -> tuple:
    atomic_questions: List[Dict[str, Any]] = []
    grouped: Dict[str, Dict[str, Any]] = {}
    merged_control_summaries: List[Dict[str, Any]] = []

    for item in items:
        source_questions = item.get("source_questions") or []
        if source_questions:
            group_id = _derive_group_id(item)
            group_label = _derive_group_label(item)
            source_item_ids = list(dict.fromkeys(str(src.get("question_id", "")) for src in source_questions if str(src.get("question_id", "")).strip()))
            control_ids = list(dict.fromkeys(str(src.get("control_id", item.get("control_id", ""))) for src in source_questions if str(src.get("control_id", item.get("control_id", ""))).strip()))
            framework_keys = list(dict.fromkeys(str(src.get("framework_key", item.get("framework_key", ""))) for src in source_questions if str(src.get("framework_key", item.get("framework_key", ""))).strip()))
            for src in source_questions:
                atomic = {
                    **src,
                    "framework_name": src.get("framework_name", item.get("framework_name", "")),
                    "framework_key": src.get("framework_key", item.get("framework_key", "")),
                    "domain_id": src.get("domain_id", item.get("domain_id", "")),
                    "domain_name": src.get("domain_name", item.get("domain_name", "")),
                    "control_id": src.get("control_id", item.get("control_id", "")),
                    "expected_evidence": src.get("expected_evidence", item.get("expected_evidence", "")),
                    "question_type": src.get("question_type", item.get("question_type", "free_text")),
                }
                atomic_questions.append(atomic)
                grouped.setdefault(group_id, {
                    "group_id": group_id,
                    "group_label": group_label,
                    "question_count": 0,
                    "control_ids": [],
                    "framework_keys": [],
                    "questions": [],
                })
                group_entry = grouped[group_id]
                group_entry["questions"].append(atomic)
                group_entry["question_count"] += 1
                for value in control_ids:
                    if value and value not in group_entry["control_ids"]:
                        group_entry["control_ids"].append(value)
                for value in framework_keys:
                    if value and value not in group_entry["framework_keys"]:
                        group_entry["framework_keys"].append(value)

            if len(source_questions) > 1:
                merged_xrefs: List[Dict[str, Any]] = []
                seen_mx: set = set()
                for s in source_questions:
                    for xr in s.get("cross_references", []) or []:
                        ofw = str(xr.get("other_framework_id", "")).strip()
                        octrl = str(xr.get("other_control_id", "")).strip()
                        key = (ofw, octrl)
                        if key not in seen_mx and ofw:
                            seen_mx.add(key)
                            merged_xrefs.append({"other_framework_id": ofw, "other_control_id": octrl})

                merged_control_summaries.append({
                    "control_ids": control_ids,
                    "framework_keys": framework_keys,
                    "source_item_ids": source_item_ids,
                    "question_count": len(source_questions),
                    "merge_reason": item.get("merge_reason", ""),
                    "merged_question_text": item.get("question_text", ""),
                    "frameworks": list(dict.fromkeys(str(s.get("framework_name") or item.get("framework_name")) for s in source_questions)),
                    "domain_names": list(dict.fromkeys(str(s.get("domain_name") or item.get("domain_name")) for s in source_questions)),
                    "cross_references": merged_xrefs,
                })
        else:
            group_id = _derive_group_id(item)
            group_label = _derive_group_label(item)
            atomic = {
                **item,
                "group_id": group_id,
                "group_label": group_label,
                "group_size": 1,
                "group_control_ids": [str(item.get("control_id", ""))] if item.get("control_id") else [],
                "group_framework_keys": [str(item.get("framework_key", ""))] if item.get("framework_key") else [],
            }
            atomic_questions.append(atomic)
            grouped.setdefault(group_id, {
                "group_id": group_id,
                "group_label": group_label,
                "question_count": 0,
                "control_ids": [],
                "framework_keys": [],
                "questions": [],
            })
            group_entry = grouped[group_id]
            group_entry["questions"].append(atomic)
            group_entry["question_count"] += 1
            if item.get("control_id") and item.get("control_id") not in group_entry["control_ids"]:
                group_entry["control_ids"].append(str(item.get("control_id")))
            if item.get("framework_key") and item.get("framework_key") not in group_entry["framework_keys"]:
                group_entry["framework_keys"].append(str(item.get("framework_key")))

    question_groups = sorted(grouped.values(), key=lambda group: (group.get("group_label", ""), group.get("group_id", "")))
    return atomic_questions, question_groups, merged_control_summaries


def _lexical_similarity(left: str, right: str) -> float:
    a = set(re.findall(r"\w+", (left or "").lower()))
    b = set(re.findall(r"\w+", (right or "").lower()))
    if not a or not b:
        return 0.0
    inter = a.intersection(b)
    uni = a.union(b)
    return len(inter) / len(uni)


# Replace the entire _merge_question_items_with_llm function
def _merge_question_items_with_llm(
    items: List[Dict[str, Any]],
    llm_api_key: str,
    model: str = "openai/gpt-oss-120b",
) -> List[Dict[str, Any]]:
    """Merge audit question items with LLM, batching to avoid 413 errors."""
    import json, time, logging
    logger = logging.getLogger(__name__)

    if len(items) <= 1:
        return items

    chain = _create_merge_chain(llm_api_key, model)
    if chain is None:
        raise RuntimeError("GROQ_API_KEY is not set. LLM-based merging is required.")

    ordered_items = sorted(items, key=lambda item: (
        str(item.get("framework_name", "")),
        str(item.get("domain_id", "")),
        str(item.get("control_id", "")),
        str(item.get("question_type", "")),
        str(item.get("question_text", "")),
    ))

    # ── Build batches ─────────────────────────────────────────────────────
    slimmed = [_slim_item(i) for i in ordered_items]
    total_chars = len(json.dumps(slimmed))

    if total_chars <= _MERGE_MAX_CHARS and len(ordered_items) <= _MERGE_BATCH_SIZE:
        batches = [ordered_items]
    else:
        # Chunk into fixed-size batches
        batches = [
            ordered_items[start : start + _MERGE_BATCH_SIZE]
            for start in range(0, len(ordered_items), _MERGE_BATCH_SIZE)
        ]
        logger.info(
            "Merge payload too large (%d chars / %d items) — split into %d batches",
            total_chars, len(ordered_items), len(batches),
        )

    # ── Process batches ───────────────────────────────────────────────────
    all_merged: List[Dict[str, Any]] = []
    all_seen_ids: set = set()

    for batch_idx, batch in enumerate(batches):
        slim_batch = [_slim_item(i) for i in batch]
        items_json = json.dumps(slim_batch, indent=2)
        user_prompt = MERGE_HUMAN_PROMPT.replace("{{ items_json }}", items_json)

        try:
            raw = chain.invoke({
                "system_prompt": MERGE_SYSTEM_PROMPT,
                "user_prompt": user_prompt,
                "max_tokens": 4096,
            })
            groups = _parse_merge_groups(raw)
        except Exception as exc:
            logger.warning("LLM merge failed for batch %d/%d: %s", batch_idx + 1, len(batches), exc)
            # Fallback: merge by control only for this batch
            all_merged.extend(_merge_question_items_by_control(batch))
            all_seen_ids.update(
                str(i.get("item_id", i.get("question_id", ""))) for i in batch
            )
            if batch_idx < len(batches) - 1:
                time.sleep(2)
            continue

        for group in groups:
            merged_item = _merge_question_group(batch, group)
            all_merged.append(merged_item)
            all_seen_ids.update(merged_item.get("source_item_ids", []))

        # Carry forward any items the LLM forgot to include
        for item in batch:
            iid = str(item.get("item_id", item.get("question_id", "")))
            if iid not in all_seen_ids:
                all_merged.append(item)
                all_seen_ids.add(iid)

        # Throttle between batches to avoid 429s
        if batch_idx < len(batches) - 1:
            time.sleep(2)

    return all_merged


def _merge_question_items_by_control(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple, List[Dict[str, Any]]] = {}
    for item in items:
        key = (
            str(item.get("framework_key", "")),
            str(item.get("domain_id", "")),
            str(item.get("control_id", "")),
        )
        grouped.setdefault(key, []).append(item)

    merged_items: List[Dict[str, Any]] = []
    for key in sorted(grouped.keys()):
        group_items = grouped[key]
        source_ids = [str(item.get("item_id", item.get("question_id", ""))) for item in group_items if str(item.get("item_id", item.get("question_id", ""))).strip()]
        merged_items.append(
            _merge_question_group(
                group_items,
                {
                    "source_item_ids": source_ids,
                    "merge_reason": "Merged by control fallback because LLM output was unavailable or invalid.",
                    "primary_question_type": "merged",
                },
            )
        )

    return merged_items


def _read_pdf_text(pdf_path: Path, start_page: int = 16) -> str:
    reader = PdfReader(str(pdf_path))
    pages = reader.pages[start_page:]
    return "\n".join((page.extract_text() or "") for page in pages)


def _build_cis_domain(domain_id: str, domain_name: str, description: str) -> Dict[str, Any]:
    return {"domain_id": domain_id, "domain_name": domain_name, "description": description, "controls": []}


def _build_cis_framework() -> Dict[str, Any]:
    if not CIS_WORKBOOK_PATH.exists():
        raise FileNotFoundError(f"CIS workbook not found: {CIS_WORKBOOK_PATH}")

    workbook = openpyxl.load_workbook(str(CIS_WORKBOOK_PATH), read_only=True, data_only=True)
    if "Controls v8.1.2" not in workbook.sheetnames:
        raise ValueError("CIS workbook is missing the 'Controls v8.1.2' sheet")

    worksheet = workbook["Controls v8.1.2"]
    cis_nist_mapping = _load_cis_nist_mapping(CIS_NIST_MAPPING_PATH)
    domains: List[Dict[str, Any]] = []
    current_domain: Optional[Dict[str, Any]] = None

    for row in worksheet.iter_rows(min_row=2, values_only=True):
        values = list(row[:9]) if row else []
        while len(values) < 9:
            values.append(None)

        control_number = _normalize_cis_id(values[0])
        safeguard_number = _normalize_cis_id(values[1])
        asset_class = _clean_cis_text(values[2])
        security_function = _clean_cis_text(values[3])
        title = _clean_cis_text(values[4])
        description = _clean_cis_text(values[5])
        implementation_groups = _implementation_groups(values[6], values[7], values[8])

        if control_number and not safeguard_number and title and description:
            if current_domain:
                domains.append(current_domain)
            current_domain = _build_cis_domain(control_number, title, description)
            continue

        if not safeguard_number or not title or not description:
            continue

        if current_domain is None:
            current_domain = _build_cis_domain(control_number or safeguard_number.split(".", 1)[0], f"CIS Control {control_number or safeguard_number.split('.', 1)[0]}", "")

        current_domain.setdefault("controls", []).append(
            {
                "control_id": safeguard_number,
                "control_title": title,
                "control_statement": f"{title}: {description}" if title else description,
                "asset_class": asset_class,
                "security_function": security_function,
                "implementation_groups": implementation_groups,
                "maturity_levels": [],
                "expected_evidence_types": _heuristic_evidence_types(safeguard_number, title, description),
                "cross_references": cis_nist_mapping.get(safeguard_number, []),
                "questions": [],
            }
        )

    if current_domain:
        domains.append(current_domain)

    return {
        "framework_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "CIS Controls v8.1.2|2025")),
        "name": "CIS Controls v8.1.2",
        "framework_name": "CIS Controls v8.1.2",
        "version": "8.1.2",
        "source_format": "xlsx",
        "ingested_at": timestamp(),
        "publisher": "CIS",
        "source_document": CIS_WORKBOOK_PATH.name,
        "domains": domains,
    }


def _parse_iso_annex_a(pdf_path: Path) -> Dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    if len(reader.pages) < 24:
        raise ValueError("ISO PDF does not contain the expected Annex A page range")

    raw_text = "\n".join((reader.pages[i].extract_text() or "") for i in range(16, 24))
    raw_text = raw_text.replace("-\n", "")

    noise_prefixes = (
        "ISO/IEC", "Table A.1", "SNV / licensed", "All rights reserved", "Annex A",
        "(normative)", "Information security controls reference",
        "The information security controls listed in Table A.1 are directly derived",
        "Information security controls",
    )

    cleaned_lines: List[str] = []
    for raw_line in raw_text.splitlines():
        line = _clean_cis_text(raw_line)
        if not line:
            continue
        if any(line.startswith(prefix) for prefix in noise_prefixes):
            continue
        if re.fullmatch(r"\d{1,2}", line):
            continue
        cleaned_lines.append(line)

    domains: List[Dict[str, Any]] = []
    current_domain: Optional[Dict[str, Any]] = None
    current_control: Optional[Dict[str, Any]] = None

    def close_control() -> None:
        nonlocal current_control
        if not current_control or not current_domain:
            return
        parts = [part for part in current_control.pop("parts", []) if part]
        if not parts:
            current_control["control_statement"] = ""
        else:
            if "Control" in parts:
                control_idx = parts.index("Control")
                title_parts = parts[:control_idx]
                statement_parts = parts[control_idx + 1:]
            else:
                title_parts = parts[:1]
                statement_parts = parts[1:]
            title = " ".join(title_parts).strip()
            statement = " ".join(statement_parts).strip()
            if not statement and len(parts) > 1:
                statement = " ".join(parts[1:]).strip()
            current_control["control_statement"] = f"{title}: {statement}" if title and statement else (title or statement)

        current_control["maturity_levels"] = ["Initial", "Defined", "Managed"]
        current_control["expected_evidence_types"] = _heuristic_evidence_types(
            str(current_control.get("control_id", "")),
            str(current_control.get("control_id", "")),
            str(current_control.get("control_statement", "")),
        )
        current_control["cross_references"] = []
        current_domain.setdefault("controls", []).append(current_control)
        current_control = None

    for line in cleaned_lines:
        domain_match = re.match(r"^([5-8])\s+(.+?controls)$", line, flags=re.I)
        if domain_match:
            close_control()
            if current_domain:
                domains.append(current_domain)
            current_domain = {
                "domain_id": domain_match.group(1),
                "domain_name": domain_match.group(2).strip(),
                "controls": [],
            }
            continue

        control_match = re.match(r"^(\d+\.\d+)\s+(.*)$", line)
        if control_match:
            close_control()
            current_control = {
                "control_id": control_match.group(1),
                "parts": [control_match.group(2)],
                "maturity_levels": ["Initial", "Defined", "Managed"],
                "expected_evidence_types": [],
                "cross_references": [],
            }
            continue

        if current_control is not None:
            current_control.setdefault("parts", []).append(line)

    close_control()
    if current_domain:
        domains.append(current_domain)

    framework = {
        "framework_id": "iso-iec-27001-2022",
        "name": "ISO/IEC 27001:2022",
        "framework_name": "ISO/IEC 27001:2022",
        "version": "2022",
        "source_format": "pdf",
        "ingested_at": timestamp(),
        "publisher": "ISO",
        "source_document": pdf_path.name,
        "domains": domains,
    }

    for domain in framework["domains"]:
        if domain["domain_id"] == "5":
            domain["domain_name"] = "Organizational controls"
        elif domain["domain_id"] == "6":
            domain["domain_name"] = "People controls"
        elif domain["domain_id"] == "7":
            domain["domain_name"] = "Physical controls"
        elif domain["domain_id"] == "8":
            domain["domain_name"] = "Technological controls"
        for control in domain.get("controls", []):
            control["control_statement"] = re.sub(r"\s+", " ", control.get("control_statement", "")).strip()

    return framework


def load_iso_framework() -> Dict[str, Any]:
    if not ISO_PDF_PATH.exists():
        raise FileNotFoundError(f"ISO PDF not found: {ISO_PDF_PATH}")
    return _attach_questions(_parse_iso_annex_a(ISO_PDF_PATH))


def _heuristic_evidence_types(control_id: str, title: str, statement: str) -> List[str]:
    text = f"{title} {statement}".lower()
    evidence: List[str] = []

    def add(*items: str) -> None:
        for item in items:
            if item and item not in evidence:
                evidence.append(item)

    if any(k in text for k in ["policy", "procedure", "procedures"]):
        add("policy", "procedure", "approval_record")
    if any(k in text for k in ["roles", "responsibilities", "management", "authority"]):
        add("role_matrix", "org_chart", "responsibility_assignment")
    if any(k in text for k in ["contact", "authorities", "groups", "forums"]):
        add("contact_log", "membership_record", "communication_log")
    if any(k in text for k in ["threat", "intelligence"]):
        add("threat_report", "intel_feed", "analysis_note")
    if any(k in text for k in ["project", "development", "change", "software", "system"]):
        add("project_record", "change_ticket", "engineering_standard")
    if any(k in text for k in ["inventory", "asset", "assets"]):
        add("inventory", "asset_register", "owner_register")
    if any(k in text for k in ["acceptable use", "use of", "handling"]):
        add("acceptable_use_policy", "user_acknowledgement", "procedure")
    if any(k in text for k in ["return of assets", "return assets"]):
        add("offboarding_record", "asset_return_log")
    if any(k in text for k in ["classification", "labelling", "labeling"]):
        add("classification_standard", "labeling_procedure")
    if any(k in text for k in ["transfer", "transmission"]):
        add("transfer_agreement", "transfer_procedure")
    if any(k in text for k in ["access control", "access rights", "identity", "authentication", "privileged"]):
        add("access_policy", "access_review", "iam_log")
    if any(k in text for k in ["supplier", "cloud services", "ict supply chain"]):
        add("supplier_contract", "supplier_assessment", "review_report")
    if any(k in text for k in ["incident", "event", "evidence"]):
        add("incident_log", "response_procedure", "evidence_record")
    if any(k in text for k in ["business continuity", "disruption", "backup", "redundancy"]):
        add("backup_test", "continuity_plan", "recovery_test")
    if any(k in text for k in ["legal", "regulatory", "contractual", "privacy", "pii", "compliance"]):
        add("legal_register", "compliance_review", "privacy_record")
    if any(k in text for k in ["physical", "office", "premises", "equipment", "media", "cabling"]):
        add("facility_record", "access_log", "maintenance_log")
    if any(k in text for k in ["screening", "employment", "remote working", "awareness", "disciplinary"]):
        add("hr_record", "training_record", "signed_agreement")
    if any(k in text for k in ["logging", "monitoring", "network", "malware", "cryptography", "backup", "configuration"]):
        add("technical_control_record", "system_config", "monitoring_report")

    if not evidence:
        add("implementation_evidence", "operational_record")

    return evidence[:3]


def _control_questions(
    framework: Dict[str, Any],
    domain: Dict[str, Any],
    control: Dict[str, Any],
) -> List[Dict[str, Any]]:
    framework_id = framework.get("framework_id", "")
    domain_id = domain.get("domain_id", "")
    control_id = control.get("control_id", "")
    control_statement = control.get("control_statement", "")
    evidence_types = control.get("expected_evidence_types", []) or []

    questions: List[Dict[str, Any]] = []
    for question_index in range(2):
        question_type = _question_type_for_control(control, question_index)
        question_text = _question_text(control_statement, question_type, evidence_types)
        questions.append(
            {
                "question_id": _stable_uuid(framework_id, domain_id, control_id, str(question_index + 1), question_type),
                "question_text": question_text,
                "question_type": question_type,
                "weight": 5 if question_index == 0 else 4,
            }
        )

    return questions


def _attach_questions(framework: Dict[str, Any]) -> Dict[str, Any]:
    enriched = _normalise_framework(framework)
    for domain in enriched.get("domains", []):
        for control in domain.get("controls", []):
            control["questions"] = _control_questions(enriched, domain, control)
    return enriched


def load_nist_framework() -> Dict[str, Any]:
    if NIST_EXPORT_PATH.exists():
        raw = load_json(NIST_EXPORT_PATH)
        framework = FrameworkNormalizer().normalize(raw, "json")
    else:
        # Build from source and normalise
        raw = _build_nist_framework_from_source()   # your existing builder
        framework = FrameworkNormalizer().normalize(raw, "json")
    return _attach_questions(framework)

def load_cis_framework() -> Dict[str, Any]:
    framework = _build_cis_framework()
    return _attach_questions(framework)




def load_market_assessment_framework() -> Dict[str, Any]:
    """Load the Organizational Assessment framework from its canonical JSON file.

    When the source document is available, the verbatim per-sub-topic maturity
    assessment guides (Mature / Partial / Critical Gap) and red-flag tables are
    extracted and attached to each control/domain so the questionnaire can be
    scored against the real framework wording. If the document is missing, any
    guides already embedded in the canonical JSON are preserved as a fallback.
    """
    if not MARKET_ASSESSMENT_PATH.exists():
        raise FileNotFoundError(f"Market Assessment canonical JSON not found: {MARKET_ASSESSMENT_PATH}")
    framework = _normalise_framework(load_json(MARKET_ASSESSMENT_PATH))

    if MARKET_ASSESSMENT_DOCX_PATH.exists():
        try:
            from backend.ingestion.maturity_guides import (
                attach_maturity_guides_to_framework,
                extract_maturity_guides_from_docx,
            )

            guides = extract_maturity_guides_from_docx(MARKET_ASSESSMENT_DOCX_PATH)
            attach_maturity_guides_to_framework(framework, guides)
        except Exception as exc:
            logger.warning("Could not attach verbatim maturity guides: %s", exc)

    return framework

def save_framework_exports() -> List[Path]:
    FRAMEWORK_JSON_DIR.mkdir(parents=True, exist_ok=True)
    saved_paths: List[Path] = []

    nist_framework = canonicalize_framework(load_nist_framework())
    save_json(nist_framework, NIST_EXPORT_PATH)
    saved_paths.append(NIST_EXPORT_PATH)

    cis_framework = canonicalize_framework(load_cis_framework())
    save_json(cis_framework, CIS_EXPORT_PATH)
    saved_paths.append(CIS_EXPORT_PATH)

    if ISO_PDF_PATH.exists():
        iso_framework = canonicalize_framework(load_iso_framework())
        save_json(iso_framework, ISO_EXPORT_PATH)
        saved_paths.append(ISO_EXPORT_PATH)

    if MARKET_ASSESSMENT_PATH.exists():
        ma_framework = canonicalize_framework(load_market_assessment_framework())
        save_json(ma_framework, MARKET_ASSESSMENT_EXPORT_PATH)
        saved_paths.append(MARKET_ASSESSMENT_EXPORT_PATH)
        # Persist the embedded guides back to the canonical source and refresh
        # the verbatim maturity scoring criteria file for downstream consumers.
        try:
            save_json(ma_framework, MARKET_ASSESSMENT_PATH)
            regenerate_maturity_scoring_criteria(ma_framework)
            saved_paths.append(MATURITY_SCORING_PATH)
        except Exception as exc:
            logger.warning("Could not refresh maturity scoring criteria: %s", exc)

    logger.info("Saved %d framework export(s).", len(saved_paths))
    return saved_paths


def regenerate_maturity_scoring_criteria(framework: Optional[Dict[str, Any]] = None) -> Path:
    """Write data/outputs/maturity_scoring_criteria.json from verbatim guides.

    Each row preserves the Mature / Partial / Critical Gap wording extracted from
    the source document, keyed by the canonical domain. Consumers (rag_pipeline,
    web_ui) read this file to render real scoring criteria per cluster/domain.
    """
    from backend.ingestion.maturity_guides import build_criteria_entries

    if framework is None:
        framework = load_market_assessment_framework()

    entries = build_criteria_entries(framework)

    # Remap cluster_id to the market_domain_clusters taxonomy (matched by name)
    # so cluster-keyed consumers (rag_pipeline, web_ui) keep resolving criteria,
    # while domain_id stays the canonical domain id.
    cluster_by_name: Dict[str, str] = {}
    clusters_path = OUTPUTS_DIR / "market_domain_clusters.json"
    if clusters_path.exists():
        try:
            for cluster in load_json(clusters_path).get("clusters", []):
                name = re.sub(r"\s*\(.*?\)\s*$", "", str(cluster.get("cluster_name", ""))).strip().lower()
                if name:
                    cluster_by_name[name] = str(cluster.get("cluster_id", ""))
        except Exception as exc:
            logger.warning("Could not load cluster map for criteria remap: %s", exc)
    for entry in entries:
        mapped = cluster_by_name.get(str(entry.get("cluster_name", "")).strip().lower())
        if mapped:
            entry["cluster_id"] = mapped

    payload = {
        "_meta": {
            "document": "Market Assessment - Maturity Scoring Criteria",
            "description": (
                "One maturity scoring entry per domain sub-topic, extracted "
                "verbatim from the Market Assessment document. Columns preserve "
                "the Mature / Partial / Critical Gap structure."
            ),
            "source": MARKET_ASSESSMENT_DOCX_PATH.name,
            "column_order": ["mature", "partial", "critical_gap"],
            "entry_count": len(entries),
        },
        "criteria": entries,
    }
    save_json(payload, MATURITY_SCORING_PATH)
    logger.info("Wrote %d verbatim maturity scoring criteria → %s", len(entries), MATURITY_SCORING_PATH)
    return MATURITY_SCORING_PATH


def load_framework_catalog() -> List[Dict[str, Any]]:
    if not NIST_EXPORT_PATH.exists() or not CIS_EXPORT_PATH.exists() or (ISO_PDF_PATH.exists() and not ISO_EXPORT_PATH.exists()) or (MARKET_ASSESSMENT_PATH.exists() and not MARKET_ASSESSMENT_EXPORT_PATH.exists()):
        try:
            save_framework_exports()
        except Exception as exc:
            logger.warning("Framework export refresh failed: %s", exc)

    catalog: List[Dict[str, Any]] = []

    nist_framework = load_json(NIST_EXPORT_PATH) if NIST_EXPORT_PATH.exists() else load_nist_framework()
    catalog.append({
        "framework_id": nist_framework.get("framework_id", ""),
        "framework_key": "nist-csf-2-0",
        "framework_name": nist_framework.get("name", nist_framework.get("framework_name", "NIST CSF 2.0")),
        "domains": nist_framework.get("domains", []),
        "description": "Built-in NIST CSF 2.0 control catalogue.",
        "source": "file" if NIST_EXPORT_PATH.exists() else "built-in",
    })

    cis_framework = load_json(CIS_EXPORT_PATH) if CIS_EXPORT_PATH.exists() else load_cis_framework()
    catalog.append({
        "framework_id": cis_framework.get("framework_id", ""),
        "framework_key": "cis-controls-v8-1-2",
        "framework_name": cis_framework.get("name", cis_framework.get("framework_name", "CIS Controls v8.1.2")),
        "domains": cis_framework.get("domains", []),
        "description": "CIS Controls v8.1.2 loaded from data/frameworks/xlsx.",
        "source": "file",
    })

    if ISO_PDF_PATH.exists():
        iso_framework = load_json(ISO_EXPORT_PATH) if ISO_EXPORT_PATH.exists() else load_iso_framework()
        catalog.append({
            "framework_id": iso_framework.get("framework_id", ""),
            "framework_key": "iso-27001-2022",
            "framework_name": iso_framework.get("name", iso_framework.get("framework_name", "ISO/IEC 27001:2022")),
            "domains": iso_framework.get("domains", []),
            "description": "ISO/IEC 27001:2022 loaded from data/frameworks/pdf.",
            "source": "file" if ISO_EXPORT_PATH.exists() else "built-in",
        })


    if MARKET_ASSESSMENT_PATH.exists():
        ma_framework = load_json(MARKET_ASSESSMENT_EXPORT_PATH) if MARKET_ASSESSMENT_EXPORT_PATH.exists() else load_market_assessment_framework()
        catalog.append({
            "framework_id": ma_framework.get("framework_id", ""),
            "framework_key": "mcd-market-assessment",
            "framework_name": ma_framework.get("name", ma_framework.get("framework_name", "Organizational Assessment Framework")),
            "domains": ma_framework.get("domains", []),
            "description": "Organizational Assessment Framework loaded from data/outputs/canonical_frameworks.",
            "source": "file" if MARKET_ASSESSMENT_EXPORT_PATH.exists() else "built-in",
        })

    # ── Dynamic frameworks (ingested via CLI or web upload) ──────────────
    _dynamic_dir = DATA_DIR / "outputs" / "canonical_frameworks"
    _existing_names = {
        (fw.get("framework_name") or "").strip().lower()
        for fw in catalog
    }
    if _dynamic_dir.exists():
        for _dpath in sorted(_dynamic_dir.glob("framework_*.json")):
            try:
                _dfw = load_json(_dpath)
                _fw_name = (_dfw.get("framework_name") or _dfw.get("name") or "").strip()
                if not _fw_name or _fw_name.lower() in _existing_names:
                    continue
                _collection_name = _dpath.stem          # e.g. "framework_pci_dss_4_0"
                _fw_version = _dfw.get("version", "")

                # Older canonical JSONs store controls nested under sub_domains
                # rather than directly on the domain.  Flatten them so that
                # build_questionnaire_from_selection() can find the controls.
                _domains = _dfw.get("domains", [])
                for _domain in _domains:
                    if not _domain.get("controls") and _domain.get("sub_domains"):
                        _domain["controls"] = [
                            ctrl
                            for sd in _domain["sub_domains"]
                            for ctrl in sd.get("controls", [])
                        ]

                catalog.append({
                    "framework_id":   _dfw.get("framework_id", _collection_name),
                    "framework_key":  _collection_name,
                    "framework_name": _fw_name + (f" {_fw_version}" if _fw_version else ""),
                    "domains":        _domains,
                    "description":    f"Dynamically ingested framework ({_dpath.name}).",
                    "source":         "dynamic",
                })
                _existing_names.add(_fw_name.lower())
            except Exception as exc:
                logger.warning("Could not load dynamic framework %s: %s", _dpath.name, exc)

    # ── Apply user-ingested authoritative cross-framework mappings ────────
    try:
        from backend.ingestion.cross_mapping import apply_mappings_to_catalog
        apply_mappings_to_catalog(catalog)
    except Exception as exc:
        logger.warning("Could not apply ingested cross-mappings: %s", exc)

    return catalog


def _question_display_payload(
    framework: Dict[str, Any],
    domain: Dict[str, Any],
    control: Dict[str, Any],
    question: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "framework_id": framework.get("framework_id", ""),
        "framework_name": framework.get("framework_name", framework.get("name", "")),
        "domain_id": domain.get("domain_id", ""),
        "domain_name": domain.get("domain_name", ""),
        "control_id": control.get("control_id", ""),
        "control_statement": control.get("control_statement", ""),
        "question_id": question.get("question_id", ""),
        "question_text": question.get("question_text", ""),
        "question_type": question.get("question_type", "free_text"),
        "weight": question.get("weight", 3),
        "maturity_levels": control.get("maturity_levels", []),
        "expected_evidence_types": control.get("expected_evidence_types", []),
        "cross_references": control.get("cross_references", []),
    }


def _framework_alias(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "cis" in text:
        return "cis-controls-v8-1-2"
    if "nist" in text or "csf" in text:
        return "nist-csf-2-0"
    if "iso" in text or "27001" in text:
        return "iso-27001-2022"
    return text


def _control_key(framework_key: Any, control_id: Any) -> str:
    return f"{_framework_alias(framework_key)}:{str(control_id or '').strip().upper()}"


def _embedding_collections_for_selection(selected_framework_keys: Sequence[str]) -> List[tuple]:
    selected_aliases = {_framework_alias(key) for key in selected_framework_keys}
    collections: List[tuple] = []
    try:
        from backend.data_access.vectordb import framework_collection_name
    except Exception as exc:
        logger.warning("TryChroma collection lookup unavailable: %s", exc)
        return collections

    if "nist-csf-2-0" in selected_aliases:
        collections.append(("nist", framework_collection_name("nist")))
    if "cis-controls-v8-1-2" in selected_aliases:
        collections.append(("cis", framework_collection_name("cis")))
    if {"nist-csf-2-0", "cis-controls-v8-1-2"}.issubset(selected_aliases):
        collections.append(("nist_cis", framework_collection_name("nist_cis")))

    # Dynamic frameworks: framework_key IS the ChromaDB collection name (e.g. "framework_pci_dss_4_0")
    # "mcd-market-assessment" is a historical data key kept verbatim: it is written
    # into already-generated questionnaire banks and matches the framework row in
    # the database. Renaming it would orphan those artifacts.
    _static_keys = {"nist-csf-2-0", "cis-controls-v8-1-2", "iso-27001-2022", "mcd-market-assessment"}
    _added_collections = {col for _, col in collections}
    for key in selected_framework_keys:
        if key not in _static_keys and key.startswith("framework_") and key not in _added_collections:
            collections.append((key, key))
            _added_collections.add(key)

    return collections


def _retrieved_doc_payload(doc: Any, collection_alias: str) -> Dict[str, str]:
    metadata = getattr(doc, "metadata", {}) or {}
    return {
        "collection": collection_alias,
        "framework_key": _framework_alias(metadata.get("framework_name") or metadata.get("framework_id")),
        "framework_name": str(metadata.get("framework_name", "")),
        "domain_id": str(metadata.get("domain_id", "")),
        "domain_name": str(metadata.get("domain_name", "")),
        "control_id": str(metadata.get("control_id", "")),
        "control_statement": str(metadata.get("control_statement", ""))[:500],
    }


def _attach_trychroma_context(
    items: List[Dict[str, Any]],
    selected_framework_keys: Sequence[str],
) -> List[Dict[str, Any]]:
    collections = _embedding_collections_for_selection(selected_framework_keys)
    if not collections:
        return items

    try:
        from backend.data_access.vectordb import VectorDBManager
        vdb = VectorDBManager()
    except Exception as exc:
        logger.warning("TryChroma retrieval context unavailable: %s", exc)
        return items

    cache: Dict[tuple, List[Dict[str, str]]] = {}
    for item in items:
        query = " ".join(
            part for part in [
                str(item.get("framework_name", "")),
                str(item.get("domain_name", "")),
                str(item.get("control_id", "")),
                str(item.get("control_statement", "")),
            ] if part
        )
        if not query.strip():
            continue

        context: List[Dict[str, str]] = []
        seen_context_keys: set = set()
        for alias, collection_name in collections:
            cache_key = (collection_name, query)
            if cache_key not in cache:
                try:
                    docs = vdb.search_collection(collection_name, query=query, k=4)
                    cache[cache_key] = [_retrieved_doc_payload(doc, alias) for doc in docs]
                except Exception as exc:
                    logger.warning("TryChroma search failed for %s: %s", collection_name, exc)
                    cache[cache_key] = []

            for payload in cache[cache_key]:
                key = _control_key(payload.get("framework_key"), payload.get("control_id"))
                if key and key not in seen_context_keys:
                    seen_context_keys.add(key)
                    context.append(payload)

        if context:
            item["related_embedding_context"] = context

    return items


def _merge_components_from_context(items: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    control_to_items: Dict[str, List[Dict[str, Any]]] = {}
    selected_controls: set = set()
    for item in items:
        key = _control_key(item.get("framework_key"), item.get("control_id"))
        if key.endswith(":"):
            continue
        selected_controls.add(key)
        control_to_items.setdefault(key, []).append(item)

    edges: Dict[str, set] = {key: set() for key in selected_controls}
    for item in items:
        source_key = _control_key(item.get("framework_key"), item.get("control_id"))
        if source_key not in selected_controls:
            continue
        for ref in item.get("cross_references", []) or []:
            if not isinstance(ref, dict):
                continue
            target_key = _control_key(ref.get("other_framework_id"), ref.get("other_control_id"))
            if target_key in selected_controls:
                edges[source_key].add(target_key)
                edges[target_key].add(source_key)

    components: List[List[Dict[str, Any]]] = []
    visited: set = set()
    for control_key in sorted(selected_controls):
        if control_key in visited:
            continue
        stack = [control_key]
        component_keys: List[str] = []
        visited.add(control_key)
        while stack:
            current = stack.pop()
            component_keys.append(current)
            for neighbor in sorted(edges.get(current, set())):
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)

        component_items: List[Dict[str, Any]] = []
        for key in sorted(component_keys):
            component_items.extend(control_to_items.get(key, []))
        if component_items:
            components.append(component_items)

    return components or [items]


# ---------------------------------------------------------------------------
# Part 1 / 2 / 3 builders  (Market Assessment format)
# ---------------------------------------------------------------------------

_PART1_MAX_PER_DOMAIN = 5   # Part 1 pre-assessment questions per selected domain


def _p1_question_text(q: Dict[str, Any], domain_name: str) -> str:
    """Derive a Part 1 yes/no capability-check question from a merged or source question."""
    # 1. Best: yes_no source question already phrased as a yes/no
    for src in (q.get("source_questions") or []):
        if (src.get("question_type") or "").lower() in ("yes_no", "yes_no_with_detail"):
            text = (src.get("question_text") or "").strip()
            if text:
                return text

    # 2. The question itself is yes_no
    if (q.get("question_type") or "").lower() in ("yes_no", "yes_no_with_detail"):
        text = (q.get("question_text") or "").strip()
        if text:
            return text

    # 3. Rephrase from control_statement
    stmt = (q.get("control_statement") or "").strip().rstrip(".")
    if stmt:
        return f"Is {stmt[:120].lower()} implemented and operational?"

    # 4. Rephrase from question text
    text = (q.get("question_text") or "").strip()
    if text:
        low = text.lower()
        if low.startswith(("is ", "are ", "does ", "has ", "do ")):
            return text
        if low.startswith("upload"):
            remainder = re.sub(r"^upload[^:]*:\s*", "", text, flags=re.IGNORECASE).strip()
            return f"Is the following evidenced and operational: {remainder[:100]}?"
        cid = q.get("control_id", domain_name)
        return f"Is {cid} formally implemented and can supporting evidence be provided?"

    cid = q.get("control_id", "")
    return f"Is {cid or domain_name} implemented and operational?"


def _build_part1_checklist(grouped_questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Pre-assessment checklist: up to 5 yes/no + tool-selection questions per domain.

    Iterates group.questions directly (not sub_topics) so every unique control
    contributes a candidate question regardless of how many sub_topics the LLM
    generated. For each merged question the preferred text comes from the yes_no
    entry inside source_questions; if absent, text is derived from the control
    statement or question text. Questions are deduplicated by control_id and
    capped at _PART1_MAX_PER_DOMAIN per domain.
    """
    checklist: List[Dict[str, Any]] = []
    counter = 0

    for group in grouped_questions:
        domain_name = group.get("domain_name", "")
        framework_name = group.get("framework_name", "")
        domain_items: List[Dict[str, Any]] = []
        seen: set = set()

        for q in group.get("questions", []):
            if len(domain_items) >= _PART1_MAX_PER_DOMAIN:
                break

            # Deduplicate by control_id at the merged-question level so each
            # physical control contributes at most one Part 1 checklist item.
            ctrl_id = (q.get("control_id") or "").strip()
            dedup = ctrl_id or (q.get("question_text") or "")[:60]
            if dedup in seen:
                continue
            seen.add(dedup)

            # Find the best yes/no question text: prefer a yes_no source question,
            # then fall back to deriving text from the merged question itself.
            text = ""
            for src in (q.get("source_questions") or []):
                if (src.get("question_type") or "").lower() in ("yes_no", "yes_no_with_detail"):
                    text = (src.get("question_text") or "").strip()
                    if text:
                        break
            if not text:
                text = _p1_question_text(q, domain_name)
            if not text:
                continue

            domain_items.append({
                "question_id": _stable_uuid(framework_name, domain_name, ctrl_id, str(len(domain_items))),
                "question_text": text,
                "sub_topic": "",
                "domain_name": domain_name,
                "framework_name": framework_name,
                "control_id": ctrl_id,
                "answer_format": "yes_no_with_detail",
                "response_options": "Yes / No / Partial",
                "detail_prompt": "If yes or partial, specify the tool or technology in use.",
            })

        for item in domain_items:
            counter += 1
            item["number"] = counter
            checklist.append(item)

    return checklist


def _enrich_group_from_verbatim_guides(
    group: Dict[str, Any],
    control_maturity: List[Dict[str, Any]],
    llm_api_key: str = "",
    llm_model: str = "openai/gpt-oss-120b",
) -> Dict[str, Any]:
    """Build Part 2 sub-topic sections from verbatim per-control maturity guides.

    Each control carries its own sub-topic name and Mature/Partial/Critical Gap
    wording (extracted from the source framework document). The verbatim wording
    is ALWAYS preserved as the authoritative maturity scoring criteria.

    For the interview questions themselves, when a Groq API key is available the
    function asks the LLM to GENERATE diagnostic questions whose answers reveal
    where on the Mature/Partial/Critical-Gap ladder the organisation sits and
    which gap it has (see QuestionnaireBuilder.generate_diagnostic_questions_for_domain).
    The document's own questions are passed in only as style samples. When no key
    is available, or generation fails, the function falls back to reusing the
    document's verbatim questions.
    """
    questions = group.get("questions", [])
    domain_name = group.get("domain_name", "domain")
    domain_description = group.get("domain_description", "")
    framework_name = group.get("framework_name", "")

    # Map control_id -> merged questions for that control
    questions_by_control: Dict[str, List[Dict[str, Any]]] = {}
    for q in questions:
        questions_by_control.setdefault(str(q.get("control_id", "")), []).append(q)

    def _sample_texts(control_id: str) -> List[str]:
        texts: List[str] = []
        for q in questions_by_control.get(control_id, []):
            for src in (q.get("source_questions") or [q]):
                t = src.get("question_text") or q.get("question_text", "")
                if t and t not in texts:
                    texts.append(t)
        return texts

    def _verbatim_questions(control_id: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for q in questions_by_control.get(control_id, []):
            for src in (q.get("source_questions") or [q]):
                text = src.get("question_text") or q.get("question_text", "")
                if text:
                    out.append({
                        "question_id": src.get("question_id") or q.get("question_id", ""),
                        "question_text": text,
                        "text": text,
                        "question_type": (src.get("question_type") or "FREE_TEXT").upper(),
                        "control_id": control_id,
                        "weight": src.get("weight", q.get("weight", 3)),
                    })
        return out

    # ── Build the per-sub-topic rubric guides (with sample questions) ────────
    sub_topic_guides: List[Dict[str, Any]] = []
    for cm in control_maturity:
        guide = cm.get("maturity_guide") or {}
        if not any(guide.values()):
            continue
        cid = str(cm.get("control_id", ""))
        sub_topic_guides.append({
            "control_id": cid,
            "sub_topic": cm.get("sub_topic") or cid,
            "control_statement": cm.get("control_statement", ""),
            "maturity_guide": dict(guide),
            "expected_evidence_types": list(cm.get("expected_evidence_types", []) or []),
            "sample_questions": _sample_texts(cid),
        })

    # ── Generate diagnostic questions grouped by control_id (LLM, cached) ────
    diag_by_control: Dict[str, List[Dict[str, Any]]] = {}
    if llm_api_key and sub_topic_guides:
        try:
            from backend.services.questionnaire import QuestionnaireBuilder

            sorted_cids = sorted(g["control_id"] for g in sub_topic_guides)
            cache_key = _cache_key("diagnostic", framework_name, domain_name, str(sorted_cids))
            cached = _read_cache(cache_key)
            if cached:
                logger.info("Diagnostic cache hit for %s / %s", framework_name, domain_name)
                generated = json.loads(cached)
            else:
                builder = QuestionnaireBuilder(llm_api_key=llm_api_key, model=llm_model)
                generated = builder.generate_diagnostic_questions_for_domain(
                    domain_name=domain_name,
                    sub_topic_guides=sub_topic_guides,
                    domain_description=domain_description,
                    framework_name=framework_name,
                    count=10,
                )
                _write_cache(cache_key, json.dumps(generated))

            for gq in generated:
                diag_by_control.setdefault(str(gq.get("control_id", "")), []).append(gq)
        except Exception as exc:
            logger.warning(
                "Diagnostic question generation failed for %s / %s: %s — "
                "falling back to verbatim document questions.",
                framework_name, domain_name, exc,
            )
            diag_by_control = {}

    sub_topic_sections: List[Dict[str, Any]] = []
    all_evidence: List[str] = []
    seen_ev: set = set()
    mature_parts: List[str] = []
    partial_parts: List[str] = []
    gap_parts: List[str] = []

    for cm in control_maturity:
        guide = cm.get("maturity_guide") or {}
        if not any(guide.values()):
            continue
        control_id = str(cm.get("control_id", ""))
        sub_topic = cm.get("sub_topic") or control_id

        # Prefer generated diagnostic questions; fall back to verbatim document Qs
        interview_questions = diag_by_control.get(control_id) or _verbatim_questions(control_id)

        evidence = [ev for ev in cm.get("expected_evidence_types", []) if ev]
        for ev in evidence:
            if ev not in seen_ev:
                seen_ev.add(ev)
                all_evidence.append(ev)

        if guide.get("mature"):
            mature_parts.append(f"{sub_topic}: {guide['mature']}")
        if guide.get("partial"):
            partial_parts.append(f"{sub_topic}: {guide['partial']}")
        if guide.get("critical_gap"):
            gap_parts.append(f"{sub_topic}: {guide['critical_gap']}")

        sub_topic_sections.append({
            "sub_topic": sub_topic,
            "control_ids": [control_id],
            "questions": interview_questions,
            "interview_questions": interview_questions,
            "maturity_guide": {
                "mature": guide.get("mature", ""),
                "partial": guide.get("partial", ""),
                "critical_gap": guide.get("critical_gap", ""),
            },
            "evidence_to_request": evidence,
            "red_flags": [],
        })

    group["sub_topics"] = sub_topic_sections
    group["evidence_to_request"] = all_evidence
    group["red_flags"] = list(group.get("domain_red_flags", []) or [])
    group["maturity_guide"] = {
        "mature": " ".join(mature_parts),
        "partial": " ".join(partial_parts),
        "critical_gap": " ".join(gap_parts),
    }
    return group


def _enrich_group_for_part2(group: Dict[str, Any], llm_api_key: str = "", llm_model: str = "openai/gpt-oss-120b") -> Dict[str, Any]:
    """
    Add Part 2 sub-sections to a question_group.

    When a Groq API key is available the function calls
    QuestionnaireBuilder.generate_market_assessment_for_domain() to produce
    LLM-generated sub-topic sections. Each section contains:
      - interview_questions  : open-ended bullet questions (free_text style)
      - maturity_guide       : {mature, partial, critical_gap} table
      - evidence_to_request  : list of artefact names
      - red_flags            : [{critical_gap, recommended_action}]

    Falls back to the static template when no API key is available.

    The group dict is mutated in-place and returned.
    """
    domain_name = group.get("domain_name", "domain")
    framework_name = group.get("framework_name", "")
    questions = group.get("questions", [])

    # ── Verbatim maturity guides carried on the controls (e.g. the Market Assessment) ─────────
    # When the framework already provides per-sub-topic Mature/Partial/Critical
    # Gap wording, build the Part 2 sub-topic sections directly from it. This is
    # deterministic, needs no API key, and preserves the source framework text.
    control_maturity = group.get("_control_maturity") or []
    if any(any((cm.get("maturity_guide") or {}).values()) for cm in control_maturity):
        return _enrich_group_from_verbatim_guides(
            group, control_maturity, llm_api_key=llm_api_key, llm_model=llm_model
        )

    # ── Try LLM-based Market Assessment section generation ─────────────────
    if llm_api_key:
        try:
            from backend.services.questionnaire import QuestionnaireBuilder

            # Build a minimal framework-like dict from the group's questions
            # so QuestionnaireBuilder can iterate controls
            domain_controls = []
            seen_cids: set = set()
            for q in questions:
                cid = q.get("control_id", "")
                if cid and cid not in seen_cids:
                    seen_cids.add(cid)
                    domain_controls.append({
                        "control_id": cid,
                        "control_statement": q.get("control_statement", ""),
                        "maturity_levels": [q.get("maturity_level", "Defined")],
                        "expected_evidence_types": [
                            ev.strip()
                            for ev in str(q.get("expected_evidence") or "").split(",")
                            if ev.strip()
                        ],
                        "cross_references": q.get("cross_references", []),
                    })

            if domain_controls:
                # Sort control_ids for a consistent cache key
                sorted_control_ids = sorted([c["control_id"] for c in domain_controls])
                cache_key = _cache_key(framework_name, domain_name, str(sorted_control_ids))
                cached = _read_cache(cache_key)

                if cached:
                    logger.info("MA cache hit for %s / %s", framework_name, domain_name)
                    sub_topic_sections = json.loads(cached)
                else:
                    mini_framework = {
                        "framework_id": group.get("framework_id", ""),
                        "framework_name": framework_name,
                        "name": framework_name,
                        "version": "2.0",
                        "domains": [
                            {
                                "domain_id": group.get("domain_id", "GEN"),
                                "domain_name": domain_name,
                                "controls": domain_controls,
                            }
                        ],
                    }

                    builder = QuestionnaireBuilder(llm_api_key=llm_api_key, model=llm_model)
                    sub_topic_sections = builder.generate_market_assessment_for_domain(
                        mini_framework, domain_id=group.get("domain_id", "GEN")
                    )
                    _write_cache(cache_key, json.dumps(sub_topic_sections))

                # Always set sub_topics and initialise aggregation (both cache-hit and miss)
                group["sub_topics"] = sub_topic_sections
                all_evidence: List[str] = []
                all_red_flags: List[Dict[str, Any]] = []
                seen_ev: set = set()

                for section in sub_topic_sections:
                    # evidence_to_request is per-sub-topic — deduplicate across sub-topics
                    for ev in section.get("evidence_to_request", []):
                        if ev and ev not in seen_ev:
                            seen_ev.add(ev)
                            all_evidence.append(ev)
                    all_red_flags.extend(section.get("red_flags", []))

                # Build merged maturity guide from sub-topics
                all_mature = "; ".join(s.get("maturity_guide", {}).get("mature", "") for s in sub_topic_sections if s.get("maturity_guide", {}).get("mature"))
                all_partial = "; ".join(s.get("maturity_guide", {}).get("partial", "") for s in sub_topic_sections if s.get("maturity_guide", {}).get("partial"))
                all_gap = "; ".join(s.get("maturity_guide", {}).get("critical_gap", "") for s in sub_topic_sections if s.get("maturity_guide", {}).get("critical_gap"))

                group["evidence_to_request"] = all_evidence
                group["red_flags"] = all_red_flags
                group["maturity_guide"] = {
                    "mature": all_mature or f"{framework_name} {domain_name} controls are fully implemented.",
                    "partial": all_partial or f"Some {domain_name} controls are in place but gaps remain.",
                    "critical_gap": all_gap or f"Key {domain_name} controls are absent. Immediate remediation required.",
                }
                return group

        except Exception as exc:
            logger.warning("MA section LLM generation failed for group %s: %s", domain_name, exc)
            # Fall through to static template

    # ── Static fallback (no LLM) ───────────────────────────────────────────
    evidence_items: List[str] = []
    seen_ev: set = set()
    for q in questions:
        raw = q.get("expected_evidence") or q.get("expected_evidence_types") or ""
        parts = raw if isinstance(raw, list) else [p.strip() for p in str(raw).split(",") if p.strip()]
        for ev in parts:
            if ev and ev not in seen_ev:
                seen_ev.add(ev)
                evidence_items.append(ev)

    maturity_guide = {
        "mature": (
            f"{framework_name} {domain_name} controls are fully implemented, monitored, "
            "and continuously improved. All required evidence is available and current."
        ),
        "partial": (
            f"Some {domain_name} controls are in place but gaps remain. "
            "Evidence exists for key areas but coverage is incomplete or inconsistent."
        ),
        "critical_gap": (
            f"Key {domain_name} controls are absent or unenforced. "
            "Little or no evidence is available. Immediate remediation is required."
        ),
    }

    group["sub_topics"] = []
    group["evidence_to_request"] = evidence_items
    group["maturity_guide"] = maturity_guide
    group["red_flags"] = []
    return group


def _build_part3_tracker(grouped_questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Flat evidence tracking table across all domains for Part 3.

    Evidence rows are grouped by (domain, sub_topic) — matching the
    Market Assessment "Evidence to Request" layout — rather than by
    (domain, control_id).

    Each row:
      framework, domain, sub_topic, evidence_request, status (blank)
    """
    rows: List[Dict[str, Any]] = []
    seen: set = set()

    for group in grouped_questions:
        domain = group.get("domain_name", "")
        framework = group.get("framework_name", "")

        # ── Preferred: pull from LLM-generated sub-topic sections ─────────
        sub_topics = group.get("sub_topics", [])
        if sub_topics:
            for section in sub_topics:
                sub_topic = section.get("sub_topic", "")
                for ev in section.get("evidence_to_request", []):
                    if not ev:
                        continue
                    key = (framework, domain, sub_topic, ev)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append({
                        "framework": framework,
                        "domain": domain,
                        "sub_topic": sub_topic,
                        "control_id": ", ".join(
                            section.get("control_ids", [])
                            if isinstance(section.get("control_ids"), list)
                            else [str(section.get("control_ids", ""))]
                        ),
                        "evidence_request": ev,
                        "answer_format": "status_text",
                        "status": "",
                    })
            continue

        # ── Fallback: pull evidence from question fields ───────────────────
        for q in group.get("questions", []):
            control_id = q.get("control_id", "")
            raw = q.get("expected_evidence") or q.get("expected_evidence_types") or ""
            parts = raw if isinstance(raw, list) else [p.strip() for p in str(raw).split(",") if p.strip()]
            for ev in parts:
                if not ev:
                    continue
                key = (framework, domain, control_id, ev)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "framework": framework,
                    "domain": domain,
                    "sub_topic": "",
                    "control_id": control_id,
                    "evidence_request": ev,
                    "answer_format": "status_text",
                    "status": "",
                })

    return rows


def _build_red_flags_by_domain(grouped_questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collect red flags from each domain's sub-topic sections and return a
    list of domain-level red flag tables for Part 2 rendering.

    Format:
    [
      {
        "domain_name": "...",
        "framework_name": "...",
        "red_flags": [
          {"critical_gap": "...", "recommended_action": "..."},
          ...
        ]
      },
      ...
    ]
    """
    result: List[Dict[str, Any]] = []
    for group in grouped_questions:
        domain_red_flags: List[Dict[str, Any]] = []

        # From LLM-generated sub-topic sections
        for section in group.get("sub_topics", []):
            for rf in section.get("red_flags", []):
                if rf.get("critical_gap") or rf.get("recommended_action"):
                    domain_red_flags.append(rf)

        # From group-level red_flags (aggregated by _enrich_group_for_part2)
        for rf in group.get("red_flags", []):
            if rf not in domain_red_flags and (rf.get("critical_gap") or rf.get("recommended_action")):
                domain_red_flags.append(rf)

        if domain_red_flags:
            result.append({
                "domain_name": group.get("domain_name", ""),
                "framework_name": group.get("framework_name", ""),
                "red_flags": domain_red_flags,
            })

    return result


def _derive_domain_maturity_guide(group: Dict[str, Any]) -> Dict[str, str]:
    """Derive a per-domain maturity guide when the framework provides none.

    Produces Mature/Partial/Critical Gap wording grounded in the domain's own
    controls so questionnaires for NIST/CIS/ISO and dynamically-ingested
    frameworks remain scoreable even without verbatim source guides.
    """
    existing = group.get("maturity_guide") or {}
    if any(existing.values()):
        return {
            "mature": existing.get("mature", ""),
            "partial": existing.get("partial", ""),
            "critical_gap": existing.get("critical_gap", ""),
        }
    domain_name = group.get("domain_name", "the domain")
    return {
        "mature": (
            f"{domain_name} controls are fully defined, consistently implemented "
            "across the in-scope environment, monitored, evidenced, and reviewed "
            "for continuous improvement."
        ),
        "partial": (
            f"{domain_name} controls are partially implemented or inconsistently "
            "applied; ownership, coverage, automation, monitoring, or evidence is "
            "incomplete."
        ),
        "critical_gap": (
            f"{domain_name} controls are absent, informal, or not operating "
            "effectively, creating material security, compliance, or operational risk."
        ),
    }


def _build_maturity_scoring_criteria(grouped_questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten every domain/sub-topic's maturity guide into scoring criteria rows.

    One row per sub-topic when sub-topics exist (verbatim for frameworks that
    provide them, generated otherwise); falls back to one domain-level row.
    """
    rows: List[Dict[str, Any]] = []
    for group in grouped_questions:
        domain_id = group.get("domain_id", "")
        domain_name = group.get("domain_name", "")
        framework_name = group.get("framework_name", "")
        sub_topics = group.get("sub_topics") or []

        emitted = False
        for st in sub_topics:
            guide = st.get("maturity_guide") or {}
            if not any(guide.values()):
                continue
            control_ids = st.get("control_ids") or []
            if isinstance(control_ids, str):
                control_ids = [c.strip() for c in control_ids.split(",") if c.strip()]
            rows.append({
                "framework_name": framework_name,
                "domain_id": domain_id,
                "domain_name": domain_name,
                "sub_topic": st.get("sub_topic", ""),
                "control_ids": control_ids,
                "scoring_columns": {
                    "mature": guide.get("mature", ""),
                    "partial": guide.get("partial", ""),
                    "critical_gap": guide.get("critical_gap", ""),
                },
            })
            emitted = True

        if not emitted:
            rows.append({
                "framework_name": framework_name,
                "domain_id": domain_id,
                "domain_name": domain_name,
                "sub_topic": "",
                "control_ids": [],
                "scoring_columns": _derive_domain_maturity_guide(group),
            })

    return rows


def build_questionnaire_from_selection(
    frameworks: Sequence[Dict[str, Any]],
    selected_domain_keys: Sequence[str],
) -> Dict[str, Any]:
    framework_index = {framework.get("framework_key"): framework for framework in frameworks}
    grouped_questions: List[Dict[str, Any]] = []
    selected_questions: List[Dict[str, Any]] = []
    used_frameworks: List[Dict[str, Any]] = []
    used_framework_keys: set = set()
    selected_framework_keys: List[str] = []

    merge_api_key = os.getenv("GROQ_API_KEY", os.getenv("GROQ_KEY", "")).strip()
    merge_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b"

    for domain_key in selected_domain_keys:
        framework_key, _, domain_id = domain_key.partition(":")
        framework = framework_index.get(framework_key)
        if not framework or not domain_id:
            raise ValueError(f"Unknown framework/domain selection: {domain_key}")

        domain = next(
            (item for item in framework.get("domains", []) if str(item.get("domain_id", "")).upper() == domain_id.upper()),
            None,
        )
        if not domain:
            raise ValueError(f"Domain not found in framework: {domain_key}")

        selected_controls = list(domain.get("controls", []))
        domain_question_ids: List[str] = []
        for control in selected_controls:
            control_questions = control.get("questions") or _control_questions(framework, domain, control)
            for question in control_questions:
                payload = _question_display_payload(framework, domain, control, question)
                payload.update(
                    _build_merge_candidate(
                        framework, domain, control,
                        {
                            **question,
                            "framework_name": framework.get("framework_name", ""),
                            "framework_key": framework_key,
                            "domain_id": domain.get("domain_id", ""),
                            "domain_name": domain.get("domain_name", ""),
                            "control_id": control.get("control_id", ""),
                            "control_statement": control.get("control_statement", ""),
                            "cross_references": control.get("cross_references", []),
                            "expected_evidence": ", ".join(control.get("expected_evidence_types", []) or []),
                            "maturity_level": control.get("maturity_levels", ["Defined"])[0] if control.get("maturity_levels") else "Defined",
                        },
                    )
                )
                domain_question_ids.append(payload.get("item_id") or payload.get("question_id", ""))
                selected_questions.append(payload)

        # Collect any verbatim maturity scoring criteria carried on the controls
        # (e.g. the Organizational Assessment) so Part 2 enrichment can use the real
        # framework wording instead of generated/generic text.
        control_maturity: List[Dict[str, Any]] = []
        for control in selected_controls:
            guide = control.get("maturity_guide") or {}
            sub_topic = control.get("sub_topic", "")
            if sub_topic or any(guide.values()):
                control_maturity.append({
                    "control_id": control.get("control_id", ""),
                    "sub_topic": sub_topic,
                    "maturity_guide": dict(guide),
                    "control_statement": control.get("control_statement", ""),
                    "expected_evidence_types": list(control.get("expected_evidence_types", []) or []),
                })

        grouped_questions.append({
            "framework_id": framework.get("framework_id", ""),
            "framework_key": framework_key,
            "framework_name": framework.get("framework_name", framework_key),
            "domain_id": domain.get("domain_id", ""),
            "domain_name": domain.get("domain_name", domain_id),
            "domain_description": domain.get("description") or DOMAIN_DESCRIPTIONS.get(domain.get("domain_id", ""), ""),
            "source_question_ids": domain_question_ids,
            "_control_maturity": control_maturity,
            "domain_red_flags": list(domain.get("red_flags", []) or []),
        })

        if framework_key not in used_framework_keys:
            used_frameworks.append({
                "framework_id": framework.get("framework_id", ""),
                "framework_key": framework_key,
                "framework_name": framework.get("framework_name", framework_key),
            })
            used_framework_keys.add(framework_key)
            selected_framework_keys.append(framework_key)

    selected_questions = _attach_trychroma_context(selected_questions, selected_framework_keys)

    flattened_questions: List[Dict[str, Any]] = []
    merge_components = _merge_components_from_context(selected_questions)
    for component in merge_components:
        component_controls = {
            _control_key(item.get("framework_key"), item.get("control_id"))
            for item in component
        }
        if len(component_controls) <= 1:
            flattened_questions.extend(_merge_question_items_by_control(component))
        else:
            flattened_questions.extend(_merge_question_items_with_llm(component, merge_api_key, merge_model))

    for group in grouped_questions:
        source_ids = set(str(item_id) for item_id in group.pop("source_question_ids", []) if str(item_id).strip())
        domain_questions = [
            item for item in flattened_questions
            if source_ids.intersection(
                str(source_id) for source_id in item.get("source_item_ids", []) or [item.get("item_id") or item.get("question_id", "")]
            )
        ]
        group["question_count"] = len(domain_questions)
        group["questions"] = domain_questions

    # ── Enrich each group with Part 2 Market Assessment sub-sections ───────
    _ENRICH_INTER_DELAY  = 8.0   # seconds between domain-group enrichments
    _ENRICH_RATE_BACKOFF = 62.0  # seconds to wait on 429

    for enrich_idx, group in enumerate(grouped_questions):
        for attempt in range(2):
            try:
                _enrich_group_for_part2(group, llm_api_key=merge_api_key, llm_model=merge_model)
                break
            except Exception as exc:
                err_str = str(exc)
                if ("429" in err_str or "rate limit" in err_str.lower()) and attempt == 0:
                    logger.warning(
                        "Enrich 429 for group %s — waiting %.0fs",
                        group.get("domain_name", "?"), _ENRICH_RATE_BACKOFF,
                    )
                    time.sleep(_ENRICH_RATE_BACKOFF)
                    continue
                logger.warning("Enrich failed for group %s: %s", group.get("domain_name", "?"), exc)
                break

        # Throttle between groups (skip after last)
        if enrich_idx < len(grouped_questions) - 1:
            time.sleep(_ENRICH_INTER_DELAY)

    # ── Build the three structured parts ───────────────────────────────────
    # Part 1 checklist is built first — uses all sub-topics (capped at 5/domain inside)
    part1_checklist     = _build_part1_checklist(grouped_questions)
    part3_tracker       = _build_part3_tracker(grouped_questions)
    red_flags_by_domain = _build_red_flags_by_domain(grouped_questions)
    # Built from the FULL sub-topic list so every domain/sub-topic's maturity
    # scoring criteria is preserved even though Part 2 interview is capped below.
    maturity_scoring_criteria = _build_maturity_scoring_criteria(grouped_questions)

    # Cap Part 2 interview to ~10 questions per domain. The generic LLM path
    # puts all 10 in one sub-topic section (so one section is kept); the
    # diagnostic path spreads ~10 across per-control sub-topic sections, so
    # keep enough sections to reach the target rather than dropping to one.
    # Done AFTER Part 1/3 builders consume the full sub-topics list.
    _PART2_TARGET = 10
    for _g in grouped_questions:
        sections = _g.get("sub_topics") or []
        kept: List[Dict[str, Any]] = []
        running = 0
        for _st in sections:
            if running >= _PART2_TARGET:
                break
            kept.append(_st)
            running += len(_st.get("questions") or [])
        _g["sub_topics"] = kept
        _g.pop("_control_maturity", None)  # internal scratch — keep out of output

    framework_names: List[str] = []
    for fw in used_frameworks:
        name = fw["framework_name"]
        if name not in framework_names:
            framework_names.append(name)

    seen_names: List[str] = []
    for g in grouped_questions:
        label = f"{g.get('framework_name')} ({g.get('domain_name')})"
        if label not in seen_names:
            seen_names.append(label)
    questionnaire_name = " + ".join(seen_names) if seen_names else "Multi-framework questionnaire"

    raw_id = "multi-" + "-".join(sorted(selected_domain_keys))
    if len(selected_domain_keys) == 1:
        questionnaire_id = slugify(raw_id)
    else:
        questionnaire_id = f"multi-set-{str(uuid.uuid5(uuid.NAMESPACE_URL, raw_id))[:12]}"

    # Cap flat questions at 10 per domain so scored questions stay manageable
    _PART2_MAX_PER_DOMAIN = 10
    for group in grouped_questions:
        group["questions"] = group.get("questions", [])[:_PART2_MAX_PER_DOMAIN]

    capped_questions: List[Dict[str, Any]] = []
    for group in grouped_questions:
        capped_questions.extend(group.get("questions", []))
    flattened_questions = capped_questions

    atomic_questions, question_groups, merged_controls = _expand_grouped_questions(flattened_questions)

    return {
        "questionnaire_id": questionnaire_id,
        "questionnaire_type": "multi_framework",
        "framework_id": str(uuid.uuid5(uuid.NAMESPACE_URL, questionnaire_id)),
        "name": questionnaire_name,
        "framework_names": framework_names,
        "framework_summary": " + ".join(framework_names) if framework_names else "Multi-framework",
        "version": "1.0",
        "source_format": "multi_framework_merged",
        "question_count": len(atomic_questions),
        # ── Part 1: Pre-Assessment Checklist (one row per control/sub-topic) ──
        "part1_checklist": part1_checklist,
        # ── Part 2: Interview Questions by Domain (with sub-topics) ───────────
        # Each group in question_groups now carries:
        #   .sub_topics          – list of MA sub-topic sections (LLM-generated)
        #   .questions           – merged per-control questions with answer_formats
        #   .interview_questions – aggregated open-ended bullets
        #   .maturity_guide      – {mature, partial, critical_gap}
        #   .evidence_to_request – list of artefact names
        #   .red_flags           – [{critical_gap, recommended_action}]
        "question_groups": grouped_questions,
        # Flat list kept for backward compatibility
        "questions": atomic_questions,
        # ── Part 3: Evidence Tracker (grouped by domain + sub-topic) ──────────
        "part3_tracker": part3_tracker,
        # ── Red Flags & Remediation table per domain ───────────────────────────
        "red_flags_by_domain": red_flags_by_domain,
        # ── Maturity scoring criteria (per domain/sub-topic) for evaluation ─────
        # Verbatim from the framework where available, otherwise
        # derived per sub-topic/domain so every questionnaire is scoreable.
        "maturity_scoring_criteria": maturity_scoring_criteria,
        "selected_domain_keys": list(selected_domain_keys),
        "ingested_at": timestamp(),
        "maturity_levels": [
            {"level": 1, "code": "INITIAL",     "name": "Initial",     "description": "Ad hoc and undocumented."},
            {"level": 2, "code": "REPEATABLE",  "name": "Repeatable",  "description": "Basic processes established and repeated."},
            {"level": 3, "code": "DEFINED",     "name": "Defined",     "description": "Processes documented, standardised, and communicated."},
            {"level": 4, "code": "MANAGED",     "name": "Managed",     "description": "Processes measured and controlled with quantitative data."},
            {"level": 5, "code": "OPTIMIZING",  "name": "Optimizing",  "description": "Continuous improvement driven by metrics and feedback."},
        ],
        "domains": _build_domains_block(grouped_questions),
        "mappings": _build_mappings_block(grouped_questions),
    }


_MATURITY_STR_TO_INT: Dict[str, int] = {
    "Initial": 1, "Developing": 2, "Repeatable": 2,
    "Defined": 3, "Managed": 4, "Optimizing": 5,
}


def _maturity_to_int(value: Any) -> int:
    """Convert a maturity string or int to a 1–5 integer, defaulting to 3."""
    if isinstance(value, int) and 1 <= value <= 5:
        return value
    return _MATURITY_STR_TO_INT.get(str(value or "").strip(), 3)


def _build_questions_for_control(q: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Prefer LLM-generated questions from sub_topics when available (Change 11).
    Falls back to source_questions (static yes_no / evidence_upload).
    """
    # Collect LLM-generated questions from all sub_topics on the parent group.
    # These are stored one level up on the group, not on the flattened question item,
    # so we accept them pre-collected via q["_llm_questions"] set by _build_domains_block.
    llm_questions: List[Dict[str, Any]] = q.get("_llm_questions") or []
    source_questions: List[Dict[str, Any]] = q.get("source_questions") or [q]

    raw_list = llm_questions if llm_questions else source_questions

    result: List[Dict[str, Any]] = []
    for sq in raw_list:
        question: Dict[str, Any] = {
            "question_id": sq.get("question_id") or str(uuid.uuid4()),
            # Change 10: read both "text" (LLM schema) and "question_text" (legacy)
            "text": sq.get("text") or sq.get("question_text", ""),
            # Change 11: preserve UPPER type from LLM; normalise legacy lowercase
            "question_type": (sq.get("question_type") or "FREE_TEXT").upper(),
            "weight": int(sq.get("weight", 3)),
            # Change 4: maturity_level as integer
            "maturity_level": _maturity_to_int(sq.get("maturity_level")),
            # Change 5: help_text
            "help_text": sq.get("help_text", ""),
            # Change 6: choices (only populated for MULTI_CHOICE but always present)
            "choices": sq.get("choices", []),
        }
        # Change 7: applicability — only emit when truthy
        if sq.get("applicability"):
            question["applicability"] = sq["applicability"]
        # Diagnostic signals: per-question maturity rubric + the gap a weak
        # answer reveals. Preserved so the report can classify which maturity
        # category the response falls in and which gap it exposes.
        if sq.get("maturity_signals"):
            question["maturity_signals"] = sq["maturity_signals"]
        if sq.get("gap_if_deficient"):
            question["gap_if_deficient"] = sq["gap_if_deficient"]
        if sq.get("sub_topic"):
            question["sub_topic"] = sq["sub_topic"]
        if sq.get("control_id"):
            question["control_id"] = sq["control_id"]
        if sq.get("expected_evidence_types"):
            question["expected_evidence_types"] = sq["expected_evidence_types"]
        result.append(question)
    return result


def _build_domains_block(grouped_questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build the top-level 'domains' array matching the sample schema.

    Structure: domains → categories → controls → questions

    Each control gets only the questions from its own sub-topic section,
    NOT all questions from all sub-topics in the domain.
    """
    domains: List[Dict[str, Any]] = []

    for group in grouped_questions:
        # Build a mapping from control_id → LLM questions for that specific sub-topic.
        # Each sub-topic section covers a set of control_ids; its questions apply
        # only to those controls, not to every control in the domain.
        control_to_llm_questions: Dict[str, List[Dict[str, Any]]] = {}
        for st in group.get("sub_topics") or []:
            st_questions = st.get("questions") or []
            if not st_questions:
                continue
            st_control_ids = st.get("control_ids") or []
            # Normalise: control_ids may be a comma-separated string or a list
            if isinstance(st_control_ids, str):
                st_control_ids = [c.strip() for c in st_control_ids.split(",") if c.strip()]
            for cid in st_control_ids:
                control_to_llm_questions[cid] = st_questions

        # Group controls by category prefix (e.g. "ID.AM-1", "ID.AM-2" → category "ID.AM")
        category_map: Dict[str, List[Dict[str, Any]]] = {}
        for q in group.get("questions", []):
            control_id = q.get("control_id", "")
            match = re.match(r"^(.+)-\d+$", control_id)
            category_key = match.group(1) if match else control_id
            category_map.setdefault(category_key, []).append(q)

        categories: List[Dict[str, Any]] = []
        for category_key, controls in category_map.items():
            built_controls: List[Dict[str, Any]] = []
            for q in controls:
                control_id = q.get("control_id", "")
                q_copy = dict(q)
                # Assign only this control's sub-topic questions, or fall back to empty
                q_copy["_llm_questions"] = control_to_llm_questions.get(control_id, [])

                control: Dict[str, Any] = {
                    "code": control_id,
                    "statement": q.get("control_statement", ""),
                    "cross_refs": [
                        {
                            "framework": xr.get("framework") or xr.get("other_framework_id", ""),
                            "version": xr.get("version", ""),
                            "code": xr.get("code") or xr.get("other_control_id", ""),
                        }
                        for xr in (q.get("cross_references") or [])
                    ],
                    "questions": _build_questions_for_control(q_copy),
                }
                built_controls.append(control)

            categories.append({
                "code": category_key,
                "name": category_key,
                "controls": built_controls,
            })

        domains.append({
            "code": group.get("domain_id", ""),
            "name": group.get("domain_name", ""),
            "categories": categories,
        })

    return domains


def _build_mappings_block(grouped_questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Change 9: Build a flat mappings array from all cross-references across every control.
    """
    mappings: List[Dict[str, Any]] = []
    for group in grouped_questions:
        for q in group.get("questions", []):
            control_code = q.get("control_id", "")
            for xr in (q.get("cross_references") or []):
                framework = xr.get("framework") or xr.get("other_framework_id", "")
                version = xr.get("version", "")
                code = xr.get("code") or xr.get("other_control_id", "")
                entry: Dict[str, Any] = {
                    "source_control":           control_code,
                    "target_framework":         framework,
                    "target_framework_version": version,
                    "target_control":           code,
                    "relationship":             xr.get("relationship", "RELATED"),
                    "notes":                    xr.get("notes", ""),
                }
                confidence = xr.get("confidence")
                if confidence is not None:
                    entry["confidence"] = confidence
                mappings.append(entry)
    return mappings


def save_questionnaire(questionnaire: Dict[str, Any]) -> Path:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    base_id = questionnaire.get("questionnaire_id") or questionnaire.get("framework_id") or "unknown"
    output_path = OUTPUTS_DIR / f"questionnaire_bank_{base_id}.json"
    save_json(questionnaire, output_path)
    return output_path

