"""LLM helpers to evaluate the evidence uploaded for a category (sub-domain)
against the Organizational Assessment 0-3 maturity rubric.

The evaluation is grounded in the SAME conditions the source scoring spreadsheet
uses per item (see the `maturity_guides` canonical criteria):

  * a 0-3 maturity scale read differently for a **Technology** than for a
    **Process** (each item carries the correct rubric column);
  * the item's **Scope & Cadence** (the coverage/frequency expectation);
  * the item's **Preferred Tooling** (is the expected capability in use);
  * the **expected evidence** that proves the control operates.

For each item the model returns a defensible 0-3 level plus the strengths,
gaps and a recommendation to reach the next level — mirroring the Scoring
sheet's Score / Strengths / Gaps / Recommendations columns. A category-level
roll-up and follow-up questions are derived from the item ratings.

Reuses the same requests-based LLMClient as the prefill pipeline so it works in
the API-only deployment (no flask/langchain dependency).
"""
import json

from backend.core.utils import LLMClient, parse_llm_json
from backend.core.evaluation_metrics import market_level_from_percent


MIN_FOLLOWUPS = 1
MAX_FOLLOWUPS = 10

# 0-3 maturity level labels (Organizational Assessment scale).
LEVEL_LABELS = {0: "Not in place", 1: "Partially in place", 2: "Passable", 3: "Strong"}

# Fallback rubric wording, used only when an item carries no maturity_guide.
_DEFAULT_RUBRIC = {
    "Technology": {
        0: "Tooling is absent, not deployed, or deployed but not functional or not in use.",
        1: "Deployed to only part of the environment, partially configured, or not consistently enforced.",
        2: "Covers most in-scope assets, appropriately configured and enforced, with only minor gaps.",
        3: "Fully covers the in-scope environment, hardened and well configured, enforced, monitored, and integrated into security operations.",
    },
    "Process": {
        0: "No defined or repeatable process exists. Activity is absent or purely ad hoc.",
        1: "Defined but informal, undocumented, or followed inconsistently.",
        2: "Documented and followed consistently with clear ownership, though cadence, metrics, or exception handling may not be fully formalized.",
        3: "Formally documented, consistently executed, owned, measured against defined metrics or SLAs, and reviewed on a set cadence.",
    },
}


RATING_SYSTEM_PROMPT = """\
You are a senior cybersecurity assessor scoring an organization's maturity
against a fixed 0-3 rubric. You are given a CATEGORY containing one or more
ITEMS (each a Technology or a Process) and the EVIDENCE documents collected
for that organization.

Score STRICTLY from the evidence — never assume a control exists because it is
common practice. If the evidence is silent on an item, that item is weak.

For EACH item, choose the single 0-3 level whose definition the evidence best
satisfies. The item tells you whether the Technology or Process rubric applies
and gives you that rubric's exact wording for all four levels:
  0 = Not in place      1 = Partially in place
  2 = Passable          3 = Strong

Apply these THREE capping rules — they can only LOWER a level, never raise it:
  1. COVERAGE vs SCOPE: the item's "scope_cadence" states the coverage and
     frequency expected (e.g. "all users", "all endpoints", "quarterly"). If
     the evidence does not demonstrate that coverage/cadence, the item cannot
     exceed level 1.
  2. EVIDENCE CLASS: level 3 (Strong) requires OPERATIONAL evidence that the
     control runs (coverage reports, logs, tickets, dashboards, review
     records). A policy or standard document alone caps the item at level 2.
  3. POLICY BACKING (Process items): if no approved, owned, reviewed policy
     backs the process, cap the item at level 2 even when practice looks
     consistent.

For each item also report:
  - "coverage": "full" | "most" | "partial" | "none" | "unknown"
  - "tooling_present": true | false | null   (is the preferred tool / an
     equivalent capability evidenced?)
  - "evidence_class": "operational" | "policy" | "claim_only" | "none"
  - "strengths": concrete things the evidence proves (may be empty)
  - "gaps": specific missing coverage / documentation / evidence
  - "recommendation": the single most important action to reach the NEXT level
  - "rationale": 1-2 sentences citing the evidence and the chosen rubric level

Then write follow-up questions for the CATEGORY as a whole — at least
{min_followups}, at most {max_followups}. Weak/thin evidence needs more; strong
evidence needs only a few confirmatory ones. Every question must be specific
and answerable by the assessed organization, and the set must cover three angles:
  - GENUINITY of the evidence (authentic, current, consistent — dates, system
    names, who produced it, does it match the claimed process);
  - POLICY verification (approved, owned, reviewed policy; exceptions);
  - IMPLEMENTATION verification (how it operates, who, how often, how gaps are
    found and fixed).

Return ONLY valid JSON (no markdown, no commentary):
{{
  "items": [
    {{
      "control_code": "<the item's control_code, copied verbatim>",
      "level": <integer 0-3>,
      "coverage": "full|most|partial|none|unknown",
      "tooling_present": true,
      "evidence_class": "operational|policy|claim_only|none",
      "rationale": "<1-2 sentences>",
      "strengths": ["<strength>", "..."],
      "gaps": ["<gap>", "..."],
      "recommendation": "<action to reach the next level>"
    }}
  ],
  "followups": ["<q1>", "<q2>", "..."]
}}
"""


