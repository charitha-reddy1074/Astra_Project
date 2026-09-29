"""
evaluation_metrics.py — Per-framework scoring profiles for deterministic evaluation.

Five layers applied in answer_questionnaire._finalize_scoring():
  Layer 1 : Domain weights              — higher-criticality domains contribute more
  Layer 2 : Control criticality tiers   — critical controls double their weight
  Layer 3 : Per-framework risk/maturity — tighter ISO bands, CIS IG labels, NIST Severe
  Layer 4 : Evidence quality rubric     — keyword-graded 0/20/40/75/100
  Layer 5 : Auto-flagging rules         — override/annotate score after finalize
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── Layer 1: Domain weight tables ────────────────────────────────────────────

_NIST_DOMAIN_WEIGHTS: Dict[str, float] = {
    # Short codes used in control IDs
    "GV": 5.0, "GOVERN": 5.0,
    "ID": 4.0, "IDENTIFY": 4.0,
    "PR": 4.0, "PROTECT": 4.0,
    "DE": 3.0, "DETECT": 3.0,
    "RS": 3.0, "RESPOND": 3.0,
    "RC": 2.0, "RECOVER": 2.0,
}

_ISO_DOMAIN_WEIGHTS: Dict[str, float] = {
    # ISO 27001:2022 clause numbers
    "5": 5.0, "A5": 5.0,   # Organisational controls
    "6": 3.0, "A6": 3.0,   # People controls
    "7": 3.0, "A7": 3.0,   # Physical controls
    "8": 5.0, "A8": 5.0,   # Technological controls
    "ORGANISATIONAL": 5.0, "PEOPLE": 3.0,
    "PHYSICAL": 3.0,        "TECHNOLOGICAL": 5.0,
}

# CIS domain_id is the control group number (1–18 as strings)
_CIS_DOMAIN_WEIGHTS: Dict[str, float] = {
    "1": 5.0, "2": 5.0, "3": 5.0, "4": 5.0, "5": 5.0, "6": 5.0,  # IG1 basics
    "7": 4.0, "8": 4.0, "9": 4.0, "10": 4.0, "11": 4.0,           # IG2 foundational
    "12": 3.0, "13": 3.0, "14": 3.0, "15": 3.0, "16": 3.0,        # Organisational
    "17": 2.0, "18": 2.0,                                         # Advanced / IG3
}

# ── Layer 2: Criticality tier detection ──────────────────────────────────────

CRITICALITY_MULT: Dict[str, float] = {
    "critical":      2.0,
    "standard":      1.0,
    "informational": 0.5,
}


def _nist_criticality(control_id: str) -> str:
    cid = str(control_id or "").upper()
    # Policy sub-controls (-P) or first numbered sub-control (-01 / .1)
    if re.search(r"-P$", cid) or re.search(r"[-.]01$", cid) or re.search(r"[-.]1$", cid):
        return "critical"
    return "standard"


def _iso_criticality(control_id: str) -> str:
    cid = str(control_id or "").strip()
    # High-impact technological controls (access, crypto, monitoring, logging)
    _critical_controls = {
        "5.2", "5.3", "5.4",                            # Roles, segregation, mgmt
        "8.2", "8.3", "8.4", "8.5", "8.7",             # Access, privileaged, auth
        "8.11", "8.15", "8.16", "8.20", "8.28",        # Logging, monitoring, filtering
    }
    # Match last two or three numeric parts (e.g. "8.5" at end of "A.8.5")
    norm = re.sub(r"^[A-Za-z.]+", "", cid).strip(".")
    for suffix in _critical_controls:
        if norm == suffix or norm.endswith("." + suffix):
            return "critical"
    return "standard"


def _cis_criticality(control_id: str) -> str:
    cid = str(control_id or "").strip()
    m = re.match(r"^(\d+)", cid)
    if m:
        top = int(m.group(1))
        if top <= 6:
            return "critical"
        if 7 <= top <= 11:
            return "standard"
        return "informational"
    return "standard"


# ── Layer 3: Risk/maturity classification bands ───────────────────────────────

_BandList = List[Dict[str, Any]]

# Generic 4-level risk bands — identical across all frameworks:
#   >= 80  Low       (green)
#   60-79  Medium    (amber)
#   40-59  High      (orange)
#   < 40   Critical  (red)
_GENERIC_BANDS: _BandList = [
    {"min": 80, "risk": "Low",      "maturity": "Optimizing"},
    {"min": 60, "risk": "Medium",   "maturity": "Managed"},
    {"min": 40, "risk": "High",     "maturity": "Defined"},
    {"min":  0, "risk": "Critical", "maturity": "Initial"},
]

# Organizational Assessment 0-3 maturity scale. Scores are on the achieved/possible
# percentage axis (each item worth 3 points); thresholds sit at the midpoints
# between the four levels (0 / 33.3 / 66.7 / 100):
#   >= 83.3  Strong               (level 3, green)
#   >= 50.0  Passable             (level 2, amber)
#   >= 16.7  Partially in place   (level 1, orange)
#   <  16.7  Not in place         (level 0, red)
_MARKET_BANDS: _BandList = [
    {"min": 83, "risk": "Low",      "maturity": "Strong"},
    {"min": 50, "risk": "Medium",   "maturity": "Passable"},
    {"min": 17, "risk": "High",     "maturity": "Partially in place"},
    {"min":  0, "risk": "Critical", "maturity": "Not in place"},
]


def market_level_from_percent(pct: float) -> int:
    """Map an achieved/possible percentage to the 0-3 maturity level."""
    if pct >= 83:
        return 3
    if pct >= 50:
        return 2
    if pct >= 17:
        return 1
    return 0


# Maturity choice label → 0-3 level (the assessor's single item rating).
# Numeric keys are accepted too, so an anchor stored as a bare "0".."3" (e.g. an
# AI prefill that read the 0-3 rubric, or a numeric manual entry) still resolves
# to its level instead of being ignored and dropping the item to the fallback.
_MARKET_LABEL_LEVEL: Dict[str, int] = {
    "not in place": 0,
    "partially in place": 1,
    "passable": 2,
    "strong": 3,
    "0": 0,
    "1": 1,
    "2": 2,
    "3": 3,
}


def _deployment_contradicts(revs: List[Dict[str, Any]]) -> bool:
    """True when a review set contains a deployment YES/NO answered NO — i.e. the
    control's own "Is it deployed?" question says it is not in place. The coverage
    question ("Is coverage complete…") is excluded: incomplete coverage is a
    partial state, not a contradiction of a Passable/Strong rating."""
    for r in revs:
        qt = str(r.get("question_text") or "").lower()
        if (
            str(r.get("question_type") or "").lower() == "yes_no"
            and str(r.get("response") or "").strip().upper() == "NO"
            and "deploy" in qt
            and "coverage" not in qt
        ):
            return True
    return False


def resolve_market_level(
    revs: List[Dict[str, Any]],
    ai_level: Optional[int] = None,
) -> Optional[int]:
    """Resolve one item's authoritative 0-3 level from its question reviews,
    applying the item-level precedence:

      1. the assessor's manual MULTI_CHOICE anchor (the xlsx single score),
         capped at Partially in place (1) when it contradicts the item's own
         deployment YES/NO (rated >= Passable but "deployed?" answered NO);
      2. the AI rubric-derived level (grounded in the Technology/Process rubric,
         scope coverage, tooling, and evidence class);
      3. a weighted roll-up of the supporting questions — CAPPED at Passable (2)
         when no operational evidence is attached, so an all-"yes" control with
         no evidence can no longer read as Strong.

    Returns None when the item was never answered (N/A — excluded from scoring).
    """
    answered = any(str(r.get("response") or "").strip() for r in revs)
    if not answered:
        return None

    anchor = None
    w_sum = w_tot = 0.0
    has_evidence = False
    for r in revs:
        w = float(r.get("weight") or 3)
        w_sum += float(r.get("score") or 0) * w
        w_tot += w
        if str(r.get("evidence_preview") or "").strip():
            has_evidence = True
        if str(r.get("question_type") or "").lower() == "multi_choice":
            lv = _MARKET_LABEL_LEVEL.get(str(r.get("response") or "").strip().lower())
            if lv is not None:
                anchor = lv

    if anchor is not None:
        # Contradiction cap: the assessor rated the item >= Passable (2) but their
        # own deployment YES/NO says it is NOT deployed. The two signals are
        # inconsistent, so the item is capped at Partially in place (1). This
        # mirrors report_generator.build_market_analytics so the persisted score
        # and the report analytics stay a single source of truth.
        if anchor >= 2 and _deployment_contradicts(revs):
            return min(anchor, 1)
        return anchor
    if ai_level is not None:
        return max(0, min(3, int(ai_level)))
    level = market_level_from_percent((w_sum / w_tot) if w_tot else 0.0)
    if not has_evidence:
        level = min(level, 2)  # Strong requires an anchor, AI rating, or evidence
    return level


def market_control_rollup(
    question_reviews: List[Dict[str, Any]],
    ai_levels: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Roll question reviews up to the achieved/possible score, strictly per
    the xlsx pattern: each item contributes a single 0-3 level resolved by
    :func:`resolve_market_level` (anchor -> AI rubric level -> capped fallback);
    items with no answered question are N/A and excluded from the denominator.
    Returns overall {achieved, possible, pct, level}.

    This is the single source of truth for the Market Assessment overall score so
    the Assessments view and the Reports view can never diverge.
    """
    ai_levels = ai_levels or {}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in question_reviews:
        groups.setdefault(r.get("control_id") or "", []).append(r)

    levels: List[int] = []
    for cid, revs in groups.items():
        if not cid:
            continue
        lv = resolve_market_level(revs, ai_levels.get(cid))
        if lv is not None:
            levels.append(lv)

    achieved = sum(levels)
    possible = len(levels) * 3
    pct = round(achieved / possible * 100, 1) if possible else 0.0
    return {"achieved": achieved, "possible": possible, "pct": pct, "level": market_level_from_percent(pct)}


