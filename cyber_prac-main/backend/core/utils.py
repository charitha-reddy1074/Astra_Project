"""
utils.py — Shared utility functions for the NIST CSF RAG pipeline.
"""

import json
import re
import uuid
import logging
import requests
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union


# ─────────────────────────────────────────
# Logging
# ─────────────────────────────────────────

def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure and return root logger with clean formatting."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("nist_rag")


logger = setup_logging()


# ─────────────────────────────────────────
# ID Generation
# ─────────────────────────────────────────

def generate_id() -> str:
    """Generate a unique UUID string."""
    return str(uuid.uuid4())


# ─────────────────────────────────────────
# Timestamps
# ─────────────────────────────────────────

def timestamp() -> str:
    """Return current UTC timestamp as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────
# JSON I/O
# ─────────────────────────────────────────

def save_json(data: Any, path: Path) -> None:
    """Serialise *data* to a pretty-printed JSON file at *path*."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
    logger.debug("Saved JSON → %s", path)


def load_json(path: Path) -> Any:
    """Load and return parsed JSON from *path*."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_llm_json(raw: str) -> Dict[str, Any]:
    """Extracts and parses JSON from LLM output.

    Strategy:
    1) Strip <think>...</think> reasoning blocks (reasoning models like nemotron).
    2) Prefer fenced ```json ... ``` blocks.
    3) Otherwise, scan the text to find the first balanced JSON object `{...}` or
       array `[...]` and attempt to parse that substring.
    4) Last resort: if the JSON is truncated (cut off by max_tokens), repair it
       by closing unterminated strings/containers and salvage what completed.

    This is intentionally resilient to common LLM formatting issues
    (preamble, markdown fences, reasoning tags, extra text before/after JSON,
    and responses truncated mid-output).
    """

    if raw is None:
        raise ValueError("LLM raw output is None")

    text = str(raw).strip()
    if not text:
        raise ValueError("LLM raw output is empty")

    # 0) Strip <think>...</think> reasoning blocks produced by reasoning models
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if not text:
        raise ValueError("LLM raw output is empty after stripping <think> blocks")

    # 1) fenced code blocks — object
    fenced_obj = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced_obj:
        return json.loads(fenced_obj.group(1))

    # 1b) fenced code blocks — array (wrap in dict to normalise callers)
    fenced_arr = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text, re.IGNORECASE)
    if fenced_arr:
        parsed = json.loads(fenced_arr.group(1))
        return parsed[0] if isinstance(parsed, list) and parsed else {}

    # 2) scan for first balanced JSON object/array
    def _try_parse_fragment(fragment: str) -> Any:
        cleaned = fragment.strip().replace("```", "")
        return json.loads(cleaned)

    start_chars = {"{": "}", "[": "]"}
    stack: List[str] = []
    start_idx: Optional[int] = None

    for i, ch in enumerate(text):
        if ch in start_chars and not stack:
            stack.append(start_chars[ch])
            start_idx = i
            continue

        if stack:
            if ch == stack[-1]:
                stack.pop()
                if not stack and start_idx is not None:
                    fragment = text[start_idx: i + 1]
                    parsed = _try_parse_fragment(fragment)
                    # Normalise: if the LLM returned a list, unwrap first element
                    if isinstance(parsed, list):
                        return parsed[0] if parsed else {}
                    return parsed
            elif ch in start_chars:
                stack.append(start_chars[ch])

    # 3) attempt to parse the full text after removing common fences
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    cleaned = cleaned.rstrip("`").strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed[0] if parsed else {}
        return parsed
    except json.JSONDecodeError:
        # 4) last resort: the JSON was truncated (cut off by max_tokens),
        #    producing an unterminated string / unclosed container. Repair it
        #    by discarding the trailing incomplete element and closing every
        #    open container, then parse — salvaging the answers that completed.
        repaired = _repair_truncated_json(cleaned)
        if repaired is None:
            raise
        parsed = json.loads(repaired)
        if isinstance(parsed, list):
            return parsed[0] if parsed else {}
        return parsed


def _repair_truncated_json(text: str) -> Optional[str]:
    """Best-effort repair of JSON truncated mid-output (e.g. by max_tokens).

    Scans for the first ``{``/``[`` and walks the text tracking string state and
    bracket depth. It remembers the position right after the last *fully closed*
    container, rewinds to there (dropping any partial trailing element and its
    dangling comma), and appends the closing brackets for whatever is still open.

    Returns the repaired JSON string, or ``None`` if nothing salvageable was
    found (no container was ever closed).
    """
    start = next((i for i, c in enumerate(text) if c in "{["), None)
    if start is None:
        return None

    stack: List[str] = []
    in_str = False
    escaped = False
    last_complete: Optional[tuple] = None  # (index_after_close, remaining_stack)

    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack:
                stack.pop()
            # Record a clean cut point only at array/object element boundaries
            # (depth >= 1), so the outer container can still be closed.
            last_complete = (i + 1, list(stack))

    if last_complete is None:
        return None
    idx, remaining = last_complete
    repaired = text[start:idx].rstrip().rstrip(",")
    repaired += "".join(reversed(remaining))
    return repaired


# ─────────────────────────────────────────
# Centralized LLM Client
# ─────────────────────────────────────────

class LLMClient:
    """Groq LLM client with multi-model fallback and retry logic."""

    # Every entry must be a model Groq still serves. The previous list
    # (llama-3.3-70b-versatile / llama-3.1-8b-instant / mixtral-8x7b-32768)
    # was decommissioned in full, which made every call site that omitted an
    # explicit model fail with a non-retryable 404/400 and then raise.
    DEFAULT_MODELS: List[str] = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
    ]

    def __init__(
        self,
        api_key: str,
        model: Optional[Union[str, List[str]]] = None,
        temperature: float = 0.3,
    ):
        self.api_key = api_key
        self.temperature = temperature
        self.url = "https://api.groq.com/openai/v1/chat/completions"

        if model is None:
            self.models = self.DEFAULT_MODELS
        elif isinstance(model, str):
            self.models = [model]
        else:
            self.models = model

        # Keep a single `.model` attribute pointing at the primary model
        # so existing log lines that reference `self.model` still work.
        self.model = self.models[0]

    def invoke(
        self,
        system_prompt,
        user_prompt: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        """
        Invoke the LLM and return the content string.

        Supports both positional prompts and dictionary-based inputs:
            client.invoke(system_prompt="...", user_prompt="...")
            client.invoke({"system_prompt": "...", "user_prompt": "..."})

        Tries each model in self.models in order, with up to 3 retries per model.

        Raises
        ------
        RuntimeError
            If all models and all retry attempts are exhausted.
        """
        # ── Normalise inputs ────────────────────────────────────────────
        if isinstance(system_prompt, dict):
            inputs = system_prompt
            s_prompt = inputs.get("system_prompt", "")
            u_prompt = inputs.get("user_prompt", "")
            m_tokens = inputs.get("max_tokens", max_tokens)
        else:
            s_prompt = system_prompt
            u_prompt = user_prompt or ""
            m_tokens = max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Optional[Exception] = None

        logger.info("Attempting to invoke LLM with models: %s", self.models)

        for current_model in self.models:
            logger.info("Trying model: %s", current_model)

            payload = {
                "model": current_model,
                "messages": [
                    {"role": "system", "content": s_prompt},
                    {"role": "user",   "content": u_prompt},
                ],
                "temperature": self.temperature,
                "max_tokens": m_tokens,
            }

            for attempt in range(3):
                try:
                    resp = requests.post(
                        self.url, json=payload, headers=headers, timeout=90
                    )

                    # ── Rate limit ──────────────────────────────────────
                    if resp.status_code == 429:
                        wait_time = 5
                        logger.warning(
                            "Model %s: Rate limited (429). Waiting %ds before retry %d/3...",
                            current_model, wait_time, attempt + 1,
                        )
                        time.sleep(wait_time)
                        last_error = RuntimeError(
                            f"Model {current_model}: Rate limited (HTTP 429)"
                        )
                        continue

                    # ── Non-retryable client errors (413, 400, 404…) ────
                    # These are deterministic: the identical request will
                    # fail the same way on every retry, so retrying just
                    # wastes time and the per-minute budget. A 413 in
                    # particular means the request (prompt + max_tokens)
                    # exceeds this model's per-request limit — fail over to
                    # the next model immediately instead of retrying 3×.
                    if 400 <= resp.status_code < 500 and resp.status_code != 408:
                        try:
                            detail = json.dumps(resp.json().get("error", {}))[:300]
                        except Exception:
                            detail = resp.text[:300]
                        last_error = RuntimeError(
                            f"Model {current_model}: non-retryable HTTP "
                            f"{resp.status_code}. {detail}"
                        )
                        logger.warning(
                            "Model %s: HTTP %d not retryable — moving to next "
                            "model. %s",
                            current_model, resp.status_code, detail,
                        )
                        break  # stop retrying this model; try the next one

                    resp.raise_for_status()
                    data = resp.json()

                    # ── Log raw response ────────────────────────────────
                    logger.debug(
                        "LLM raw API response for %s: %s",
                        current_model, json.dumps(data)[:1000],
                    )

                    # ── Validate choices ────────────────────────────────
                    choices = data.get("choices", [])
                    if not choices:
                        api_err = data.get("error", {})
                        raise RuntimeError(
                            f"Model {current_model}: No choices in LLM response. "
                            f"API error: {api_err}"
                        )

                    content: str = (
                        choices[0].get("message", {}).get("content", "") or ""
                    )

                    # ── Strip <think> reasoning blocks ──────────────────
                    content = re.sub(
                        r"<think>.*?</think>", "", content, flags=re.DOTALL
                    ).strip()

                    logger.debug(
                        "LLM content for model %s (attempt %d): %r",
                        current_model, attempt + 1, content[:300],
                    )

                    # A reasoning model (gpt-oss) spends part of `max_tokens`
                    # on reasoning tokens and can hit the ceiling with the
                    # answer truncated mid-JSON. Retrying the same budget is
                    # pointless, so fail over to the next model instead.
                    finish_reason = choices[0].get("finish_reason")
                    if finish_reason == "length":
                        logger.warning(
                            "Model %s: hit max_tokens (%d) before finishing. "
                            "Increasing the budget on the next attempt.",
                            current_model, m_tokens,
                        )
                        if not content:
                            m_tokens = int(m_tokens * 2)
                            last_error = RuntimeError(
                                f"Model {current_model}: reasoning exhausted "
                                f"max_tokens={m_tokens}; retrying with more budget"
                            )
                            continue

                    if not content:
                        raise RuntimeError(
                            f"Model {current_model}: LLM returned empty content on "
                            f"attempt {attempt + 1}. "
                            f"Full response snippet: {json.dumps(data)[:500]}"
                        )

                    return content  # ✅ success

                except RuntimeError as exc:
                    last_error = exc
                    logger.warning(
                        "Model %s: LLM attempt %d/3 failed (RuntimeError): %s",
                        current_model, attempt + 1, exc,
                    )
                    if attempt < 2:
                        time.sleep(2 ** attempt)

                except requests.exceptions.RequestException as exc:
                    last_error = exc
                    logger.warning(
                        "Model %s: LLM network error attempt %d/3: %s",
                        current_model, attempt + 1, exc,
                    )
                    if attempt < 2:
                        time.sleep(2 ** attempt)

                except Exception as exc:
                    logger.error(
                        "Model %s: Unexpected LLM client error: %s",
                        current_model, exc,
                    )
                    raise  # re-raise unexpected errors immediately

            logger.warning(
                "Model %s exhausted all retries. Moving to next model.", current_model
            )

        # ── All models and retries exhausted ────────────────────────────
        raise RuntimeError(
            f"All LLM models failed after multiple attempts. Last error: {last_error}"
        )


# ─────────────────────────────────────────
# Console Formatting
# ─────────────────────────────────────────

def print_banner(title: str) -> None:
    """Print a prominent section banner."""
    width = 64
    print("\n" + "═" * width)
    print(f"  {title}")
    print("═" * width)


def print_section(title: str) -> None:
    """Print a sub-section header."""
    print(f"\n{'─' * 56}")
    print(f"  ▶  {title}")
    print(f"{'─' * 56}")


def print_kv(key: str, value: Any, indent: int = 4) -> None:
    """Print a key-value pair with consistent indentation."""
    pad = " " * indent
    print(f"{pad}{key:<30} {value}")


def print_dict(d: dict, indent: int = 4) -> None:
    """Pretty-print a flat dictionary."""
    for k, v in d.items():
        print_kv(str(k), str(v), indent)


def print_verdict(verdict: dict) -> None:
    """Print an evidence evaluation verdict in a structured way."""
    status = verdict.get("compliance_status", "UNKNOWN")
    status_icons = {
        "COMPLIANT":           "✅",
        "PARTIALLY_COMPLIANT": "⚠️ ",
        "NON_COMPLIANT":       "❌",
    }
    icon = status_icons.get(status, "❓")

    print(f"\n  {icon}  Control : {verdict.get('control_id', 'N/A')}")
    print(f"      Status  : {status}")
    print(f"      Score   : {verdict.get('confidence_score', 0):.2f}")
    print(f"      Reason  : {verdict.get('reasoning', '')[:120]}...")

    missing = verdict.get("missing_requirements", [])
    if missing:
        print("      Missing :")
        for m in missing[:3]:
            print(f"               • {m}")
