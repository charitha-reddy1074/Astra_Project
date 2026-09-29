"""Seed the CyberAI platform with a full, realistic dataset.

Replaces scripts/setup_demo_assessments.py, which was hardcoded to Windows paths
(C:/Users/chari/...) and a stale framework UUID, so it could not run here.

What it produces:
  * A staffed user directory — one organization owner, two managers, one auditor
    per organization, and one evidence contributor per organization, with full
    profiles (phone / department / job title / notes).
  * One or more assessments for EVERY organization in ORGANIZATIONS below,
    spread across ALL five frameworks so nothing sits unused.
  * Real engagement evidence attached from data/evidence/* and
    data/tmp_demo/sample_evidence.
  * AI pre-fill + AI control rating on the evidence-backed engagements.
  * Deterministic, organization-specific maturity answers everywhere else, so
    scores form a realistic spread instead of all landing on the same number.
  * Every assessment submitted and scored, with findings and reports generated.

Run from the project root with the backend already up on :8000:
    .venv/bin/python scripts/seed_platform_demo.py [--reset] [--no-ai]

  --reset   delete existing assessments and seeded users first
  --no-ai   skip Groq pre-fill / rating (fast; answers are still generated)
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
HDRS = {"X-User-Email": "avery.sinclair@cyberai.io", "X-User-Role": "org_owner"}
TIMEOUT = 1800

# The single organization, split into the business units that own engagements.
# `market_id` / `market_label` on an assessment are the legacy column names for
# exactly this value; the user records below use the `organization` field.
ORGANIZATIONS = {
    "asia":          "ASIA",
    "mea":           "MEA",
    "denmark":       "Denmark",
    "france":        "France",
    "spain":         "Spain",
    "mexico":        "Mexico",
    "iom_bu":        "IOM BU",
    "united_states": "United States",
}

# Maturity profile per market → drives MULTI_CHOICE / YES_NO answers so each
# market lands on a genuinely different score.
MATURITY = {
    "france":        "strong",
    "united_states": "strong",
    "asia":          "passable",
    "iom_bu":        "passable",
    "denmark":       "partial",
    "mea":           "partial",
    "spain":         "mixed",
    "mexico":        "weak",
}
LADDER = {
    "strong":  ["Strong", "Strong", "Passable"],
    "passable": ["Passable", "Passable", "Strong", "Partially in place"],
    "partial": ["Partially in place", "Partially in place", "Passable"],
    "mixed":   ["Passable", "Partially in place", "Strong", "Not in place"],
    "weak":    ["Not in place", "Partially in place", "Not in place"],
}
# Values must be UPPERCASE: prefill.map_prefill_answer emits "YES"/"NO"/"PARTIAL"
# as "the EXACT form the app's UI/scoring expect", and the UI highlights the
# matching button. Lowercase values save and count as answered but show as
# unselected.
YESNO = {
    "strong":   ["YES", "YES", "YES", "PARTIAL"],
    "passable": ["YES", "YES", "PARTIAL"],
    "partial":  ["PARTIAL", "YES", "PARTIAL", "NO"],
    "mixed":    ["YES", "PARTIAL", "NO", "YES"],
    "weak":     ["NO", "PARTIAL", "NO"],
}

EVIDENCE_SETS = {
    "iam":   ROOT / "data" / "evidence" / "multi-set-d3bb1229-fb6",
    "cloud": ROOT / "data" / "evidence" / "multi-set-dc39b7f6-2a1",
    "mixed": ROOT / "data" / "evidence" / "multi-set-43b32e91-6fd",
    "cis":   ROOT / "data" / "evidence" / "multi-cis-controls-v8-1-2-7",
    "iso":   ROOT / "data" / "evidence" / "multi-iso-27001-2022-b",
    "sample": ROOT / "data" / "tmp_demo" / "sample_evidence",
}

USERS = [
    ("Avery Sinclair",  "avery.sinclair@cyberai.io",  "org_owner",          None,           "Compliance Office",   "Director of GRC",      "Administers users, roles and the framework catalog"),
    ("Priya Raman",     "priya.raman@cyberai.io",     "compliance_manager", None,           "Security Assurance",  "Head of Compliance",   "Owns the global assessment programme"),
    ("Daniel Okafor",   "daniel.okafor@cyberai.io",   "security_manager",   None,           "Security Assurance",  "Principal Security",   "Reviews engagement submissions"),
    ("Mark Lefevre",    "mark.lefevre@cyberai.io",    "auditor",            "France",        "Cyber Risk",          "Senior Auditor",       "EU organizations"),
    ("Karen Tan",       "karen.tan@cyberai.io",       "auditor",            "ASIA",          "Cyber Risk",          "Senior Auditor",       "APAC organizations"),
    ("John Sorensen",   "john.sorensen@cyberai.io",   "auditor",            "Denmark",       "Cyber Risk",          "Auditor",              "Nordics"),
    ("Ana Delgado",     "ana.delgado@cyberai.io",     "auditor",            "Mexico",        "Cyber Risk",          "Auditor",              "LatAm organizations"),
    ("Sam Whitfield",   "sam.whitfield@cyberai.io",   "auditor",            "United States", "Cyber Risk",          "Senior Auditor",       "IOM / US"),
    ("Omar Hassan",     "omar.hassan@cyberai.io",     "auditor",            "MEA",           "Cyber Risk",          "Senior Auditor",       "MEA organizations"),
    ("Sofia Navarro",   "sofia.navarro@cyberai.io",   "auditor",            "Spain",         "Cyber Risk",          "Auditor",              "Iberian organizations"),
    ("James Fletcher",  "james.fletcher@cyberai.io",  "auditor",            "IOM BU",        "Cyber Risk",          "Auditor",              "IOM business unit"),
]
# organization label → (name, department, job title, contributor role)
CONTRIBUTOR_META = {
    "ASIA":          ("Wei Chen",        "IT Operations",   "Regional IT Director",  "evidence_contributor"),
    "MEA":           ("Layla Haddad",    "IT Operations",   "Head of Infrastructure","evidence_contributor"),
    "Denmark":       ("Freja Nielsen",   "IT",              "IT Manager",            "team_member"),
    "France":        ("Julien Moreau",   "IT",              "Head of IT Security",   "team_member"),
    "Spain":         ("Carmen Ortiz",    "IT",              "IT Manager",            "evidence_contributor"),
    "Mexico":        ("Diego Ramirez",   "IT",              "IT Coordinator",        "evidence_contributor"),
    "IOM BU":        ("Rachel Adams",    "Business Unit IT","BU Technology Lead",    "team_member"),
    "United States": ("Brian Cole",      "Corporate IT",    "Director of Security Ops","evidence_contributor"),
}


def api(method, path, **kw):
    kw.setdefault("timeout", TIMEOUT)
    hdrs = dict(HDRS)
    hdrs.update(kw.pop("headers", {}))
    return requests.request(method, f"{BASE}{path}", headers=hdrs, **kw)


def hdr(msg):
    print(f"\n{'=' * 70}\n  {msg}\n{'=' * 70}")


def note(msg):
    print(f"  {msg}", flush=True)


# ─────────────────────────── users ───────────────────────────

def seed_users(reset: bool) -> None:
    hdr("USERS — staffing the directory")
    if reset:
        # GET /users only returns active users; also delete by known emails so
        # inactive seeded records don't block re-creation with 409.
        existing = api("get", "/users").json()
        active_emails = {u["email"] for u in existing if u["email"].endswith("@cyberai.io")}
        for email in active_emails:
            api("delete", f"/users/{email}")
        known_emails = {email for _, email, *_ in USERS}
        known_emails |= {f"{mid}.contributor@cyberai.io" for mid in ORGANIZATIONS}
        for email in known_emails - active_emails:
            api("delete", f"/users/{email}")
        note(f"removed {len(active_emails)} active + {len(known_emails - active_emails)} inactive seeded users")

    made = 0
    for name, email, role, organization, dept, title, notes in USERS:
        body = {"name": name, "email": email, "role": role, "organization": organization,
                "department": dept, "job_title": title, "notes": notes,
                "phone": f"+1-555-{random.randint(1000, 9999)}"}
        r = api("post", "/users", json=body)
        made += r.status_code in (200, 201)
        if r.status_code not in (200, 201, 409):
            note(f"  ! {email} -> {r.status_code} {r.text[:120]}")

    for mid, label in ORGANIZATIONS.items():
        nm, dept, title, role = CONTRIBUTOR_META[label]
        body = {"name": nm, "email": f"{mid}.contributor@cyberai.io", "role": role,
                "organization": label, "department": dept, "job_title": title,
                "notes": f"Evidence contributor for {label}",
                "phone": f"+1-555-{random.randint(1000, 9999)}"}
        r = api("post", "/users", json=body)
        made += r.status_code in (200, 201)
        if r.status_code not in (200, 201, 409):
            note(f"  ! {mid} contributor -> {r.status_code} {r.text[:120]}")

    stats = api("get", "/users-stats").json()
    note(f"created {made} users · directory now: {stats}")


# ─────────────────────────── frameworks ───────────────────────────

def load_frameworks() -> dict:
    fws = api("get", "/frameworks").json()
    out = {}
    for f in fws:
        detail = api("get", f"/frameworks/{f['id']}").json()
        out[f["code"]] = detail
    return out


def questions_for(detail: dict, domain_codes: list[str] | None) -> list[dict]:
    """Flatten framework → questions, as AssessmentWorkspace.buildQuestions does."""
    qs = []
    for d in detail.get("domains", []):
        if domain_codes and d["code"] not in domain_codes:
            continue
        cats = d.get("categories") or [{"controls": d.get("controls", [])}]
        for cat in cats:
            for ctl in cat.get("controls", []) or []:
                for q in ctl.get("questions", []) or []:
                    qs.append({"id": q["id"],
                               "type": str(q.get("question_type") or "YES_NO").upper(),
                               "choices": q.get("choices"),
                               "control_code": ctl.get("code")})
    return qs


# ─────────────────────────── evidence ───────────────────────────

def upload_evidence(aid: str, folder: Path, limit: int, doc_type: str = "evidence") -> int:
    if not folder.exists():
        note(f"  evidence folder missing: {folder}")
        return 0
    # Prefer real source documents; skip the .txt preview sidecars.
    files = [p for p in sorted(folder.iterdir())
             if p.is_file() and not p.name.endswith(".txt")][:limit]
    if not files:
        files = [p for p in sorted(folder.iterdir()) if p.is_file()][:limit]
    if not files:
        return 0
    handles, payload = [], []
    try:
        for p in files:
            fh = p.open("rb")
            handles.append(fh)
            payload.append(("files", (p.name, fh, "application/octet-stream")))
        r = api("post", f"/assessments/{aid}/evidence-bulk",
                files=payload, data={"doc_type": doc_type})
    finally:
        for fh in handles:
            fh.close()
    if r.status_code not in (200, 201):
        note(f"  ! evidence upload -> {r.status_code} {r.text[:160]}")
        return 0
    body = r.json()
    note(f"  evidence: {body.get('count')} files, text extracted from {body.get('text_extracted')}")
    return body.get("count") or 0


# ─────────────────────────── answers ───────────────────────────

FREETEXT = {
    "strong": ("Fully implemented and documented. The capability is deployed across all in-scope "
               "assets, owned by a named team, reviewed on a defined cadence, and evidenced by "
               "current reports retained in the GRC platform."),
    "passable": ("Implemented and operating across most in-scope assets with documented procedures. "
                 "Coverage gaps remain in a small number of legacy systems, tracked on the "
                 "remediation plan with target dates."),
    "partial": ("Partially in place. The process exists but is informal and inconsistently applied; "
                "coverage is incomplete and evidence is produced on request rather than routinely."),
    "mixed": ("Implemented for core systems with documented ownership; peripheral and third-party "
              "managed estates are not yet in scope and are being onboarded this year."),
    "weak": ("Not currently in place. No defined owner, tooling, or repeatable process exists; this "
             "has been logged as a gap and prioritised for the next planning cycle."),
}


def answer_all(aid: str, qs: list[dict], profile: str, already: set[str]) -> int:
    rng = random.Random(f"{aid}:{profile}")
    ladder, yesno = LADDER[profile], YESNO[profile]
    posted = 0
    for q in qs:
        if q["id"] in already:
            continue
        t = q["type"]
        if t in ("YES_NO",):
            val = rng.choice(yesno)
        elif t in ("MULTI_CHOICE", "MULTICHOICE"):
            choices = q["choices"] or ladder
            pick = rng.choice(ladder)
            val = next((c for c in choices if str(c).strip().lower() == pick.lower()), rng.choice(list(choices)))
        elif t in ("SCALE_1_5", "SCALE"):
            val = {"strong": "5", "passable": "4", "partial": "3", "mixed": "3", "weak": "2"}[profile]
        elif t in ("EVIDENCE_UPLOAD", "EVIDENCE"):
            # Graded by score_evidence_quality against the uploaded evidence text,
            # so name the artefact rather than writing filler prose.
            val = rng.choice([
                "iam_policy.docx", "asset_register.xlsx", "patch_compliance_report.xlsx",
                "auth_logs.docx", "role_matrix.xlsx",
            ])
        else:
            val = FREETEXT[profile]
        r = api("post", f"/assessments/{aid}/responses",
                json={"question_id": q["id"], "control_id": q["control_code"],
                      "response_value": val, "notes": "seeded"})
        posted += r.status_code in (200, 201)
    return posted


# ─────────────────────────── engagements ───────────────────────────

PLAN = [
    # (market_id, framework_code, domain_codes, evidence_key, evidence_count, do_ai, name)
    ("france",        "MARKET_ASSESSMENT",    ["IAM"],                 "iam",    8, True,  "France — Identity & Directory Review"),
    ("asia",          "MARKET_ASSESSMENT",    ["CLOUD", "APP"],        "cloud", 10, True,  "ASIA — Cloud & Application Security"),
    ("spain",         "MARKET_ASSESSMENT",    ["DATA", "TPRM"],        "mixed",  8, True,  "Spain — Data Protection & Third Parties"),
    ("denmark",       "MARKET_ASSESSMENT",    ["EP", "M365"],          "sample", 6, False, "Denmark — Endpoint & Email Security"),
    ("mexico",        "MARKET_ASSESSMENT",    ["AI", "DR", "NET"],     None,     0, False, "Mexico — Network, AI & Response Readiness"),
    ("united_states", "NIST_CSF_2.0",         ["GV", "ID"],            "sample", 6, False, "United States — NIST CSF Governance & Identify"),
    ("mea",           "ISO/IEC_27001:2022",   ["A.6", "A.7"],          "iso",    2, False, "MEA — ISO 27001 People & Physical Controls"),
    ("iom_bu",        "CIS_CONTROLS_V8.1.2",  None,                    "cis",    8, False, "IOM BU — CIS Controls Baseline"),
    ("asia",          "PCI_DSS",              None,                    None,     0, False, "ASIA — PCI DSS 4.0 Cardholder Data Review"),
    ("france",        "MARKET_ASSESSMENT",    None,                    None,     0, False, "France — Full Market Assessment (all domains)"),
]

REVIEWER_BY_ORGANIZATION = {
    "France": "mark.lefevre@cyberai.io", "ASIA": "karen.tan@cyberai.io",
    "Denmark": "john.sorensen@cyberai.io", "Mexico": "ana.delgado@cyberai.io",
    "United States": "sam.whitfield@cyberai.io", "Spain": "mark.lefevre@cyberai.io",
    "MEA": "karen.tan@cyberai.io", "IOM BU": "sam.whitfield@cyberai.io",
}


def run_engagement(spec, frameworks, use_ai: bool) -> dict:
    mid, fw_code, domain_codes, ev_key, ev_n, wants_ai, name = spec
    label = ORGANIZATIONS[mid]
    profile = MATURITY[mid]
    detail = frameworks.get(fw_code)
    if not detail:
        note(f"SKIP {name}: framework {fw_code} not loaded")
        return {}

    hdr(f"{name}   [{fw_code} · {label} · {profile}]")

    # domain scoping uses the "<framework_id>:<domain_code>" key the UI sends
    valid = {d["code"] for d in detail["domains"]}
    codes = [c for c in (domain_codes or []) if c in valid]
    if domain_codes and not codes:
        note(f"  ! none of {domain_codes} exist in {fw_code}; using all domains "
             f"(available: {sorted(valid)})")
        codes = []
    selected = [f"{detail['id']}:{c}" for c in codes]

    r = api("post", "/assessments", json={
        "name": name,
        "description": f"{label} engagement against {detail['name']}.",
        "framework_ids": [detail["id"]],
        "selected_domains": selected,
        "organization": label,
        "assigned_to": REVIEWER_BY_ORGANIZATION.get(label),
        "market_id": mid,
        "market_label": label,
    })
    if r.status_code not in (200, 201):
        note(f"  ! create -> {r.status_code} {r.text[:200]}")
        return {}
    aid = r.json()["id"]
    note(f"  created {aid}")

    gq = api("post", f"/assessments/{aid}/generate-questionnaire", json={})
    total = gq.json().get("total_questions") if gq.ok else "?"
    note(f"  questionnaire: {total} questions")

    if ev_key and ev_n:
        upload_evidence(aid, EVIDENCE_SETS[ev_key], ev_n,
                        doc_type="policy" if ev_key in ("iam", "iso") else "evidence")

    prefilled = set()
    if use_ai and wants_ai and ev_key:
        t0 = time.time()
        pf = api("post", f"/assessments/{aid}/prefill", json={})
        if pf.ok:
            body = pf.json()
            summ = body.get("summary", {})
            note(f"  prefill: filled={summ.get('filled')}/{summ.get('total')} "
                 f"in {time.time() - t0:.0f}s {'ERR ' + str(body.get('error')) if body.get('error') else ''}")
            cur = api("get", f"/assessments/{aid}").json()
            prefilled = {x.get("question_id") for x in (cur.get("responses") or [])}
        else:
            note(f"  ! prefill -> {pf.status_code} {pf.text[:160]}")

    qs = questions_for(detail, codes or None)
    posted = answer_all(aid, qs, profile, prefilled)
    note(f"  answers: {posted} posted (+{len(prefilled)} pre-filled) of {len(qs)} in scope")

    if use_ai and wants_ai and ev_key:
        rt = api("post", f"/assessments/{aid}/ai/rate-controls", json={})
        if rt.ok:
            b = rt.json()
            note(f"  AI rating: {b.get('summary') or list(b.keys())}")
        else:
            note(f"  ! rate-controls -> {rt.status_code} {rt.text[:160]}")

    sub = api("post", f"/assessments/{aid}/submit", json={})
    note(f"  submit -> {sub.status_code} {sub.json().get('status') if sub.ok else sub.text[:120]}")

    sc = api("post", f"/assessments/{aid}/score", json={})
    if sc.ok:
        b = sc.json()
        note(f"  SCORE {b.get('overall_score')}%  maturity={b.get('maturity_level')}  "
             f"findings={b.get('findings_count')}")
    else:
        note(f"  ! score -> {sc.status_code} {sc.text[:200]}")

    rv = api("get", f"/assessments/{aid}/report-view")
    note(f"  report-view -> {rv.status_code}")
    final = api("get", f"/assessments/{aid}").json()
    note(f"  final: status={final.get('status')} score={final.get('overall_score')}")
    return {"id": aid, "name": name, "organization": label, "framework": fw_code,
            "score": final.get("overall_score"), "status": final.get("status")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--no-ai", action="store_true")
    args = ap.parse_args()

    try:
        api("get", "/frameworks", timeout=15)
    except Exception as e:
        sys.exit(f"Backend not reachable at {BASE}: {e}")

    if args.reset:
        hdr("RESET — clearing existing assessments")
        for a in api("get", "/assessments").json():
            api("delete", f"/assessments/{a['id']}")
        note("assessments cleared")

    seed_users(args.reset)
    frameworks = load_frameworks()
    note(f"frameworks loaded: {', '.join(sorted(frameworks))}")

    results = []
    for spec in PLAN:
        try:
            got = run_engagement(spec, frameworks, use_ai=not args.no_ai)
            if got:
                results.append(got)
        except Exception as e:
            note(f"  !! engagement failed: {e}")

    hdr("SUMMARY")
    print(f"  {'organization':16}{'framework':22}{'status':12}{'score':>7}   name")
    for r in results:
        print(f"  {r['organization']:16}{r['framework']:22}{str(r['status']):12}"
              f"{str(r['score']):>7}   {r['name'][:44]}")
    scored = [r for r in results if isinstance(r.get("score"), (int, float)) and r["score"] > 0]
    print(f"\n  {len(results)} engagements · {len(scored)} with a non-zero score")
    covered = {r["organization"] for r in results}
    missing = set(ORGANIZATIONS.values()) - covered
    print(f"  organizations covered: {len(covered)}/{len(ORGANIZATIONS)}"
          + (f"  MISSING: {sorted(missing)}" if missing else "  (all)"))
    fw_covered = {r["framework"] for r in results}
    print(f"  frameworks used: {len(fw_covered)}/5 -> {sorted(fw_covered)}")


if __name__ == "__main__":
    main()