# Deterministic recommendation-to-next-level, worded from the item's own next
# rubric definition (mirrors the xlsx Scoring sheet "Recommendations" column).
def market_recommendation(
    level: int,
    control_name: str,
    maturity_guide: Optional[Dict[str, Any]] = None,
) -> str:
    labels = ["Not in place", "Partially in place", "Passable", "Strong"]
    name = control_name or "this item"
    if level >= 3:
        return f'Maintain and monitor "{name}"; keep coverage evidence current.'
    nxt = min(level + 1, 3)
    definition = ""
    node = (maturity_guide or {}).get(f"level_{nxt}")
    if isinstance(node, dict):
        definition = str(node.get("definition") or "")
    if definition:
        return f'To reach {labels[nxt]} for "{name}": {definition}'
    return (
        f'Prioritise closing gaps in "{name}" to reach {labels[nxt]}; '
        f"document, enforce, and evidence coverage across the defined scope."
    )


def _classify(score: float, bands: _BandList) -> Tuple[str, str]:
    for band in sorted(bands, key=lambda b: -b["min"]):
        if score >= band["min"]:
            return band["risk"], band["maturity"]
    last = bands[-1]
    return last["risk"], last["maturity"]


# ── Layer 4: Evidence quality rubric ─────────────────────────────────────────

