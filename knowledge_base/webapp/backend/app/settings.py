"""Backend configuration."""
from __future__ import annotations

from pathlib import Path

# webapp/backend/app/settings.py -> project root is three parents up.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATABASE_PATH = PROJECT_ROOT / "cyber_ai.db"

# Dev CORS origins (Vite dev server).
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# Stub identity until real auth is wired in. Every mutation is audit-logged
# against this user id / role. See DESIGN.md §12.
CURRENT_USER_ID = 1
CURRENT_USER_ROLE = "admin"  # one of: viewer | engineer | admin
