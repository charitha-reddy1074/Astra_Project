"""Robust PDF framework ingestion (root backend).

The generic LLM-batch extractor (``ingestion.extractor``) drops controls on
dense multi-column standards like PCI DSS, because ``extract_text`` interleaves
the table columns and the LLM silently omits sub-requirements. This module
guarantees COMPLETENESS by extracting control IDs + statements deterministically
(isolating the left "requirements" column of each page), and then uses the LLM
only for the quality step it is good at: writing ONE assessment question per
control. The canonical JSON it produces therefore contains every control, each
with exactly one LLM-generated question.

Flow:  PDF → column-aware (id, statement) extraction  → LLM 1-question-per-control
            → Framework dict (with questions) → chunk + embed → canonical JSON.

It is tuned for tabular, hierarchically-numbered requirement frameworks
(PCI DSS, and similar). For documents it cannot segment (too few controls
found) the caller should fall back to ``ingestion.dynamic_pipeline.ingest_file``.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.core.utils import LLMClient, logger, parse_llm_json, save_json

# A control ID at the start of a line: "7.1", "7.1.1", "12.10.1" (1-2 digit top level).
_ID_RE = re.compile(r"^(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)\s+(.*)$")
# Section header carrying the requirement (domain) title.
_REQ_HDR_RE = re.compile(r"Requirement\s+(\d{1,2}):\s+(.{6,110})")
# Lines that mark the END of a requirement statement (start of another cell/section).
_STOP_PREFIXES = (
    "Customized Approach", "Defined Approach", "Applicability Notes",
    "Payment Card Industry", "Requirements and Testing", "Note:", "Note ",
    "©2006", "(c)2006",
)
_CANONICAL_DIR = Path(__file__).resolve().parents[2] / "data" / "outputs" / "canonical_frameworks"


def _clean(text: str) -> str:
    """Normalise bullet glyphs and whitespace produced by PDF extraction."""
    text = re.sub(r"[•●�·]", "•", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _slug(name: str, version: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", f"{name} {version}".lower()).strip("_")
    return f"framework_{base}" if base else f"framework_{uuid.uuid4().hex[:8]}"


def _requirement_titles(pdf) -> Dict[str, str]:
    """Best-effort map of requirement-number → human title from full-page text.

    Prefers the first clean section-header occurrence (title not starting with a
    digit), which avoids picking up applicability notes that also say
    'Requirement N:'.
    """
    titles: Dict[str, str] = {}
    for pg in pdf.pages:
        txt = pg.extract_text() or ""
        for m in _REQ_HDR_RE.finditer(txt):
            n, t = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
            if n in titles:
                continue
            if t and not t[0].isdigit() and t[0].isupper():
                titles[n] = t
    return titles


def _column_boundary(page) -> float:
    """Right edge x of the left 'requirements' column.

    Detected from the x-position of the 'Testing' header word (in 'Defined
    Approach Testing Procedures'); falls back to 42% of page width.
    """
    try:
        xs = [w["x0"] for w in page.extract_words() if w.get("text") == "Testing"]
        if xs:
            return min(xs)
    except Exception:
        pass
    return page.width * 0.42


def _extract_controls(pdf) -> List[Tuple[str, str]]:
    """Ordered list of (control_id, statement) from the left requirements column."""
    controls: Dict[str, str] = {}
    order: List[str] = []
    for pg in pdf.pages:
        boundary = _column_boundary(pg)
        try:
            txt = pg.crop((0, 0, boundary, pg.height)).extract_text() or ""
        except Exception:
            txt = pg.extract_text() or ""
        cur: Optional[str] = None
        for raw in txt.splitlines():
            ln = raw.strip()
            if not ln:
                continue
            m = _ID_RE.match(ln)
            if m:
                cid = m.group(1)
                top = int(cid.split(".")[0])
                if not (1 <= top <= 12):       # PCI has 12 requirements; ignore page numbers etc.
                    cur = None
                    continue
                rest = m.group(2).strip()
                if cid not in controls:
                    controls[cid] = ""
                    order.append(cid)
                if rest.lower() != "(continued)" and rest:
                    controls[cid] = (controls[cid] + " " + rest).strip()
                cur = cid
                continue
            if ln.startswith(_STOP_PREFIXES):
                cur = None
                continue
            if cur and ln.lower() != "(continued)":
                controls[cur] = (controls[cur] + " " + ln).strip()
    return [(cid, _clean(controls[cid])) for cid in order if controls[cid].strip()]


def extract_framework(pdf_path: str | Path, framework_name: str, framework_version: str) -> Dict[str, Any]:
    """Deterministically extract a complete Framework dict (controls, no questions yet)."""
    import pdfplumber

    pdf_path = Path(pdf_path)
    with pdfplumber.open(str(pdf_path)) as pdf:
        titles = _requirement_titles(pdf)
        pairs = _extract_controls(pdf)

    domains: Dict[str, Dict[str, Any]] = {}
    domain_order: List[str] = []
    for cid, statement in pairs:
        top = cid.split(".")[0]
        did = f"REQ-{top}"
        if did not in domains:
            domains[did] = {
                "domain_id": did,
                "domain_name": titles.get(top, f"Requirement {top}"),
                "controls": [],
            }
            domain_order.append(did)
        domains[did]["controls"].append({
            "control_id": cid,
            "control_statement": statement,
            "maturity_levels": [],
            "expected_evidence_types": [],
            "cross_references": [],
        })

    n_controls = sum(len(d["controls"]) for d in domains.values())
    logger.info("Robust extractor: %d controls across %d domains from %s",
                n_controls, len(domain_order), pdf_path.name)
    return {
        "framework_id": _slug(framework_name, framework_version),
        "framework_name": framework_name,
        "version": framework_version,
        "source_format": "pdf",
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "domains": [domains[d] for d in domain_order],
    }


# ── LLM: one question per control ────────────────────────────────────────────

_Q_SYSTEM = (
    "You are a cybersecurity compliance auditor. For EACH control given, write "
    "EXACTLY ONE clear assessment question that an auditor would ask to verify "
    "whether the organisation satisfies that control. Return STRICT JSON only:\n"
    '{"questions":[{"control_id":"<id>","question_text":"<one question>",'
    '"question_type":"YES_NO","weight":3}]}\n'
    "Rules: one object per control_id, preserve the given control_id exactly. "
    "question_type must be one of YES_NO, SCALE_1_5, MULTI_CHOICE (default YES_NO). "
    "weight is 1-5 by importance. No commentary outside the JSON."
)


def _gen_questions_batch(client: LLMClient, batch: List[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
    lines = [f'{c["control_id"]}: {c["statement"][:400]}' for c in batch]
    user = "Controls:\n" + "\n".join(lines)
    raw = client.invoke({"system_prompt": _Q_SYSTEM, "user_prompt": user, "max_tokens": 3000})
    data = parse_llm_json(raw)
    out: Dict[str, Dict[str, Any]] = {}
    for q in data.get("questions", []):
        cid = str(q.get("control_id", "")).strip()
        if cid:
            out[cid] = q
    return out


def generate_questions(
    framework: Dict[str, Any],
    api_key: str,
    model: str = "llama-3.3-70b-versatile",
    batch_size: int = 15,
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """Attach exactly ONE LLM-generated question to every control, in place.

    Falls back to a deterministic question per control if the LLM is unavailable
    or omits one, so every control always ends up with exactly one question.
    """
    flat: List[Dict[str, str]] = []
    for d in framework["domains"]:
        for c in d["controls"]:
            flat.append({"control_id": c["control_id"], "statement": c.get("control_statement", "")})

    generated: Dict[str, Dict[str, Any]] = {}
    client = LLMClient(api_key, model) if api_key else None
    total_batches = max(1, (len(flat) + batch_size - 1) // batch_size)
    for bi in range(total_batches):
        batch = flat[bi * batch_size:(bi + 1) * batch_size]
        if client:
            try:
                generated.update(_gen_questions_batch(client, batch))
            except Exception as exc:
                logger.warning("Question batch %d/%d failed (%s); using fallback.",
                               bi + 1, total_batches, exc)
        if progress_cb:
            progress_cb(f"Generating questions ({bi + 1}/{total_batches})",
                        int((bi + 1) / total_batches * 100))

    for d in framework["domains"]:
        for c in d["controls"]:
            cid = c["control_id"]
            g = generated.get(cid)
            stmt = c.get("control_statement", "")
            qtext = (g or {}).get("question_text") or (
                f"Has the organisation implemented the requirement: "
                f"{stmt[:180]}{'...' if len(stmt) > 180 else ''}"
            )
            qtype = (g or {}).get("question_type", "YES_NO")
            if qtype not in ("YES_NO", "SCALE_1_5", "MULTI_CHOICE"):
                qtype = "YES_NO"
            try:
                weight = int((g or {}).get("weight", 3))
            except (TypeError, ValueError):
                weight = 3
            c["questions"] = [{
                "question_id": str(uuid.uuid4()),
                "question_text": qtext,
                "question_type": qtype,
                "weight": max(1, min(weight, 5)),
            }]
    return framework


# ── End-to-end ingest (extract → questions → embed → canonical) ──────────────

def ingest_pdf_framework(
    pdf_path: str | Path,
    framework_name: str,
    framework_version: str,
    generate_q: bool = True,
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """Robustly ingest a (PCI-style) framework PDF.

    Extracts every control deterministically, generates exactly one question per
    control (LLM when GROQ_API_KEY is valid, deterministic fallback otherwise),
    embeds controls into the LOCAL Chroma store, and writes the canonical JSON
    (with questions) to data/outputs/canonical_frameworks/. Returns a summary.
    """
    import os

    from backend.ingestion.collection_utils import framework_to_collection_name

    def _p(msg: str, pct: int) -> None:
        logger.info("[%3d%%] %s", pct, msg)
        if progress_cb:
            progress_cb(msg, pct)

    _p("Extracting controls (column-aware)…", 10)
    framework = extract_framework(pdf_path, framework_name, framework_version)
    n_controls = sum(len(d["controls"]) for d in framework["domains"])
    n_domains = len(framework["domains"])
    _p(f"Extracted {n_controls} controls across {n_domains} domains", 40)

    if generate_q:
        _p("Generating one question per control…", 45)
        generate_questions(
            framework,
            os.getenv("GROQ_API_KEY", ""),
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            progress_cb=lambda m, p: _p(m, 45 + int(p * 0.30)),
        )

    collection_name = framework_to_collection_name(framework_name, framework_version)

    _p("Chunking + embedding into local Chroma…", 80)
    n_chunks = 0
    try:
        from backend.services.chunker import HierarchicalChunker
        from backend.data_access.vectordb import VectorDBManager
        docs = HierarchicalChunker().chunk(
            framework, include_levels=("domain", "control"), semantic_refine=True
        )
        vdb = VectorDBManager()
        if vdb._collection_count(collection_name) > 0:
            vdb.clear_collection(collection_name)
        vdb.store_documents(docs, collection_name=collection_name)
        n_chunks = len(docs)
    except Exception as exc:
        logger.warning("Embedding step failed (%s); continuing without vectors.", exc)

    _CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
    canonical_path = _CANONICAL_DIR / f"{collection_name}.json"
    save_json(framework, canonical_path)
    _p(f"Canonical JSON → {canonical_path.name}", 100)

    return {
        "collection_name": collection_name,
        "n_controls": n_controls,
        "n_domains": n_domains,
        "n_questions": n_controls,          # exactly one per control
        "n_chunks": n_chunks,
        "canonical_json_path": str(canonical_path.resolve()),
    }