_TEMPLATE_MARKERS = [
    "this is a template", "lorem ipsum", "placeholder", "sample document",
    "version 1.0", "draft", "iso/iec 27001", "nist sp 800", "cis controls",
    "framework document", "standard template", "[organization name]", "<<",
]

_GENERIC_OPERATIONAL_KW = [
    "audit log", "access log", "event log", "system log",
    "asset inventory", "incident ticket", "access review", "scan report",
    "vulnerability", "patch report", "configuration", "screenshot",
    "monitoring alert", "siem", "firewall rule", "timestamp", "record",
]

_GENERIC_POLICY_KW = [
    "policy", "procedure", "standard", "process", "guideline",
    "plan", "protocol", "framework document",
]

_FW_OP_KW: Dict[str, List[str]] = {
    "nist-csf-2-0": [
        "audit log", "asset inventory", "incident ticket", "access review",
        "scan report", "vulnerability assessment", "patch management",
        "firewall rule", "siem alert", "risk register",
    ],
    "iso-27001-2022": [
        "risk register", "soa", "statement of applicability", "nonconformity",
        "internal audit report", "management review", "corrective action",
        "vulnerability assessment", "penetration test", "access review log",
    ],
    "cis-controls-v8-1-2": [
        "vulnerability scan", "patch report", "siem alert",
        "configuration baseline", "mfa enrollment", "asset register",
        "log review", "endpoint detection", "secure configuration",
    ],
    # Organizational Assessment: coverage/operational evidence proving a control
    # actually runs across the defined scope (the hinge between Passable and
    # Strong on the 0-3 scale).
    "market-assessment": [
        "coverage report", "deployment report", "enrolled", "enrollment",
        "audit log", "access review", "certification campaign", "scan report",
        "ticket", "dashboard", "screenshot", "configuration export",
        "inventory", "rule set", "recertification", "sla", "review cadence",
    ],
    # SOC 2 Type 1: design-and-implementation-grade artefacts only. Operating
    # evidence (log samples, ticket populations, exception reports) belongs to a
    # Type 2 engagement, so it is deliberately absent from this list and the
    # compliance layer caps a control graded on period-only evidence.
    "soc2-type-1": [
        "walkthrough", "architecture diagram", "configuration export",
        "current configuration", "as-designed", "as designed", "org chart",
        "charter", "revision history", "approval record", "approved",
        "effective date", "screenshot", "control matrix", "design documentation",
        "soc report", "system description",
    ],
}

