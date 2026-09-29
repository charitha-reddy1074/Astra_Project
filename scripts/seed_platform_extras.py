"""Exercise the platform features that the main seed does not touch, so no
screen in the UI renders empty.

Run AFTER scripts/seed_platform_demo.py:
    .venv/bin/python scripts/seed_platform_extras.py

Covers:
  * Document requests — raised, fulfilled by the contributor, then
    accepted/rejected (populates the Required Documents tab, the contributor's
    My Work view, and the Inbox).
  * Pre-assessment questionnaire — default template download + a completed
    custom upload against one assessment.
  * Assessment status variety — a draft, an in-progress and an in-review
    engagement so the dashboard tiles are not "10 completed / 0 everything else".
  * Knowledge Base > Manage (catalog admin) — create, edit, duplicate and move a
    control, producing real audit-history rows.
  * Ask-CyberAI chat — a grounded question per framework.
"""
from __future__ import annotations

import io
import json
import random
import sys

import requests

BASE = "http://127.0.0.1:8000"
ADMIN = {"X-User-Email": "avery.sinclair@cyberai.io", "X-User-Role": "org_owner"}
TIMEOUT = 600


def api(method, path, **kw):
    kw.setdefault("timeout", TIMEOUT)
    h = dict(ADMIN); h.update(kw.pop("headers", {}))
    return requests.request(method, f"{BASE}{path}", headers=h, **kw)


def hdr(m): print(f"\n{'=' * 68}\n  {m}\n{'=' * 68}")
def note(m): print(f"  {m}", flush=True)


EVIDENCE_TYPES = [
    ("policy",        "Information Security Policy (current, approved)"),
    ("configuration", "MFA / SSO configuration export"),
    ("records",       "Quarterly access review records"),
    ("report",        "Latest vulnerability scan report"),
    ("procedure",     "Joiner-Mover-Leaver procedure"),
]


def document_requests(assessments):
    hdr("DOCUMENT REQUESTS — raise, fulfil, review")
    for a in assessments[:4]:
        aid = a["id"]
        org = a.get("organization") or a.get("market_label") or "the organization"
        items = [{"evidence_type": et, "note": desc,
                  "domain_code": None, "control_code": None}
                 for et, desc in random.sample(EVIDENCE_TYPES, 3)]
        r = api("post", f"/assessments/{aid}/document-requests",
                json={"items": items, "requested_by": "priya.raman@cyberai.io",
                      "note": f"Evidence pack required for the {org} engagement."})
        if not r.ok:
            note(f"  ! create -> {r.status_code} {r.text[:160]}"); continue
        reqs = api("get", f"/assessments/{aid}/document-requests").json()
        rows = reqs if isinstance(reqs, list) else reqs.get("requests", [])
        note(f"{org}: {len(rows)} requests raised")

        # The contributor fulfils the first two, the reviewer accepts one and
        # rejects one.
        for i, req in enumerate(rows[:2]):
            rid = req.get("id")
            content = (f"{req.get('evidence_type', 'evidence').upper()} — {org}\n"
                       f"Provided by the evidence contributor in response to request {rid}.\n"
                       "Approved 2026-02-01. Document owner: IT Security. Review cadence: annual.\n")
            files = [("files", (f"{org.lower().replace(' ', '_')}_"
                                f"{req.get('evidence_type', 'evidence')}.txt",
                                io.BytesIO(content.encode()), "text/plain"))]
            pr = api("post", f"/assessments/{aid}/document-requests/{rid}/provide",
                     files=files, data={"provided_by": "contributor@cyberai.io"})
            action = "accept" if i == 0 else "reject"
            rv = api("post", f"/assessments/{aid}/document-requests/{rid}/review",
                     json={"action": action,
                           "note": "Meets the requirement." if action == "accept"
                                   else "Superseded document — please upload the current version."})
            note(f"   {req.get('evidence_type')}: provide={pr.status_code} {action}={rv.status_code}")

    allr = api("get", "/document-requests").json()
    note(f"platform-wide document requests: {len(allr) if isinstance(allr, list) else allr}")


