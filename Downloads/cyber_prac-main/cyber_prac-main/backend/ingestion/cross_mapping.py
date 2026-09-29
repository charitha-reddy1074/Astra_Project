"""ingestion/cross_mapping.py — Ingest authoritative cross-framework mappings.

A cross-mapping file declares, row by row, that a control in one framework
relates to a control in another framework, with an explicit relationship type
(e.g. EQUIVALENT, SUBSET_OF, RELATED). Unlike ``cross_mapper.py`` — which only
*suggests* topical similarity — an ingested file is treated as an authoritative
crosswalk supplied by the user.

Supported file formats: CSV, XLSX/XLS, JSON.

Recognised columns (header names are matched flexibly / case-insensitively):
    source_framework   from_framework  source fw
    source_control     source_id       from_control   source control id
    target_framework   to_framework    target fw
    target_control     target_id       to_control     target control id
    relationship       relation        mapping_type
    confidence         score
    notes              description      comment

``source_framework`` / ``target_framework`` may be omitted from the rows and
supplied once via the CLI/`ingest_mapping_file(...)` arguments instead.

Pipeline
--------
    parse_mapping_file(path)        -> raw rows (dicts with normalised keys)
    ingest_mapping_file(path, ...)  -> validate, normalise, persist JSON, archive
    load_mapping_records()          -> all stored mapping records
    apply_mappings_to_catalog(cat)  -> inject records as control cross_references

The applied cross-references use the same shape the rest of the codebase reads
(``other_framework_id`` / ``other_control_id`` / ``relationship`` / ``notes`` /
``confidence``), so questionnaires and ``_build_mappings_block`` pick them up.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.core.utils import logger, save_json

_MAPPINGS_DIR = _PROJECT_ROOT / "data" / "frameworks" / "mappings"
_SOURCE_DIR = _MAPPINGS_DIR / "source"

# ── Relationship vocabulary ────────────────────────────────────────────────
# Canonical relationship types, aligned with how published crosswalks (e.g.
# NIST OLIR, CIS→NIST) describe control-to-control relations.
_CANONICAL_RELATIONSHIPS = {
    "EQUIVALENT", "SUBSET_OF", "SUPERSET_OF", "INTERSECTS", "RELATED", "NO_RELATIONSHIP",
}
_RELATIONSHIP_ALIASES = {
    "equivalent": "EQUIVALENT", "equal": "EQUIVALENT", "equals": "EQUIVALENT",
    "exact": "EQUIVALENT", "same": "EQUIVALENT", "maps to": "EQUIVALENT",
    "subset": "SUBSET_OF", "subset of": "SUBSET_OF", "narrower": "SUBSET_OF",
    "is subset of": "SUBSET_OF",
    "superset": "SUPERSET_OF", "superset of": "SUPERSET_OF", "broader": "SUPERSET_OF",
    "is superset of": "SUPERSET_OF",
    "intersects": "INTERSECTS", "intersects with": "INTERSECTS", "overlaps": "INTERSECTS",
    "partial": "INTERSECTS", "partially": "INTERSECTS",
    "related": "RELATED", "related to": "RELATED", "relates to": "RELATED",
    "similar": "RELATED", "see also": "RELATED",
    "none": "NO_RELATIONSHIP", "no relationship": "NO_RELATIONSHIP", "n/a": "NO_RELATIONSHIP",
}

# ── Column header resolution ────────────────────────────────────────────────
_COLUMN_ALIASES = {
    "source_framework": {"source_framework", "source framework", "source_fw", "source fw",
                          "from_framework", "from framework", "src_framework", "framework"},
    "source_control": {"source_control", "source control", "source_control_id",
                       "source control id", "source_id", "source", "from_control",
                       "from control", "src_control", "control", "control_id"},
    "target_framework": {"target_framework", "target framework", "target_fw", "target fw",
                         "to_framework", "to framework", "dst_framework"},
    "target_control": {"target_control", "target control", "target_control_id",
                       "target control id", "target_id", "target", "to_control",
                       "to control", "mapped_control", "mapped control"},
    "relationship": {"relationship", "relation", "mapping_type", "mapping type",
                     "type", "rel"},
    "confidence": {"confidence", "score", "similarity", "similarity_score"},
    "notes": {"notes", "note", "description", "comment", "comments", "rationale"},
}


def _norm_header(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _build_header_map(headers: List[str]) -> Dict[str, str]:
    """Map each recognised field to the actual header present in the file."""
    resolved: Dict[str, str] = {}
    norm_to_actual = {_norm_header(h): h for h in headers if h is not None}
    for field, aliases in _COLUMN_ALIASES.items():
        for norm, actual in norm_to_actual.items():
            if norm in aliases:
                resolved[field] = actual
                break
    return resolved


def normalize_relationship(value: Any, default: str = "RELATED") -> str:
    """Map a free-text relationship to a canonical relationship type."""
    text = _norm_header(value)
    if not text:
        return default
    upper = text.upper().replace(" ", "_")
    if upper in _CANONICAL_RELATIONSHIPS:
        return upper
    return _RELATIONSHIP_ALIASES.get(text, default)


def _coerce_confidence(value: Any) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num > 1.0:  # treat 0-100 percentages as fractions
        num = num / 100.0
    return max(0.0, min(1.0, round(num, 4)))


# ── Parsing ─────────────────────────────────────────────────────────────────

def _rows_from_csv(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _rows_from_xlsx(path: Path) -> List[Dict[str, Any]]:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = [str(h) if h is not None else "" for h in next(rows_iter)]
    except StopIteration:
        return []
    records: List[Dict[str, Any]] = []
    for values in rows_iter:
        if values is None or all(v is None for v in values):
            continue
        record = {header[i]: values[i] for i in range(min(len(header), len(values)))}
        records.append(record)
    return records


def _rows_from_json(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        for key in ("mappings", "records", "criteria", "rows", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON mapping file must be a list of records or {mappings: [...]}.")
    return [r for r in data if isinstance(r, dict)]


def parse_mapping_file(path: str | Path) -> List[Dict[str, Any]]:
    """Read a mapping file and return rows keyed by recognised field names.

    Raises FileNotFoundError / ValueError on unreadable or unsupported input.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Mapping file not found: {path}")

    ext = path.suffix.lstrip(".").lower()
    if ext == "csv":
        raw_rows = _rows_from_csv(path)
    elif ext in ("xlsx", "xls"):
        raw_rows = _rows_from_xlsx(path)
    elif ext == "json":
        raw_rows = _rows_from_json(path)
    else:
        raise ValueError(f"Unsupported mapping file type '.{ext}'. Use CSV, XLSX, or JSON.")

    if not raw_rows:
        return []

    header_map = _build_header_map(list(raw_rows[0].keys()))
    normalised: List[Dict[str, Any]] = []
    for raw in raw_rows:
        row: Dict[str, Any] = {}
        for field, actual in header_map.items():
            row[field] = raw.get(actual)
        # Also accept rows that already use canonical field names directly.
        for field in _COLUMN_ALIASES:
            if field not in row and field in raw:
                row[field] = raw[field]
        normalised.append(row)
    return normalised


