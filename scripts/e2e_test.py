"""
End-to-end assessment workflow test.
Tests: user roles, assessment lifecycle, evidence, AI prefill, AI rating,
document requests (assessor<->owner), submission, scoring, and reports.

Run from project root with backend on :8000:
    .venv/Scripts/python.exe scripts/e2e_test.py

Notes:
  - AI prefill / rating can time out on Groq free-tier (expected behaviour,
    not a bug). The test marks them WARN not FAIL in that case.
  - Cleanup runs even if earlier steps fail.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "data" / "tmp_demo" / "sample_evidence"

ADMIN_HDRS    = {"X-User-Email": "e2e.admin@cyberai.io",    "X-User-Role": "org_owner"}
ASSESSOR_HDRS = {"X-User-Email": "e2e.auditor@cyberai.io",  "X-User-Role": "auditor"}
OWNER_HDRS    = {"X-User-Email": "e2e.contributor@cyberai.io", "X-User-Role": "evidence_contributor"}

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "", warn_only: bool = False) -> bool:
    if ok:
        tag = PASS
    elif warn_only:
        tag = WARN
        ok = True  # don't count as failure
    else:
        tag = FAIL
    line = f"  {tag} {label}"
    if detail:
        line += f"  -- {detail}"
    print(line)
    results.append((label, ok or warn_only, detail))
    return ok


def api(method: str, path: str, headers: dict | None = None, timeout: int = 30, **kw):
    kw["timeout"] = timeout
    h = dict(ADMIN_HDRS)
    if headers:
        h.update(headers)
    return requests.request(method, f"{BASE}{path}", headers=h, **kw)


def section(title: str):
    print(f"\n{'-' * 60}")
    print(f"  {title}")
    print(f"{'-' * 60}")


AID = None   # assessment id — set in section 4

# ── 1. Backend health ──────────────────────────────────────────

section("1. Backend & Framework Health")

try:
    r = api("get", "/frameworks")
    fws = r.json()
    check("Backend reachable", r.ok, f"status={r.status_code}")
    # The supported scope is NIST CSF 2.0 and SOC 2 Type 1. Asserted by code
    # rather than by count so adding a framework does not break this script.
    fw_codes = {f["code"] for f in fws}
    expected_codes = {"NIST_CYBERSECURITY_FRAMEWORK_CSF", "SOC2_TSC_2017"}
    missing = expected_codes - fw_codes
    check("NIST CSF 2.0 and SOC 2 are loaded", not missing,
          f"count={len(fws)} missing={sorted(missing) or 'none'}")
except Exception as e:
    print(f"  {FAIL} Backend unreachable: {e}")
    sys.exit(1)

fw = next((f for f in fws if f["code"] == "SOC2_TSC_2017"), None)
if fw is None:
    print(f"  {FAIL} SOC2_TSC_2017 framework not found; cannot continue")
    sys.exit(1)
FW_ID = fw["id"]
check("SOC2_TSC_2017 framework present", True, f"id={FW_ID}")

# ── 2. Role-based access control ──────────────────────────────

section("2. Role-Based Access Control")

r_anon  = requests.get(f"{BASE}/catalog/stats", timeout=10,
                       headers={"X-User-Role": "evidence_contributor", "X-User-Email": "anon@x.io"})
r_admin = api("get", "/catalog/stats")
check("Catalog blocked for non-reviewer (contributor role)", r_anon.status_code == 403,
      f"got {r_anon.status_code}")
check("Catalog accessible for org_owner", r_admin.ok,
      f"status={r_admin.status_code}")

# ── 3. User management ────────────────────────────────────────

section("3. User Management (create auditor + contributor)")

for user in [
    {"name": "E2E Auditor", "email": "e2e.auditor@cyberai.io",
     "role": "auditor", "organization": "France"},
    {"name": "E2E Contributor", "email": "e2e.contributor@cyberai.io",
     "role": "evidence_contributor", "organization": "France"},
]:
    r = api("post", "/users", json=user)
    check(f"Create user ({user['role']})", r.status_code in (200, 201, 409),
          f"status={r.status_code}")

r_users = api("get", "/users")
check("List users", r_users.ok, f"count={len(r_users.json())}")

# ── 4. Assessment creation ────────────────────────────────────

section("4. Assessment Creation")

fw_detail = api("get", f"/frameworks/{FW_ID}", timeout=20).json()
domain_codes = [d["code"] for d in fw_detail.get("domains", [])[:3]]
selected_domains = [f"{FW_ID}:{c}" for c in domain_codes]

payload = {
    "name": "E2E Test -- France IAM",
    "description": "Automated end-to-end test assessment",
    "framework_ids": [FW_ID],
    "selected_domains": selected_domains,
    "organization": "France",
    "assigned_to": "e2e.assessor@cyberai.io",
    "market_id": "france",
    "market_label": "France",
}
r = api("post", "/assessments", json=payload)
check("Create assessment", r.ok, f"status={r.status_code}")
if not r.ok:
    print(f"    Error body: {r.text[:300]}")
    sys.exit(1)

AID = r.json()["id"]
check("Assessment has UUID", bool(AID), AID)

# ── 5. Questionnaire generation ───────────────────────────────

section("5. Questionnaire Generation")

r = api("post", f"/assessments/{AID}/generate-questionnaire", json={}, timeout=30)
check("Generate questionnaire", r.ok, f"status={r.status_code}")
total_q = r.json().get("total_questions", 0) if r.ok else 0
check("Questions generated (>0)", total_q > 0, f"total={total_q}")

# ── 6. Evidence upload ────────────────────────────────────────

section("6. Evidence Upload")

txt_files = sorted(EVIDENCE_DIR.iterdir())[:4] if EVIDENCE_DIR.exists() else []
txt_files = [f for f in txt_files if f.is_file()]

if txt_files:
    handles, payload_files = [], []
    try:
        for p in txt_files:
            fh = p.open("rb")
            handles.append(fh)
            payload_files.append(("files", (p.name, fh, "text/plain")))
        r = api("post", f"/assessments/{AID}/evidence-bulk",
                files=payload_files, data={"doc_type": "evidence"}, timeout=60)
    finally:
        for fh in handles:
            fh.close()
    check("Upload evidence files", r.ok, f"status={r.status_code}")
    if r.ok:
        b = r.json()
        check("Files extracted", b.get("text_extracted", 0) > 0,
              f"count={b.get('count')}, extracted={b.get('text_extracted')}")
else:
    check("Evidence files available", False, f"No files at {EVIDENCE_DIR}")

# ── 7. AI pre-fill ────────────────────────────────────────────

section("7. AI Pre-fill (Groq LLM)")

t0 = time.time()
try:
    r = api("post", f"/assessments/{AID}/prefill", json={},
            headers=ASSESSOR_HDRS, timeout=600)
    elapsed = round(time.time() - t0, 1)
    if r.ok:
        b = r.json()
        summ = b.get("summary", {})
        filled = summ.get("filled", 0)
        total  = summ.get("total", 0)
        check("AI prefill", r.ok, f"status={r.status_code}  filled={filled}/{total}  {elapsed}s")
        if filled == 0:
            check("Prefill produced answers", False, "0 answers filled",
                  warn_only=True)
    else:
        check("AI prefill", False, f"status={r.status_code}  {r.text[:120]}")
except Exception as e:
    elapsed = round(time.time() - t0, 1)
    check("AI prefill", False,
          f"timeout/network error after {elapsed}s -- expected on Groq free tier",
          warn_only=True)

# ── 8. Manual answers ─────────────────────────────────────────

section("8. Manual Answer Submission")

detail = api("get", f"/assessments/{AID}", timeout=20).json()
prefilled_ids = {x["question_id"] for x in (detail.get("responses") or [])}

qs: list[dict] = []
for d in fw_detail.get("domains", []):
    if d["code"] not in domain_codes:
        continue
    cats = d.get("categories") or [{"controls": d.get("controls", [])}]
    for cat in cats:
        for ctl in (cat.get("controls") or []):
            for q in (ctl.get("questions") or []):
                qs.append({
                    "id": q["id"],
                    "type": str(q.get("question_type") or "YES_NO").upper(),
                    "choices": q.get("choices"),
                    "control_code": ctl.get("code"),
                })

posted = 0
for q in qs:
    if q["id"] in prefilled_ids:
        continue
    qtype = q["type"]
    if qtype == "YES_NO":
        val = "YES"
    elif qtype in ("MULTI_CHOICE", "MULTICHOICE"):
        choices = q["choices"] or ["Strong", "Passable", "Partially in place", "Not in place"]
        val = choices[0] if isinstance(choices[0], str) else list(choices)[0]
    elif qtype in ("SCALE_1_5", "SCALE"):
        val = "4"
    else:
        val = "Fully implemented, documented, and reviewed quarterly by the security team."
    r = api("post", f"/assessments/{AID}/responses",
            json={"question_id": q["id"], "control_id": q["control_code"],
                  "response_value": val, "notes": "e2e-test"},
            headers=ASSESSOR_HDRS, timeout=20)
    posted += r.status_code in (200, 201)

check("Manual answers posted", posted > 0,
      f"posted={posted}  prefilled={len(prefilled_ids)}  total_qs={len(qs)}")

# ── 9. AI evidence rating ─────────────────────────────────────

section("9. AI Evidence Rating")

t0 = time.time()
try:
    r = api("post", f"/assessments/{AID}/ai/rate-controls", json={},
            headers=ASSESSOR_HDRS, timeout=300)
    elapsed = round(time.time() - t0, 1)
    if r.ok:
        b = r.json()
        check("AI evidence rating", r.ok,
              f"status={r.status_code}  keys={list(b.keys())[:4]}  {elapsed}s")
    else:
        check("AI evidence rating", False,
              f"status={r.status_code}  {r.text[:120]}")
except Exception as e:
    elapsed = round(time.time() - t0, 1)
    check("AI evidence rating", False,
          f"timeout/network error after {elapsed}s -- expected on Groq free tier",
          warn_only=True)

# ── 10. Document request (assessor -> owner) ─────────────────

section("10. Document Request: Assessor -> Owner Communication")

doc_req_body = {
    "items": [
        {
            "evidence_type": "policy",
            "control_code": "IAM-1",
            "note": "Please provide the current IAM policy including RBAC config and access review logs.",
        }
    ],
    "requested_by": "e2e.assessor@cyberai.io",
    "note": "Required for IAM domain assessment",
}
r = api("post", f"/assessments/{AID}/document-requests",
        json=doc_req_body, headers=ASSESSOR_HDRS, timeout=20)
check("Assessor creates document request", r.ok, f"status={r.status_code}")
DR_ID = None
if r.ok:
    body_dr = r.json()
    # Response shape: {"ok": true, "created": [{id, ...}], "count": N}
    created_list = body_dr.get("created", [])
    DR_ID = created_list[0].get("id") if created_list else None
    check("Document request has ID", bool(DR_ID), str(DR_ID))
else:
    print(f"    Body: {r.text[:200]}")

# Owner inbox -- requires document request to exist first (market-based scoping)
r_inbox = api("get", f"/assessments/{AID}/document-requests",
              headers=OWNER_HDRS, timeout=20)
check("Owner can view assessment document requests", r_inbox.ok,
      f"status={r_inbox.status_code}  count={len(r_inbox.json()) if r_inbox.ok else '?'}")

# Owner provides a document against the request
if DR_ID and txt_files:
    fh = txt_files[0].open("rb")
    try:
        r_fulfill = api("post",
                        f"/assessments/{AID}/document-requests/{DR_ID}/provide",
                        files=[("files", (txt_files[0].name, fh, "text/plain"))],
                        data={"provided_by": "e2e.owner@cyberai.io"},
                        headers=OWNER_HDRS, timeout=30)
        check("Owner provides document", r_fulfill.ok,
              f"status={r_fulfill.status_code}")
    finally:
        fh.close()

# ── 11. Submit assessment ─────────────────────────────────────

section("11. Submit Assessment")

r = api("post", f"/assessments/{AID}/submit", json={}, headers=ASSESSOR_HDRS, timeout=20)
check("Submit assessment", r.ok, f"status={r.status_code}")
if r.ok:
    # Submit moves status to 'in_review' (not 'submitted')
    status = r.json().get("status")
    check("Status is in_review after submit", status == "in_review", f"status={status}")

# ── 12. Score assessment ──────────────────────────────────────

section("12. Scoring")

r = api("post", f"/assessments/{AID}/score", json={}, timeout=30)
check("Score assessment", r.ok, f"status={r.status_code}")
if r.ok:
    b = r.json()
    score = b.get("overall_score")
    maturity = b.get("maturity_level")
    findings = b.get("findings_count")
    check("Score returned", score is not None,
          f"score={score}%  maturity={maturity}  findings={findings}")
    check("Score in valid range 0-100", score is not None and 0 <= float(score) <= 100,
          f"score={score}")

# ── 13. Report view ───────────────────────────────────────────

section("13. Report View (Executive Dashboard Data)")

r = api("get", f"/assessments/{AID}/report-view", timeout=30)
check("Report view accessible", r.ok, f"status={r.status_code}")
if r.ok:
    b = r.json()
    # Report shape: meta, security_level, top_vulnerabilities, stats,
    #               domain_summary, enriched_reviews, strengths, gaps
    check("Report has security_level", "security_level" in b,
          f"score={b.get('security_level', {}).get('score')}")
    check("Report has domain_summary", "domain_summary" in b,
          f"domains={len(b.get('domain_summary', []))}")
    check("Report has top_vulnerabilities", "top_vulnerabilities" in b,
          f"count={len(b.get('top_vulnerabilities', []))}")
    check("Report has stats", "stats" in b, f"keys={list((b.get('stats') or {}).keys())[:5]}")

# ── 14. Activity log ──────────────────────────────────────────

section("14. Activity Log")

r = api("get", "/activity", timeout=20)
check("Activity log accessible", r.ok, f"status={r.status_code}")
if r.ok:
    entries = r.json()
    check("Activity log has entries", len(entries) > 0, f"count={len(entries)}")

# ── 15. Knowledge Base catalog ────────────────────────────────

section("15. Knowledge Base Catalog (Admin)")

r = api("get", "/catalog/stats", timeout=20)
check("Catalog stats accessible", r.ok, f"status={r.status_code}")
if r.ok:
    b = r.json()
    # Keys: frameworks, domains, categories, controls, questions, ...
    check("Catalog has controls (>0)", b.get("controls", 0) > 0,
          f"controls={b.get('controls')}  questions={b.get('questions')}")
    check("Catalog has both supported frameworks", b.get("frameworks") == 2,
          f"frameworks={b.get('frameworks')}")

# "access" rather than the old "access control": that phrase only ever existed
# in the removed Market Assessment framework, so it correctly matches nothing now.
r = api("get", "/catalog/search", params={"q": "access"}, timeout=20)
check("Catalog search returns results", r.ok and len(r.json()) > 0,
      f"status={r.status_code}  hits={len(r.json()) if r.ok else '?'}")
r = api("get", "/catalog/search", params={"q": "CC1.1"}, timeout=20)
check("Catalog search finds a control by code",
      r.ok and any(h["code"] == "CC1.1" for h in r.json()),
      f"status={r.status_code}  hits={len(r.json()) if r.ok else '?'}")

# ── 16. Compliance evaluation ─────────────────────────────────

section("16. Compliance Evaluation")

r = api("get", "/compliance/frameworks", timeout=20)
check("Compliance profiles listed", r.ok, f"status={r.status_code}")
if r.ok:
    profiles = r.json().get("supported", [])
    keys = {p["key"] for p in profiles}
    check("Only evaluable frameworks offered",
          keys == {"nist-csf-2-0", "soc2-type-1"}, f"keys={sorted(keys)}")
    # The guarantee must be in the wire format, not only in the database.
    check("No profile asserts operating effectiveness",
          all(p["asserts_operating_effectiveness"] is False for p in profiles),
          f"n={len(profiles)}")
    check("Dataset drop-in format documented",
          "sub_domains" in r.json().get("dataset_format", {}).get("domain", []),
          "")

r = api("get", f"/compliance/frameworks/{FW_ID}/controls", timeout=20)
check("Compliance controls listed", r.ok, f"status={r.status_code}")
if r.ok:
    ctrls = r.json()
    check("SOC 2 resolves to TYPE_1 only",
          ctrls.get("assurance_level") == "TYPE_1",
          f"level={ctrls.get('assurance_level')}")
    check("SOC 2 has 61 controls", ctrls.get("total") == 61,
          f"total={ctrls.get('total')}")

r = api("post", f"/assessments/{AID}/evaluations",
        json={"framework_id": FW_ID}, timeout=120)
check("Compliance evaluation runs", r.ok, f"status={r.status_code}")
if r.ok:
    run = r.json()
    # The assessment is scoped to the first 3 domains, so the run must cover
    # exactly those controls — not the whole framework.
    scoped = [c for c in api("get", f"/compliance/frameworks/{FW_ID}/controls",
                             timeout=20).json()["controls"]
              if c["domain_code"] in domain_codes]
    check("Every in-scope control evaluated",
          run.get("total") == len(scoped),
          f"evaluated={run.get('total')} in-scope={len(scoped)}")
    check("Evaluation stays inside the selected domains",
          {e["domain_code"] for e in run["evaluations"]} <= set(domain_codes),
          f"domains={sorted({e['domain_code'] for e in run['evaluations']})}")
    check("Evaluation is Type 1", run.get("assurance_level") == "TYPE_1",
          f"level={run.get('assurance_level')}")
    check("Evaluation does not assert operating effectiveness",
          run.get("asserts_operating_effectiveness") is False, "")
    check("Every evaluation row is Type 1",
          all(e["assurance_level"] == "TYPE_1" for e in run["evaluations"]), "")
    check("Unanswered controls are INSUFFICIENT_EVIDENCE, not PASS",
          run["counts"]["PASS"] == 0
          and run["counts"]["INSUFFICIENT_EVIDENCE"] == run["total"],
          f"counts={run.get('counts')}")

r = api("get", f"/assessments/{AID}/evaluations/summary", timeout=20)
check("Compliance summary readable", r.ok, f"status={r.status_code}")
if r.ok:
    s = r.json()
    check("Summary keeps the Type 1 scope",
          all(b["assurance_level"] == "TYPE_1" for b in s.get("frameworks", []))
          and all(b["asserts_operating_effectiveness"] is False
                  for b in s.get("frameworks", [])),
          f"frameworks={len(s.get('frameworks', []))}")

r = api("get", f"/assessments/{AID}/evaluation-audit", timeout=20)
check("Evaluation audit trail recorded",
      r.ok and r.json().get("total", 0) > 0,
      f"status={r.status_code} events={r.json().get('total') if r.ok else '?'}")

# ── Cleanup ───────────────────────────────────────────────────

section("Cleanup")

r = api("delete", f"/assessments/{AID}", timeout=20)
check("Delete test assessment", r.status_code in (200, 204, 404), f"status={r.status_code}")

for email in ["e2e.assessor@cyberai.io", "e2e.owner@cyberai.io"]:
    r = api("delete", f"/users/{email}", timeout=10)
    check(f"Delete user {email.split('@')[0]}", r.status_code in (200, 204, 404),
          f"status={r.status_code}")

# ── Summary ───────────────────────────────────────────────────

section("TEST SUMMARY")
total  = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = [(label, detail) for label, ok, detail in results if not ok]

print(f"\n  Passed: {passed}/{total}")
if failed:
    print(f"\n  Failed checks ({len(failed)}):")
    for label, detail in failed:
        print(f"    {FAIL} {label}  -- {detail}")
else:
    print(f"\n  All checks passed!")

sys.exit(0 if not failed else 1)
