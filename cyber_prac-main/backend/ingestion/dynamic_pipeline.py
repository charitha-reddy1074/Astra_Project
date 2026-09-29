"""
ingestion/dynamic_pipeline.py — Dynamic framework ingestion orchestrator.

Full pipeline for any uploaded compliance document:

    File (PDF / JSON / XLSX / CSV / XML)
      │
      ▼  ingestion.parsers  — text extraction ONLY (no chunking)
    List[RawChunk]
      │
      ▼  ingestion.extractor.DynamicFrameworkExtractor  — Groq LLM
    Framework dict  {framework_name, version, domains[{domain_id, controls[…]}]}
      │
      ▼  chunker.HierarchicalChunker  — 500-token, 25% overlap, Jaccard semantic
    List[Document]  with full rich metadata  (control_id, domain_id, chunk_level, …)
      │
      ▼  vectordb.VectorDBManager  — existing, unchanged
    ChromaDB collection

Downstream (rag_pipeline, questionnaire, reports) reads from VectorDBManager
exactly as before — no changes required there.

Re-ingestion behaviour
──────────────────────
Same name + same version   → old documents deleted, fresh data stored (overwrite).
Same name + different version → new collection created alongside (versioned).
Static collections (NIST/CIS/ISO/MA) are never touched at startup; a deliberate
re-upload of one of those frameworks triggers the normal overwrite path.
"""
from __future__ import annotations

import copy
import os
import re as _re
import shutil
import sys
import uuid as _uuid_mod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Ensure the project root (files/) is on sys.path so sibling modules resolve
# regardless of how this module is imported.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import backend.config.settings  # noqa: F401  (loads backend/.env once)

from backend.services.chunker import HierarchicalChunker
from backend.ingestion.collection_utils import framework_to_collection_name
from backend.ingestion.extractor import DynamicFrameworkExtractor
from backend.ingestion.parsers import get_parser
from backend.core.utils import logger, save_json
from backend.data_access.vectordb import VectorDBManager


_CANONICAL_DIR = _PROJECT_ROOT / "data" / "outputs" / "canonical_frameworks"
_FRAMEWORKS_DIR = _PROJECT_ROOT / "data" / "frameworks"

# Map a file extension to the data/frameworks sub-folder it should be stored in.
_EXT_FOLDER = {
    "pdf": "pdf",
    "json": "json",
    "xlsx": "xlsx",
    "xls": "xlsx",
    "csv": "csv",
    "xml": "xml",
    "docx": "docx",
    "doc": "docx",
    "txt": "txt",
}


def _store_source_file(file_path: Path, ext: str) -> Optional[Path]:
    """Copy the uploaded framework file into data/frameworks/<filetype>/.

    The destination folder is chosen from the file's extension. If the file is
    already inside its destination folder the copy is skipped. Returns the path
    of the stored copy (or the original location if already in place), or None
    if the copy fails.
    """
    folder = _EXT_FOLDER.get(ext, ext or "other")
    dest_dir = _FRAMEWORKS_DIR / folder
    dest_path = dest_dir / file_path.name
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        if file_path.resolve() == dest_path.resolve():
            return dest_path  # already stored in the right place
        shutil.copy2(file_path, dest_path)
        logger.info("Stored framework source → %s", dest_path)
        return dest_path
    except Exception as exc:
        logger.warning("Could not store framework source file in %s: %s", dest_dir, exc)
        return None

# ---------------------------------------------------------------------------
# Sub-domain resolution — deterministic grouping for known framework ID patterns
# ---------------------------------------------------------------------------

_NIST_CSF_CATEGORY_NAMES: Dict[str, str] = {
    "GV.OC": "Organizational Context",
    "GV.RM": "Risk Management Strategy",
    "GV.RR": "Roles, Responsibilities, and Authorities",
    "GV.PO": "Policy",
    "GV.OV": "Oversight",
    "GV.SC": "Cybersecurity Supply Chain Risk Management",
    "ID.AM": "Asset Management",
    "ID.RA": "Risk Assessment",
    "ID.IM": "Improvement",
    "PR.AA": "Identity Management, Authentication, and Access Control",
    "PR.AT": "Awareness and Training",
    "PR.DS": "Data Security",
    "PR.PS": "Platform Security",
    "PR.IR": "Technology Infrastructure Resilience",
    "DE.CM": "Continuous Monitoring",
    "DE.AE": "Adverse Event Analysis",
    "RS.MA": "Incident Management",
    "RS.AN": "Incident Analysis",
    "RS.CO": "Incident Response Reporting and Communication",
    "RS.MI": "Incident Mitigation",
    "RC.RP": "Incident Recovery Plan Execution",
    "RC.CO": "Incident Recovery Communication",
}