def _item_rubric(item: dict) -> tuple[str, dict[int, str]]:
    """Return (item_type, {level:int -> rubric definition}) for one item.

    Prefers the item's own maturity_guide (already specialised to its type);
    falls back to the generic Technology/Process rubric.
    """
    guide = item.get("maturity_guide") or {}
    item_type = (item.get("item_type") or guide.get("type") or guide.get("item_type") or "").strip()
    # Normalise "Technology/Process" (Network Segmentation & Zoning) to Technology.
    norm_type = "Process" if item_type.lower().startswith("process") else "Technology"
    defs: dict[int, str] = {}
    for lvl in range(4):
        node = guide.get(f"level_{lvl}")
        if isinstance(node, dict) and node.get("definition"):
            defs[lvl] = str(node["definition"])
    if len(defs) < 4:
        defs = dict(_DEFAULT_RUBRIC[norm_type])
    return item_type or norm_type, defs


def _render_items(items: list[dict]) -> str:
    """Render the item rubric block the model scores against."""
    blocks: list[str] = []
    for it in items:
        item_type, defs = _item_rubric(it)
        guide = it.get("maturity_guide") or {}
        scope = it.get("scope_cadence") or guide.get("scope_cadence") or "(not specified)"
        tooling = it.get("preferred_tooling") or guide.get("preferred_tooling") or "(none specified)"
        ev = it.get("expected_evidence_types") or guide.get("expected_evidence_types") or []
        blocks.append(
            f"- control_code: {it.get('control_code')}\n"
            f"  item: {it.get('name') or it.get('control_code')}  [{item_type}]\n"
            f"  what it is: {it.get('statement') or '(none)'}\n"
            f"  scope_cadence (coverage/frequency expected): {scope}\n"
            f"  preferred_tooling: {tooling}\n"
            f"  expected_evidence: {'; '.join(ev) if ev else '(none specified)'}\n"
            f"  0-3 rubric for this item:\n"
            f"    0 Not in place: {defs[0]}\n"
            f"    1 Partially in place: {defs[1]}\n"
            f"    2 Passable: {defs[2]}\n"
            f"    3 Strong: {defs[3]}"
        )
    return "\n".join(blocks)


def rate_category(category: dict, evidence_corpus: str, api_key: str, model: str,
                  max_tokens: int = 2000) -> dict:
    """Rate every item in one category against the 0-3 rubric and generate
    category follow-up questions.

    `category` keys: category_name, domain_name, criteria_statement,
    expected_evidence_types (list), items (list of per-item dicts with
    control_code, name, statement, item_type, scope_cadence, preferred_tooling,
    expected_evidence_types, maturity_guide). Raises on a hard LLM failure.
    """
    items = category.get("items") or []
    sys_prompt = RATING_SYSTEM_PROMPT.format(
        min_followups=MIN_FOLLOWUPS, max_followups=MAX_FOLLOWUPS
    )
    user_prompt = (
        "CATEGORY: " + str(category.get("category_name") or category.get("category_code") or "") + "\n"
        "DOMAIN: " + str(category.get("domain_name") or category.get("domain_code") or "") + "\n"
        "CATEGORY CRITERIA:\n"
        + str(category.get("criteria_statement") or "(none provided)") + "\n\n"
        "ITEMS TO SCORE (assign each one a 0-3 level using its own rubric):\n"
        + (_render_items(items) or "- (no items)") + "\n\n"
        "EVIDENCE DOCUMENTS:\n"
        + (evidence_corpus or "(no evidence provided)") + "\n\n"
        f"Score every item above and return the JSON object now, with an entry "
        f"in \"items\" for EACH control_code and between {MIN_FOLLOWUPS} and "
        f"{MAX_FOLLOWUPS} category follow-up questions covering evidence "
        f"genuinity, policy verification, and implementation verification."
    )
    model_chain: list = []
    # `model` first so a configured/pinned model is preferred. The trailing
    # entries are live Groq models — the previous head of this chain
    # (llama-3.3-70b-versatile) is decommissioned and burned a wasted round
    # trip before every real attempt.
    for m in [model, *LLMClient.DEFAULT_MODELS]:
        if m and m not in model_chain:
            model_chain.append(m)
    client = LLMClient(api_key, model_chain, temperature=0.2)
    raw = client.invoke({
        "system_prompt": sys_prompt,
        "user_prompt": user_prompt,
        "max_tokens": max_tokens,
    })
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        parsed = {}
    return _normalise(parsed, items)


