"""Configuration for the FastAPI API layer (backend/api).

Loads the SAME centralised env as the rest of the backend (backend/.env) and
resolves all paths absolutely so the server behaves identically regardless of
the current working directory.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# backend/api/config.py → API_DIR=backend/api, BACKEND_DIR=backend, PROJECT_ROOT=repo root
API_DIR = Path(__file__).resolve().parent
BACKEND_DIR = API_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent

# Use the project's single canonical .env (backend/.env), same as backend.config.settings.
load_dotenv(BACKEND_DIR / ".env")


def _resolve_db_url() -> str:
    """Use the configured DB, else fall back to a local SQLite file under backend/api."""
    url = os.getenv("DATABASE_URL", "")
    if not url or "[YOUR-PASSWORD]" in url:
        return f"sqlite+aiosqlite:///{(API_DIR / 'cyberai.db').as_posix()}"
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
            "postgres://", "postgresql+asyncpg://", 1
        )
    return url


#: Models confirmed live on Groq. Groq has decommissioned the entire
#: ``llama-3.*-versatile`` / ``llama-3.1-8b-instant`` / ``mixtral-8x7b-32768``
#: family, so any code still naming one of them fails with a non-retryable
#: HTTP 404 / 400 and the request never reaches the model. These are the
#: current replacements; order is "best first" so the first hit wins.
LIVE_MODELS: tuple[str, ...] = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
)


def _model_chain(primary: str, *fallbacks: str) -> list[str]:
    """Build a de-duplicated fallback chain that always ends on live models.

    The configured model goes first so an operator who pins a specific model
    keeps it, but the trailing live entries guarantee the chain can never be
    entirely dead — which is what silently killed the whole compliance
    semantic tier before.
    """
    chain: list[str] = []
    for name in (primary, *fallbacks, *LIVE_MODELS):
        name = (name or "").strip()
        if name and name not in chain:
            chain.append(name)
    return chain


class Settings:
    APP_NAME: str = "CyberAI"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    DATABASE_URL: str = _resolve_db_url()

    # Vector store: LOCAL Chroma (backend/.env CHROMA_PATH, resolved against the
    # repo root so the server behaves the same regardless of CWD).
    # Cloud Chroma settings are kept for reference but are not required.
    CHROMA_API_KEY: str = os.getenv("CHROMA_API_KEY", "")
    CHROMA_TENANT: str = os.getenv("CHROMA_TENANT", "")
    CHROMA_DATABASE: str = os.getenv("CHROMA_DATABASE", "cyber-ai-v2")
    CHROMA_HOST: str = os.getenv("CHROMA_HOST", "api.trychroma.com")
    CHROMA_PATH: str = str(
        (PROJECT_ROOT / os.getenv("CHROMA_PATH", "./chroma_db")).resolve()
        if not Path(os.getenv("CHROMA_PATH", "./chroma_db")).is_absolute()
        else Path(os.getenv("CHROMA_PATH", "./chroma_db"))
    )

    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "") or LIVE_MODELS[0]
    #: Ordered fallback chain for `GROQ_MODEL`. Always terminates on a model
    #: that is actually served, so a stale `GROQ_MODEL` in .env degrades to a
    #: slower retry instead of a hard 404 on every single LLM call.
    GROQ_MODEL_CHAIN: list[str] = _model_chain(
        os.getenv("GROQ_MODEL", ""),
        *(m for m in os.getenv("GROQ_MODEL_FALLBACKS", "").split(",") if m.strip()),
    )
    #: Cheaper model used for the semantic layer's clear-cut cases. A control
    #: with strong deterministic coverage does not need the strongest model to
    #: describe what the evidence already shows; the expensive model is reserved
    #: for ambiguous or thinly-evidenced controls.
    GROQ_FAST_MODEL: str = os.getenv("GROQ_FAST_MODEL", "") or LIVE_MODELS[1]
    #: Same guarantee for the fast tier.
    GROQ_FAST_MODEL_CHAIN: list[str] = _model_chain(
        os.getenv("GROQ_FAST_MODEL", ""), LIVE_MODELS[1]
    )
    #: Hard ceiling on a single semantic call. `LLMClient` retries internally
    #: with its own 90s socket timeout, so the outer bound has to be enforced
    #: here to keep one slow control from stalling a whole run.
    GROQ_SEMANTIC_TIMEOUT: float = float(os.getenv("GROQ_SEMANTIC_TIMEOUT", "45"))
    #: Controls per batched request on the batched semantic path.
    GROQ_SEMANTIC_BATCH_SIZE: int = int(os.getenv("GROQ_SEMANTIC_BATCH_SIZE", "5"))
    #: Whether the semantic layer may use the local Chroma vector stack for
    #: control ranking. OFF by default and deliberately so: that stack imports
    #: sentence-transformers, which is an optional heavy dependency and, on
    #: some numpy versions, segfaults the process on import rather than
    #: raising — a failure no try/except can contain. The semantic layer ranks
    #: controls deterministically without it, so nothing is lost by leaving it
    #: off; turn it on where the embedding stack is known to be healthy.
    SEMANTIC_VECTOR_RETRIEVAL: str = os.getenv("SEMANTIC_VECTOR_RETRIEVAL", "false")

    # ── Hindsight (persistent organizational memory) ─────────────────────────
    # Thresholds drive the deterministic memory-aware classifier in
    # compliance/memory/rules.py. They are counts of prior findings:
    #   RECURRING   — the same control+type has been found N times before.
    #   ESCALATION  — the same control+type has been found at least N times
    #                 total (including the current one). Repeated failures
    #                 beyond this point are surfaced for human escalation.
    #   PATTERN     — at least N distinct controls in the same domain share the
    #                 same finding type, so this is part of a cluster, not an
    #                 isolated gap.
    MEMORY_RECURRING_THRESHOLD: int = int(os.getenv("MEMORY_RECURRING_THRESHOLD", "2"))
    MEMORY_ESCALATION_THRESHOLD: int = int(os.getenv("MEMORY_ESCALATION_THRESHOLD", "3"))
    MEMORY_PATTERN_THRESHOLD: int = int(os.getenv("MEMORY_PATTERN_THRESHOLD", "3"))
    #: Whether the Hindsight review endpoint calls Groq to enrich a finding with
    #: its relationship to retrieved history. Advisory only; classification and
    #: the recorded evaluation status are deterministic regardless. Without a
    #: `GROQ_API_KEY` this degrades to "no enrichment" rather than failing.
    MEMORY_ENRICH_ENABLED: str = os.getenv("MEMORY_ENRICH_ENABLED", "true")
    #: Hard ceiling on a single Hindsight enrichment call, like
    #: `GROQ_SEMANTIC_TIMEOUT` but for the memory layer.
    GROQ_MEMORY_TIMEOUT: float = float(os.getenv("GROQ_MEMORY_TIMEOUT", "45"))

    # Absolute data folders (CWD-independent).
    DATA_DIR: str = str(API_DIR / "data")
    RAW_FOLDER: str = str(API_DIR / "data" / "raw")
    CANONICAL_FOLDER: str = str(API_DIR / "data" / "canonical")
    CHUNK_FOLDER: str = str(API_DIR / "data" / "chunks")
    EVIDENCE_FOLDER: str = str(API_DIR / "data" / "evidence")
    PRE_ASSESSMENT_FOLDER: str = str(API_DIR / "data" / "pre_assessments")
    TEMPLATES_FOLDER: str = str(API_DIR / "data" / "templates")

    # Next.js dev origins allowed by CORS.
    CORS_ORIGINS: list = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


settings = Settings()

# Framework codes excluded from all public-facing lists and stats.
HIDDEN_FRAMEWORK_CODES: frozenset[str] = frozenset()