_FW_POL_KW: Dict[str, List[str]] = {
    "nist-csf-2-0":        ["cybersecurity policy", "risk management plan", "incident response plan"],
    "iso-27001-2022":      ["information security policy", "isms", "risk treatment plan"],
    "cis-controls-v8-1-2": ["asset management policy", "patch management procedure", "access control policy"],
    "market-assessment":   ["standard", "policy", "procedure", "baseline document"],
    "soc2-type-1": [
        "information security policy", "acceptable use policy",
        "access control policy", "change management policy",
        "incident response plan", "risk assessment", "code of conduct",
        "vendor management policy", "data retention policy",
    ],
}


def score_evidence_quality(
    response: Any,
    evidence_preview: str,
    framework_key: str = "nist-csf-2-0",
) -> float:
    """
    Grade evidence quality on a 0–100 scale using keyword heuristics.

    Grades:
      100 — operational   (logs, scan reports, screenshots, timestamps)
       75 — policy_implemented (policy/procedure document with relevant keywords)
       40 — policy_only   (template/framework document or very generic content)
       20 — filename_only (path present but no preview text)
        0 — missing       (no file submitted)
    """
    resp    = str(response or "").strip()
    preview = str(evidence_preview or "").lower()

    if not resp:
        return 0.0

    if not preview:
        return 20.0  # filename_only

    # Detect template/framework placeholder documents
    if any(marker in preview for marker in _TEMPLATE_MARKERS):
        return 40.0  # policy_only

    fw = str(framework_key or "").lower()
    if "market" in fw:
        fw_key = "market-assessment"
    else:
        fw_key = next((k for k in _FW_OP_KW if k in fw), "nist-csf-2-0")

    op_kw  = _FW_OP_KW.get(fw_key, []) + _GENERIC_OPERATIONAL_KW
    pol_kw = _FW_POL_KW.get(fw_key, []) + _GENERIC_POLICY_KW

    if any(kw in preview for kw in op_kw):
        return 100.0  # operational

    if any(kw in preview for kw in pol_kw):
        return 75.0   # policy_implemented

    # Content present but no matching keywords — grade by length
    if len(preview) > 300:
        return 60.0
    if len(preview) > 50:
        return 40.0
    return 30.0