def pre_assessment(assessments):
    hdr("PRE-ASSESSMENT QUESTIONNAIRE")
    t = api("get", "/pre-assessment/template")
    note(f"default template download -> {t.status_code} ({len(t.content)} bytes)")

    a = assessments[0]
    rows = [
        "Section,Question,Response,Evidence / Notes",
        "Organization & Scope,What is the name and primary business of your organization?,"
        "\"Global retail and logistics group — 40 countries\",Corporate profile",
        "Governance,Do you have a documented Information Security Policy? (Yes/No/Partial),Yes,"
        "InfoSec Policy v3.2 approved 2026-01-15",
        "Governance,Is there a designated CISO or security lead?,Yes,CISO reports to the CEO",
        "Risk Management,Do you perform formal risk assessments? (Yes/No/Partial),Yes,"
        "Twice-yearly using a 5x5 matrix",
        "Access Control,Is multi-factor authentication (MFA) enforced? (Yes/No/Partial),Partial,"
        "Enforced for admins; user rollout in progress",
        "Incident Response,Do you have a documented Incident Response Plan? (Yes/No),Yes,"
        "IR plan tested Q4 2025",
    ]
    csv_bytes = ("\n".join(rows) + "\n").encode()
    r = api("post", f"/assessments/{a['id']}/pre-assessment",
            files=[("file", ("completed_pre_assessment.csv", io.BytesIO(csv_bytes), "text/csv"))])
    note(f"upload custom pre-assessment to '{a['name'][:38]}' -> {r.status_code} {r.text[:120]}")
    d = api("get", f"/assessments/{a['id']}/pre-assessment/download")
    note(f"download back -> {d.status_code} ({len(d.content)} bytes)")


