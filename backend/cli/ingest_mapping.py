"""Cross-framework mapping ingestion CLI.

Ingest an authoritative crosswalk that relates controls in one framework to
controls in another. The mapping is normalised, stored under
data/frameworks/mappings/, and applied as control cross_references the next
time the framework catalog is loaded (so questionnaires pick it up).

Usage:
    python ingest_mapping.py --file cis_to_nist.csv
    python ingest_mapping.py --file map.xlsx --source "CIS Controls v8.1.2" --target "NIST CSF 2.0"
    python ingest_mapping.py --file map.json --relationship subset_of

The file may be CSV, XLSX, or JSON. Recognised columns (matched flexibly):
    source_framework, source_control, target_framework, target_control,
    relationship, confidence, notes
source_framework / target_framework can instead be supplied once via
--source / --target for files whose rows omit them.
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]   # project root (contains backend/)
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import backend.config.settings  # noqa: F401  (loads backend/.env once)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Ingest an authoritative cross-framework control mapping (CSV/XLSX/JSON).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--file", metavar="PATH", required=True,
                        help="Path to the mapping file (CSV, XLSX, or JSON).")
    parser.add_argument("--source", metavar="NAME", default="",
                        help="Source framework name/key (fallback for rows that omit it).")
    parser.add_argument("--target", metavar="NAME", default="",
                        help="Target framework name/key (fallback for rows that omit it).")
    parser.add_argument("--relationship", metavar="REL", default="RELATED",
                        help="Default relationship when a row omits one "
                             "(EQUIVALENT, SUBSET_OF, SUPERSET_OF, INTERSECTS, RELATED).")

    args = parser.parse_args()

    from backend.ingestion.cross_mapping import ingest_mapping_file

    def _progress(msg: str, pct: int) -> None:
        print(f"  [{pct:3d}%] {msg}")

    result = ingest_mapping_file(
        file_path            = args.file,
        source_framework     = args.source,
        target_framework     = args.target,
        default_relationship = args.relationship,
        progress_cb          = _progress,
    )

    print("\nMapping ingestion complete:")
    print(f"  Records       : {result['n_records']}")
    print(f"  Skipped rows  : {result['n_skipped']}")
    print(f"  Source → Target: {result['source_framework'] or '(per-row)'} → {result['target_framework'] or '(per-row)'}")
    print(f"  Relationships : {', '.join(result['relationships'])}")
    print(f"  Stored file   : {result['archived_source_path']}")
    print(f"  Mapping JSON  : {result['mapping_json_path']}")


if __name__ == "__main__":
    main()
