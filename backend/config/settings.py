"""Centralised configuration for the backend.

Single source of truth for environment loading and project paths. Importing
this module loads the project's ``.env`` exactly once, from its canonical
location at ``backend/.env`` — regardless of the current working directory or
which module triggered the import. All other modules should rely on this
instead of calling ``load_dotenv()`` themselves.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# .../backend/config/settings.py  →  parents[1] == .../backend
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

# The .env now lives inside the backend package (moved here from the repo root).
ENV_PATH = BACKEND_DIR / ".env"

# Load it once. Safe to import this module from anywhere, any number of times.
load_dotenv(ENV_PATH)

# Commonly needed project paths (data/ and chroma_db/ stay at the repo root).
DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = DATA_DIR / "outputs"