# ── Layer 5: Auto-flagging rules ─────────────────────────────────────────────

# The lowest maturity rung, expressed across every question type (framework
# 1-5 "Initial" and the 0-3 "Not in place"/"0").
_LOWEST_MATURITY_LABELS = {"initial", "not in place", "0"}


def _at_lowest_maturity(r: Dict[str, Any]) -> bool:
    """True when a single question review sits at the lowest rung of its scale,
    regardless of question type (see Rule 3)."""
    qt = str(r.get("question_type") or "").lower()
    resp = str(r.get("response") or "").strip().lower()
    if not resp:
        return False
    if qt == "maturity_rating":
        return resp in _LOWEST_MATURITY_LABELS
    if qt in ("yes_no", "yes_no_with_detail"):
        return resp not in ("yes", "y", "true", "1")
    if qt == "scale_1_5":
        return resp in ("1", "1.0")
    return False


def apply_autoflag_rules(
    result: Dict[str, Any],
    question_reviews: List[Dict[str, Any]],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Mutate *result* in-place based on five deterministic rules, then return it.
    Rules run after _finalize_scoring() so they can override risk_rating / maturity.
    """
    gaps             = list(result.get("gaps") or [])
    current_risk     = result.get("overall_risk_rating", "")
    bands            = profile.get("bands", _GENERIC_BANDS)
    criticality_fn: Callable[[str], str] = profile.get("criticality_fn", lambda _: "standard")

    # ── Rule 1: critical_control_failed → flag it (do NOT override the risk) ──
    # The overall risk rating must stay aligned with the score band so the gauge
    # colour matches the number; we only surface the failure as a gap note.
    critical_failed = any(
        criticality_fn(r.get("control_id", "")) == "critical" and float(r.get("score") or 100) < 40
        for r in question_reviews
    )
    if critical_failed:
        gaps.append("A critical-tier control scored below threshold — prioritise remediation.")

    # ── Rule 2: evidence_all_missing ─────────────────────────────────────
    ev_qs = [r for r in question_reviews if r.get("question_type") == "evidence_upload"]
    if ev_qs and all(float(r.get("score") or 0) == 0 for r in ev_qs):
        gaps.append("No documentary evidence provided for any evidence-upload question.")

    # ── Rule 3: maturity_bottleneck → cap maturity at Defined ────────────
    # A control sitting at the LOWEST rung of its own scale is a bottleneck no
    # matter which question type expresses it. Applying this only to
    # maturity_rating let a control dodge the cap by using a Yes/No or Scale 1-5
    # question instead, so the check spans all three types:
    #   maturity_rating → "Initial" (or the market 0-3 "Not in place"/"0")
    #   yes_no          → answered NO
    #   scale_1_5       → rated 1 (the lowest rating)
    if any(_at_lowest_maturity(r) for r in question_reviews):
        result["maturity_level"] = "Defined"
        gaps.append("Maturity bottleneck: at least one control is at the lowest maturity level.")

    # ── Rule 4: yes_no_majority_no ────────────────────────────────────────
    yn_qs = [r for r in question_reviews if r.get("question_type") in ("yes_no", "yes_no_with_detail")]
    if yn_qs:
        no_count = sum(
            1 for r in yn_qs
            if str(r.get("response") or "").lower() not in ("yes", "y", "true", "1")
        )
        if no_count / len(yn_qs) > 0.6:
            gaps.append(
                f"Majority of yes/no controls not confirmed as implemented "
                f"({no_count}/{len(yn_qs)})."
            )

    # ── Rule 5: free_text_shallow ─────────────────────────────────────────
    ft_qs = [
        r for r in question_reviews
        if r.get("question_type") not in (
            "yes_no", "yes_no_with_detail", "maturity_rating", "evidence_upload",
            "scale_1_5", "multi_choice",
            "YES_NO", "SCALE_1_5", "MULTI_CHOICE", "EVIDENCE_UPLOAD",
        )
    ]
    if ft_qs:
        answered = [str(r.get("response") or "").split() for r in ft_qs if str(r.get("response") or "").strip()]
        if answered:
            avg_words = sum(len(w) for w in answered) / len(answered)
            if avg_words < 15:
                gaps.append("Open-ended responses lack sufficient detail for audit confidence.")

    result["gaps"] = gaps
    return result


# ── Profile factory ───────────────────────────────────────────────────────────

_Profile = Dict[str, Any]


def get_framework_profile(
    framework_key: str,
    questionnaire_data: Optional[Dict[str, Any]] = None,
) -> _Profile:
    """
    Return the scoring profile for the given framework key.

    For custom frameworks, reads `scoring_profile` from questionnaire_data if present.
    Falls back to NIST CSF 2.0 as the safe default.
    """
    fw = str(framework_key or "").lower()

    # ── Custom framework: read from questionnaire JSON ────────────────────
    if questionnaire_data:
        custom_sp = questionnaire_data.get("scoring_profile")
        if custom_sp:
            bands = custom_sp.get("risk_bands", _GENERIC_BANDS)
            ev_kw = custom_sp.get("evidence_keywords", {})
            return {
                "key":            framework_key,
                "domain_weights": {},          # all domains default to 3
                "criticality_fn": lambda _: "standard",
                "bands":          bands,
                "classify":       lambda s: _classify(s, bands),
                "evidence_kw":    ev_kw,
            }

    # ── Organizational Assessment (0-3 maturity scale) ────────────────────────
    if "market" in fw:
        return {
            "key":            "market-assessment",
            "domain_weights": {},          # equal weighting → achieved/possible roll-up
            "criticality_fn": lambda _: "standard",
            "bands":          _MARKET_BANDS,
            "classify":       lambda s: _classify(s, _MARKET_BANDS),
            "evidence_kw":    {},
            "scale":          "0-3",
        }

    # ── ISO 27001:2022 ────────────────────────────────────────────────────
    if "iso" in fw or "27001" in fw:
        return {
            "key":            "iso-27001-2022",
            "domain_weights": _ISO_DOMAIN_WEIGHTS,
            "criticality_fn": _iso_criticality,
            "bands":          _GENERIC_BANDS,
            "classify":       lambda s: _classify(s, _GENERIC_BANDS),
            "evidence_kw":    _FW_OP_KW.get("iso-27001-2022", {}),
        }

    # ── CIS Controls ─────────────────────────────────────────────────────
    if "cis" in fw:
        return {
            "key":            "cis-controls-v8-1-2",
            "domain_weights": _CIS_DOMAIN_WEIGHTS,
            "criticality_fn": _cis_criticality,
            "bands":          _GENERIC_BANDS,
            "classify":       lambda s: _classify(s, _GENERIC_BANDS),
            "evidence_kw":    _FW_OP_KW.get("cis-controls-v8-1-2", {}),
        }

    # ── NIST CSF 2.0 (default) ────────────────────────────────────────────
    return {
        "key":            "nist-csf-2-0",
        "domain_weights": _NIST_DOMAIN_WEIGHTS,
        "criticality_fn": _nist_criticality,
        "bands":          _GENERIC_BANDS,
        "classify":       lambda s: _classify(s, _GENERIC_BANDS),
        "evidence_kw":    _FW_OP_KW.get("nist-csf-2-0", {}),
    }


