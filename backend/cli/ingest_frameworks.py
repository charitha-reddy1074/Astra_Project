"""Framework ingestion CLI — dynamic pipeline only.

Usage:
    python ingest_frameworks.py --file doc.pdf --name "PCI DSS" --version "4.0"
    python ingest_frameworks.py --file doc.xlsx --name "HIPAA" --levels domain control question
    python ingest_frameworks.py --file doc.pdf --name "SOC 2" --version "2023" --no-semantic
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]   # project root (contains backend/)
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import backend.config.settings  # noqa: F401  (loads backend/.env once)


def _dynamic_ingest(args) -> None:
    from backend.ingestion.dynamic_pipeline import ingest_file

    levels = tuple(args.levels) if args.levels else ("domain", "control")

    def _progress(msg: str, pct: int) -> None:
        print(f"  [{pct:3d}%] {msg}")

    result = ingest_file(
        file_path         = args.file,
        framework_name    = args.name,
        framework_version = args.version,
        include_levels    = levels,
        semantic_refine   = not args.no_semantic,
        progress_cb       = _progress,
    )
    print("\nIngestion complete:")
    print(f"  Collection : {result['collection_name']}")
    print(f"  Chunks     : {result['n_chunks']}")
    print(f"  Controls   : {result['n_controls']}")
    print(f"  Domains    : {result['n_domains']}")
    print(f"  Overwritten: {result['overwritten']}")
    print(f"  Stored file: {result.get('stored_source_path', '')}")
    print(f"  Canonical  : {result['canonical_json_path']}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Ingest any framework document (PDF/JSON/XLSX/CSV/XML) into ChromaDB.\n\n"
            "Uses Groq LLM extraction → HierarchicalChunker → VectorDBManager.\n"
            "API keys are read from .env (GROQ_API_KEY, TRYCHROMA_API_KEY, etc.)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--file", metavar="PATH", required=True,
        help="Path to a framework document (PDF, JSON, XLSX, CSV, XML).",
    )
    parser.add_argument(
        "--name", metavar="NAME", required=True,
        help='Framework name, e.g. "PCI DSS" or "HIPAA".',
    )
    parser.add_argument(
        "--version", metavar="VER", default="",
        help='Framework version, e.g. "4.0". Optional.',
    )
    parser.add_argument(
        "--levels", nargs="+",
        choices=["domain", "control", "control_segment", "question"],
        default=None,
        help="Chunk hierarchy levels to generate (default: domain control).",
    )
    parser.add_argument(
        "--no-semantic", action="store_true",
        help="Disable semantic sub-splitting of long controls.",
    )

    args = parser.parse_args()
    _dynamic_ingest(args)


if __name__ == "__main__":
    main()
