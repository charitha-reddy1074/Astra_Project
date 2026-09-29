"""
Ingestion pipeline: file → canonical JSON → chunks.

Supports PDF frameworks (NIST, ISO, CIS) and XLSX frameworks (Market Assessment).
New frameworks only require: a parser function + one elif in _parse().
"""
import json
import os

from backend.api.config import settings
from backend.api.ingestion.framework_detector import detect_framework
from backend.api.ingestion.format_detector import detect_format
from backend.api.ingestion.chunkers.chunk_router import route_chunk_builder
from backend.api.ingestion.parsers.nist_parser import parse_nist_framework
from backend.api.ingestion.parsers.iso_parser import parse_iso_framework
from backend.api.ingestion.parsers.cis_parser import parse_cis_framework


def run_pipeline(file_path: str) -> dict:
    """
    Run the full ingestion pipeline for a framework file.
    Returns {"framework", "format", "canonical_json", "chunks"}.
    """
    framework = detect_framework(file_path)
    file_format = detect_format(file_path)
    print(f"Ingesting: framework={framework} format={file_format}")

    canonical_json = _parse(file_path, framework)
    framework_code = canonical_json["code"].lower()

    canonical_path = os.path.join(settings.CANONICAL_FOLDER, f"{framework_code}.json")
    _write_json(canonical_json, canonical_path)
    print(f"Saved canonical: {canonical_path}")

    chunks = route_chunk_builder(canonical_json)
    print(f"Generated {len(chunks)} chunks")

    chunk_path = os.path.join(settings.CHUNK_FOLDER, f"{framework_code}_chunks.json")
    _write_json(chunks, chunk_path)
    print(f"Saved chunks: {chunk_path}")

    return {
        "framework": framework,
        "format": file_format,
        "canonical_json": canonical_json,
        "chunks": chunks,
    }


def _parse(file_path: str, framework: str) -> dict:
    if framework == "NIST":
        return parse_nist_framework(file_path)
    if framework == "ISO27001":
        return parse_iso_framework(file_path)
    if framework == "CIS":
        return parse_cis_framework(file_path)
    if framework == "MARKET_ASSESSMENT":
        return _parse_market_xlsx(file_path)
    raise ValueError(f"Unsupported framework: {framework}")


def _parse_market_xlsx(file_path: str) -> dict:
    import openpyxl
    from scripts.build_market_canonical import build_canonical
    wb = openpyxl.load_workbook(file_path)
    return build_canonical(wb)


def _write_json(data, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