def _resolve_sub_domain(control_id: str) -> str:
    """Deterministically derive a sub-domain name from a control ID."""
    # NIST CSF: GV.OC-01 → GV.OC → "Organizational Context"
    m = _re.match(r"^([A-Z]{2,3}\.[A-Z]{2,4})-\d+", control_id)
    if m:
        prefix = m.group(1)
        return _NIST_CSF_CATEGORY_NAMES.get(prefix, prefix)
    # PCI DSS 3-level: 3.2.1 → "Requirement 3.2"
    m = _re.match(r"^(\d{1,2}\.\d+)\.\d+", control_id)
    if m:
        return f"Requirement {m.group(1)}"
    # PCI DSS 2-level: 3.2 → "Requirement 3.2"
    m = _re.match(r"^(\d{1,2}\.\d+)$", control_id)
    if m:
        return f"Requirement {m.group(1)}"
    # ISO 27001 4-level: A.9.2.1 → "A.9.2"
    m = _re.match(r"^([A-Z]\.\d+\.\d+)\.\d+$", control_id)
    if m:
        return m.group(1)
    # ISO 27001 3-level: A.9.2 → "A.9"
    m = _re.match(r"^([A-Z]\.\d+)\.\d+$", control_id)
    if m:
        return m.group(1)
    return "General"


