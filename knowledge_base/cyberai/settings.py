"""Central configuration and path resolution.

Everything derives from the project root so the scripts run from anywhere
with `python create_database.py` etc.
"""
from __future__ import annotations

import logging
from pathlib import Path

# --- Paths -------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "f_data"
SCHEMA_PATH: Path = PROJECT_ROOT / "schema.sql"
DATABASE_PATH: Path = PROJECT_ROOT / "cyber_ai.db"

# --- Framework code mapping --------------------------------------------------
# Maps a source filename (stem, lower-cased) to the stable machine `code`.
# Unknown files fall back to their upper-cased stem.
FILENAME_TO_CODE: dict[str, str] = {
    "nist": "NIST_CSF",
    "iso": "ISO_27001",
    "pci": "PCI_DSS",
    "cis": "CIS_CONTROLS",
    "market_assesment": "MARKET",   # note: source filename is misspelled
    "market_assessment": "MARKET",
}


def configure_logging(verbose: bool = False) -> logging.Logger:
    """Configure and return the shared 'cyberai' logger."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("cyberai")