def status_variety(frameworks):
    hdr("STATUS VARIETY — draft / in-progress / in-review engagements")
    mkt = frameworks["MARKET_ASSESSMENT"]
    plan = [
        ("Mexico — Q3 Follow-up Review",  "Mexico",  "mexico",  "draft",       []),
        ("Denmark — Endpoint Remediation Check", "Denmark", "denmark", "in_progress", ["EP"]),
        ("ASIA — Third-Party Risk Deep Dive",    "ASIA",    "asia",    "in_review",   ["TPRM"]),
    ]
    for name, label, mid, target, domains in plan:
        selected = [f"{mkt['id']}:{c}" for c in domains]
        r = api("post", "/assessments", json={
            "name": name, "description": f"{label} follow-up engagement.",
            "framework_ids": [mkt["id"]], "selected_domains": selected,
            "organization": label, "market_id": mid, "market_label": label,
            "assigned_to": "karen.tan@cyberai.io",
        })
        if not r.ok:
            note(f"  ! {name} -> {r.status_code}"); continue
        aid = r.json()["id"]
        if target == "draft":
            note(f"{name}: draft (no questionnaire yet)")
            continue
        api("post", f"/assessments/{aid}/generate-questionnaire", json={})
        detail = frameworks["MARKET_ASSESSMENT"]
        qs = []
        for d in detail["domains"]:
            if domains and d["code"] not in domains:
                continue
            for cat in (d.get("categories") or [{"controls": d.get("controls", [])}]):
                for ctl in cat.get("controls", []) or []:
                    for q in ctl.get("questions", []) or []:
                        qs.append((q["id"], ctl.get("code"),
                                   str(q.get("question_type") or "YES_NO").upper(), q.get("choices")))
        # in_progress → answer only part of the questionnaire
        take = len(qs) if target == "in_review" else max(1, len(qs) // 3)
        for qid, code, qtype, choices in qs[:take]:
            val = ("YES" if qtype == "YES_NO"
                   else (choices or ["Passable"])[0] if qtype == "MULTI_CHOICE"
                   else "4" if qtype.startswith("SCALE")
                   else "Implemented and documented; evidence retained in the GRC platform.")
            api("post", f"/assessments/{aid}/responses",
                json={"question_id": qid, "control_id": code, "response_value": val})
        if target == "in_review":
            api("post", f"/assessments/{aid}/submit", json={})
        st = api("get", f"/assessments/{aid}").json().get("status")
        note(f"{name}: answered {take}/{len(qs)} -> status={st}")


def catalog_admin(frameworks):
    hdr("KNOWLEDGE BASE > MANAGE — catalog admin + audit history")
    stats = api("get", "/catalog/stats").json()
    note(f"catalog stats: frameworks={stats.get('frameworks')} controls={stats.get('controls')} "
         f"questions={stats.get('questions')}")

    mkt = frameworks["MARKET_ASSESSMENT"]
    cat_id = None
    for d in mkt["domains"]:
        for c in (d.get("categories") or []):
            if c.get("id"):
                cat_id = c["id"]; break
        if cat_id: break
    if not cat_id:
        note("  ! no category id available; skipping catalog writes"); return

    r = api("post", f"/catalog/frameworks/{mkt['id']}/controls", json={
        "category_id": cat_id, "code": "IAM-CUSTOM-1",
        "name": "Privileged Session Recording",
        "statement": "Privileged sessions to production systems are recorded and retained "
                     "for 12 months, with recordings reviewed after any high-risk change.",
        "criticality": "critical", "weight": 1.5,
    })
    note(f"create control -> {r.status_code} {r.text[:140]}")
    if not r.ok:
        return
    pk = r.json().get("id") or r.json().get("control_pk")
    if not pk:
        note(f"  (no pk returned: {list(r.json().keys())})"); return

    cur = api("get", f"/catalog/controls/{pk}").json()
    up = api("patch", f"/catalog/controls/{pk}", json={
        "statement": cur.get("statement", "") + " Reviews are evidenced in the PAM platform.",
        "criticality": "critical", "row_hash": cur.get("row_hash"),
    })
    note(f"edit control -> {up.status_code}")
    dup = api("post", f"/catalog/controls/{pk}/duplicate", json={})
    note(f"duplicate control -> {dup.status_code}")
    hist = api("get", f"/catalog/controls/{pk}/history")
    rows = hist.json() if hist.ok else []
    note(f"audit history rows -> {hist.status_code} ({len(rows) if isinstance(rows, list) else rows})")
    val = api("get", f"/catalog/frameworks/{mkt['id']}/validate")
    note(f"validate framework -> {val.status_code} {json.dumps(val.json())[:160] if val.ok else ''}")
    for term in ("encryption", "privileged access", "backup"):
        s = api("get", "/catalog/search", params={"q": term})
        n = len(s.json()) if isinstance(s.json(), list) else s.json().get("total", "?")
        note(f"search '{term}' -> {s.status_code} ({n} hits)")


def ask_ai():
    hdr("ASK-CYBERAI — grounded chat")
    qs = [
        "Which controls cover multi-factor authentication across our frameworks?",
        "What does ISO 27001 require for information classification?",
        "Summarise our biggest cloud security gaps.",
    ]
    for q in qs:
        r = api("post", "/ai/chat", json={"message": q}, timeout=180)
        if not r.ok:
            note(f"'{q[:44]}' -> {r.status_code} {r.text[:120]}"); continue
        b = r.json()
        ans = str(b.get("answer") or b.get("response") or "")
        note(f"'{q[:44]}…' -> {len(b.get('citations') or [])} citations")
        note(f"    {ans[:150]}…")


def main():
    try:
        assessments = api("get", "/assessments", timeout=20).json()
    except Exception as e:
        sys.exit(f"backend unreachable: {e}")
    if not assessments:
        sys.exit("No assessments — run scripts/seed_platform_demo.py first.")

    frameworks = {}
    for f in api("get", "/frameworks").json():
        frameworks[f["code"]] = api("get", f"/frameworks/{f['id']}").json()

    document_requests(assessments)
    pre_assessment(assessments)
    status_variety(frameworks)
    catalog_admin(frameworks)
    ask_ai()

    hdr("FINAL PLATFORM STATE")
    al = api("get", "/assessments").json()
    from collections import Counter
    print("  assessments by status:", dict(Counter(a["status"] for a in al)))
    print("  total assessments:", len(al))
    print("  users:", api("get", "/users-stats").json())
    dr = api("get", "/document-requests").json()
    print("  document requests:", len(dr) if isinstance(dr, list) else dr)


if __name__ == "__main__":
    main()