def _enrich_with_sub_domains(framework_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a deep copy of framework_dict with controls grouped into sub_domains.

    Each domain dict changes from:
        {"domain_id": ..., "domain_name": ..., "controls": [...]}
    to:
        {"domain_id": ..., "domain_name": ..., "sub_domains": [
            {"sub_domain_id": ..., "sub_domain_name": ..., "controls": [...]}
        ]}

    The original framework_dict (with flat controls) is NOT mutated — the chunker
    still receives the flat structure.
    """
    result = copy.deepcopy(framework_dict)
    for domain in result.get("domains", []):
        controls = domain.pop("controls", [])
        sub_domain_map: Dict[str, Dict[str, Any]] = {}
        for ctrl in controls:
            sd_name = _resolve_sub_domain(ctrl.get("control_id", ""))
            if sd_name not in sub_domain_map:
                sub_domain_map[sd_name] = {
                    "sub_domain_id":   str(_uuid_mod.uuid4()),
                    "sub_domain_name": sd_name,
                    "controls":        [],
                }
            sub_domain_map[sd_name]["controls"].append(ctrl)
        domain["sub_domains"] = list(sub_domain_map.values())
        domain["controls"] = controls  # preserve flat list for questionnaire generation
    return result


def _build_extractor() -> DynamicFrameworkExtractor:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set in .env. Dynamic ingestion requires a Groq API key."
        )
    model      = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    batch_size = int(os.environ.get("GROQ_BATCH_SIZE", "12"))
    return DynamicFrameworkExtractor(api_key=api_key, model=model, batch_size=batch_size)


def ingest_file(
    file_path: str | Path,
    framework_name: str,
    framework_version: str,
    include_levels: Tuple[str, ...] = ("domain", "control"),
    semantic_refine: bool = True,
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """
    Ingest an arbitrary compliance document into the ChromaDB vector store.

    Parameters
    ----------
    file_path:         Path to the document (PDF, JSON, XLSX, CSV, XML).
    framework_name:    Human-readable name, e.g. "PCI DSS" or "HIPAA".
    framework_version: Version string, e.g. "4.0" or "2024".
    include_levels:    Chunk hierarchy levels for HierarchicalChunker.
                       Default ("domain", "control") matches static ingestion.
                       Add "question" or "control_segment" for finer granularity.
    semantic_refine:   Pass semantic_refine=True to enable Jaccard-based sub-splitting
                       of long controls (same default as static ingestion).
    progress_cb:       Optional callback(message: str, percent: int 0-100).

    Returns
    -------
    Dict with keys:
        collection_name     — ChromaDB collection that was written
        n_chunks            — total Document objects stored
        n_controls          — unique controls extracted by Groq
        n_domains           — domain groups
        canonical_json_path — path to the saved canonical JSON file
        overwritten         — True if a previous ingestion was replaced

    Raises
    ------
    FileNotFoundError  if file_path does not exist
    ValueError         if no text could be extracted from the file
    RuntimeError       if GROQ_API_KEY is missing
    """
    def _update(msg: str, pct: int) -> None:
        logger.info("[%3d%%] %s", pct, msg)
        if progress_cb:
            progress_cb(msg, pct)

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    ext = file_path.suffix.lstrip(".").lower()
    collection_name = framework_to_collection_name(framework_name, framework_version)

    # ── 0. Archive the source file under data/frameworks/<filetype>/ ───────
    _update(f"Storing source file under data/frameworks/{_EXT_FOLDER.get(ext, ext)}…", 3)
    stored_source_path = _store_source_file(file_path, ext)

    # ── 1. Parse ──────────────────────────────────────────────────────────
    _update(f"Parsing {file_path.name} ({ext})…", 5)
    parser     = get_parser(str(file_path))
    raw_chunks = parser.parse(str(file_path))
    if not raw_chunks:
        raise ValueError(
            f"No text could be extracted from '{file_path.name}'. "
            "Check that the file is not password-protected or empty."
        )
    _update(f"Extracted {len(raw_chunks)} raw text blocks", 15)

    # ── 2. Groq LLM extraction → Framework dict ───────────────────────────
    _update(f"Running Groq LLM extraction ({os.environ.get('GROQ_MODEL', 'openai/gpt-oss-120b')})…", 20)
    extractor     = _build_extractor()
    batch_size    = int(os.environ.get("GROQ_BATCH_SIZE", "12"))
    total_batches = max(1, (len(raw_chunks) + batch_size - 1) // batch_size)

    def _llm_progress(msg: str, current: int, total: int) -> None:
        pct = 20 + int((current / max(total, 1)) * 50)
        _update(msg, pct)

    framework_dict = extractor.extract(
        raw_chunks        = raw_chunks,
        framework_name    = framework_name,
        framework_version = framework_version,
        source_format     = ext,
        progress_cb       = _llm_progress,
    )

    n_controls = sum(len(d["controls"]) for d in framework_dict.get("domains", []))
    n_domains  = len(framework_dict.get("domains", []))
    _update(f"Extracted {n_controls} controls across {n_domains} domains", 72)

    if n_controls == 0:
        logger.warning(
            "Groq extraction returned 0 controls for '%s %s'. "
            "The file may not contain recognisable control IDs.",
            framework_name, framework_version,
        )

    # ── 3. Chunk with HierarchicalChunker ────────────────────────────────
    _update("Chunking with HierarchicalChunker…", 75)
    chunker = HierarchicalChunker()
    docs    = chunker.chunk(
        framework_dict,
        include_levels  = include_levels,
        semantic_refine = semantic_refine,
    )
    _update(f"Produced {len(docs)} chunks (levels={list(include_levels)})", 82)

    # ── 4. Re-ingestion check + store ─────────────────────────────────────
    vdb         = VectorDBManager()
    existing    = vdb._collection_count(collection_name)
    overwritten = False

    if existing > 0:
        logger.info(
            "Re-ingestion: overwriting %d existing docs in '%s'.",
            existing, collection_name,
        )
        vdb.clear_collection(collection_name)
        overwritten = True

    _update(f"Storing {len(docs)} chunks into '{collection_name}'…", 85)
    vdb.store_documents(docs, collection_name=collection_name)
    _update("Vector store updated.", 93)

    # ── 5. Persist canonical JSON (with sub_domain grouping) ─────────────
    _CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
    canonical_path = _CANONICAL_DIR / f"{collection_name}.json"
    canonical_dict = _enrich_with_sub_domains(framework_dict)
    save_json(canonical_dict, canonical_path)
    _update(f"Canonical JSON → {canonical_path.name}", 97)

    n_sub_domains = sum(
        len(d.get("sub_domains", [])) for d in canonical_dict.get("domains", [])
    )

    _update("Done.", 100)

    summary = {
        "collection_name":     collection_name,
        "n_chunks":            len(docs),
        "n_controls":          n_controls,
        "n_domains":           n_domains,
        "n_sub_domains":       n_sub_domains,
        "canonical_json_path": str(canonical_path.resolve()),
        "stored_source_path":  str(stored_source_path.resolve()) if stored_source_path else "",
        "overwritten":         overwritten,
    }
    logger.info("Ingestion summary: %s", summary)
    return summary