# ── Ingestion ─────────────────────────────────────────────────────────────--

def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value))
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def ingest_mapping_file(
    file_path: str | Path,
    source_framework: str = "",
    target_framework: str = "",
    default_relationship: str = "RELATED",
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """Ingest a cross-framework mapping file.

    Parameters
    ----------
    file_path:            Path to a CSV / XLSX / JSON mapping file.
    source_framework:     Fallback source framework name/key for rows that omit it.
    target_framework:     Fallback target framework name/key for rows that omit it.
    default_relationship: Relationship to use when a row omits one.

    Returns a summary dict including ``mapping_json_path`` and ``n_records``.
    """
    def _update(msg: str, pct: int) -> None:
        logger.info("[%3d%%] %s", pct, msg)
        if progress_cb:
            progress_cb(msg, pct)

    file_path = Path(file_path)
    _update(f"Parsing mapping file {file_path.name}…", 10)
    rows = parse_mapping_file(file_path)
    if not rows:
        raise ValueError(f"No mapping rows found in '{file_path.name}'.")

    default_rel = normalize_relationship(default_relationship, "RELATED")
    records: List[Dict[str, Any]] = []
    skipped = 0
    for row in rows:
        source_control = str(row.get("source_control") or "").strip()
        target_control = str(row.get("target_control") or "").strip()
        if not source_control or not target_control:
            skipped += 1
            continue
        src_fw = str(row.get("source_framework") or source_framework or "").strip()
        tgt_fw = str(row.get("target_framework") or target_framework or "").strip()
        records.append({
            "source_framework": src_fw,
            "source_framework_key": _slugify(src_fw),
            "source_control": source_control,
            "target_framework": tgt_fw,
            "target_framework_key": _slugify(tgt_fw),
            "target_control": target_control,
            "relationship": normalize_relationship(row.get("relationship"), default_rel),
            "confidence": _coerce_confidence(row.get("confidence")),
            "notes": str(row.get("notes") or "").strip(),
            "status": "authoritative",
            "source": f"ingested-file:{file_path.name}",
        })

    if not records:
        raise ValueError(
            f"No valid mapping rows in '{file_path.name}'. Each row needs a "
            "source_control and a target_control."
        )

    _update(f"Validated {len(records)} mapping record(s) ({skipped} skipped)…", 60)

    # ── Persist normalised records + archive the raw file ─────────────────
    _MAPPINGS_DIR.mkdir(parents=True, exist_ok=True)
    _SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    fw_pair = f"{records[0]['source_framework_key'] or 'source'}__{records[0]['target_framework_key'] or 'target'}"
    out_name = f"mapping_{_slugify(fw_pair)}.json"
    out_path = _MAPPINGS_DIR / out_name

    payload = {
        "_meta": {
            "description": "Authoritative cross-framework control mapping (user-ingested).",
            "source_file": file_path.name,
            "default_relationship": default_rel,
            "record_count": len(records),
            "skipped_rows": skipped,
        },
        "mappings": records,
    }
    save_json(payload, out_path)

    archived_path: Optional[Path] = None
    try:
        archived_path = _SOURCE_DIR / file_path.name
        if file_path.resolve() != archived_path.resolve():
            shutil.copy2(file_path, archived_path)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Could not archive raw mapping file: %s", exc)
        archived_path = None

    _update("Mapping ingested.", 100)
    summary = {
        "mapping_json_path": str(out_path.resolve()),
        "archived_source_path": str(archived_path.resolve()) if archived_path else "",
        "n_records": len(records),
        "n_skipped": skipped,
        "source_framework": records[0]["source_framework"],
        "target_framework": records[0]["target_framework"],
        "relationships": sorted({r["relationship"] for r in records}),
    }
    logger.info("Cross-mapping ingestion summary: %s", summary)
    return summary


# ── Loading + application ───────────────────────────────────────────────────

def load_mapping_records() -> List[Dict[str, Any]]:
    """Return all ingested mapping records from data/frameworks/mappings/."""
    records: List[Dict[str, Any]] = []
    if not _MAPPINGS_DIR.exists():
        return records
    for path in sorted(_MAPPINGS_DIR.glob("mapping_*.json")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            records.extend(payload.get("mappings", []))
        except Exception as exc:
            logger.warning("Could not load mapping file %s: %s", path.name, exc)
    return records


def _framework_identifiers(framework: Dict[str, Any]) -> set:
    """Collect lower-cased identifiers a mapping row might use to name a framework."""
    ids = set()
    for key in ("framework_key", "framework_name", "framework_id", "name"):
        value = str(framework.get(key) or "").strip().lower()
        if value:
            ids.add(value)
            ids.add(_slugify(value))
    return ids


def apply_mappings_to_catalog(catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Inject ingested mappings into the catalog as control cross_references.

    Each mapping's source control gains a cross_reference pointing at the target
    control, tagged ``status="authoritative"`` and ``source="ingested-file:…"``.
    Existing identical cross-references are not duplicated. Mutates and returns
    *catalog*.
    """
    records = load_mapping_records()
    if not records:
        return catalog

    # Index frameworks by every identifier they might be referenced with.
    fw_by_id: Dict[str, Dict[str, Any]] = {}
    for framework in catalog:
        for ident in _framework_identifiers(framework):
            fw_by_id.setdefault(ident, framework)

    applied = 0
    unresolved = 0
    for rec in records:
        src_keys = [
            str(rec.get("source_framework_key") or "").strip().lower(),
            str(rec.get("source_framework") or "").strip().lower(),
        ]
        framework = next((fw_by_id[k] for k in src_keys if k and k in fw_by_id), None)
        if framework is None:
            unresolved += 1
            continue

        source_control = str(rec.get("source_control") or "").strip().lower()
        target_fw = rec.get("target_framework") or rec.get("target_framework_key") or ""
        target_control = str(rec.get("target_control") or "").strip()
        if not source_control or not target_control:
            continue

        xref = {
            "other_framework_id": target_fw,
            "other_control_id": target_control,
            "relationship": rec.get("relationship", "RELATED"),
            "notes": rec.get("notes", ""),
            "status": rec.get("status", "authoritative"),
            "source": rec.get("source", "ingested-file"),
        }
        if rec.get("confidence") is not None:
            xref["confidence"] = rec["confidence"]

        for domain in framework.get("domains", []) or []:
            for control in domain.get("controls", []) or []:
                if str(control.get("control_id") or "").strip().lower() != source_control:
                    continue
                existing = control.setdefault("cross_references", [])
                dup = any(
                    str(x.get("other_framework_id", "")).lower() == str(target_fw).lower()
                    and str(x.get("other_control_id", "")).strip() == target_control
                    for x in existing
                )
                if not dup:
                    existing.append(xref)
                    applied += 1

    if unresolved:
        logger.warning(
            "Applied %d cross-mapping(s); %d row(s) referenced a source framework "
            "not present in the catalog.", applied, unresolved,
        )
    else:
        logger.info("Applied %d ingested cross-mapping(s) to the catalog.", applied)
    return catalog