def _normalise(parsed: dict, items: list[dict]) -> dict:
    """Shape the LLM output into per-item ratings + a category roll-up.

    The category score/verdict are DERIVED from the item levels (achieved vs
    possible, each item worth 3) so they can never contradict the items.
    """
    by_code = {str(it.get("control_code")): it for it in items}
    raw_items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
    raw_by_code = {str(r.get("control_code")): r for r in raw_items if isinstance(r, dict)}

    item_ratings: list[dict] = []
    all_missing: list[str] = []
    achieved = possible = 0
    for it in items:
        code = str(it.get("control_code"))
        r = raw_by_code.get(code, {})
        item_type, _ = _item_rubric(it)
        try:
            level = int(round(float(r.get("level"))))
        except (TypeError, ValueError):
            level = 0
        level = max(0, min(3, level))

        gaps = [str(g).strip() for g in (r.get("gaps") or []) if str(g).strip()]
        strengths = [str(s).strip() for s in (r.get("strengths") or []) if str(s).strip()]
        recommendation = str(r.get("recommendation") or "").strip() or _default_reco(level, it)
        evidence_class = str(r.get("evidence_class") or "").strip().lower() or "none"
        coverage = str(r.get("coverage") or "").strip().lower() or "unknown"
        tooling_present = r.get("tooling_present")
        if isinstance(tooling_present, str):
            tooling_present = tooling_present.strip().lower() in ("true", "yes", "1")

        item_ratings.append({
            "control_code": code,
            "control_name": it.get("name") or code,
            "item_type": item_type,
            "level": level,
            "level_label": LEVEL_LABELS[level],
            "coverage": coverage,
            "tooling_present": tooling_present,
            "evidence_class": evidence_class,
            "rationale": str(r.get("rationale") or "").strip(),
            "strengths": strengths[:4],
            "gaps": gaps[:4],
            "recommendation": recommendation,
        })
        all_missing.extend(gaps)
        achieved += level
        possible += 3

    # Convert the 0-3 LLM ratings onto the SAME achieved/possible percentage
    # axis the deterministic frameworks use, then classify with the shared
    # market band mapping (evaluation_metrics.market_level_from_percent) rather
    # than ad-hoc thresholds. This keeps the LLM-rated Organizational Assessment
    # directly comparable to the 0-100% weighted framework scores.
    score = round(achieved / possible * 100, 1) if possible else 0.0
    level = market_level_from_percent(score)          # 3=Strong 2=Passable 1=Partial 0=None
    verdict = "strong" if level >= 3 else "partial" if level >= 2 else "weak"

    followups = [str(q).strip() for q in (parsed.get("followups") or []) if str(q).strip()]
    followups = followups[:MAX_FOLLOWUPS]
    if len(followups) < MIN_FOLLOWUPS:
        followups.append(
            "Provide additional detail and supporting evidence for this category's criteria."
        )

    # De-duplicate gaps for the category-level "missing" list, preserving order.
    seen: set = set()
    missing: list[str] = []
    for g in all_missing:
        if g not in seen:
            seen.add(g)
            missing.append(g)

    rationale = str(parsed.get("rationale") or "").strip()
    if not rationale and item_ratings:
        weak = [i for i in item_ratings if i["level"] <= 1]
        rationale = (
            f"{len([i for i in item_ratings if i['level'] >= 2])}/{len(item_ratings)} "
            f"items are Passable or better; {len(weak)} remain at or below Partially in place."
        )

    return {
        "score": score,
        "verdict": verdict,
        "rationale": rationale,
        "missing": missing[:MAX_FOLLOWUPS],
        "followups": followups,
        "items": item_ratings,
    }


def _default_reco(level: int, item: dict) -> str:
    """Deterministic recommendation to reach the next level, worded from the
    item's own next-level rubric definition."""
    name = item.get("name") or item.get("control_code") or "this item"
    if level >= 3:
        return f"Maintain and monitor \"{name}\"; keep coverage evidence current."
    _, defs = _item_rubric(item)
    nxt = level + 1
    return (
        f"To reach {LEVEL_LABELS[nxt]} for \"{name}\": {defs[nxt]}"
    )
