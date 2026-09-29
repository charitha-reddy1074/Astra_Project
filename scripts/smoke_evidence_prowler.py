"""Smoke test for the evidence-collection Prowler workflow.

Exercises the real HTTP endpoints against the app + sqlite DB:
  request  ->  prowler generate (preview)  ->  prowler submit  ->  accept
and verifies the generated report is stored immutable and linked to the request.

Also verifies the role-enforcement gates added as the security hotfix:
  - owners blocked from assessor-only actions (create request, review, delete)
  - assessors blocked from owner-only actions (prowler generate/submit)
  - provided_by is taken from caller identity, not request body

Run: .venv/Scripts/python.exe scripts/smoke_evidence_prowler.py
"""
import json
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from backend.api.main import app  # noqa: E402
from backend.api.config import settings  # noqa: E402

# Known test owners per market — seeded by demo_setup.py.
_MARKET_OWNERS: dict[str, str] = {
    "united states": "united_states.owner@cyberai.io",
    "mea":           "mea.primary@cyberai.io",
    "denmark":       "denmark.primary@cyberai.io",
    "france":        "france.primary@cyberai.io",
    "mexico":        "mexico.primary@cyberai.io",
    "iom bu":        "iom_bu.primary@cyberai.io",
    "iom_bu":        "iom_bu.primary@cyberai.io",
    "asia":          "sam.primary@cyberai.io",
}

HDR = {"X-User-Email": "smoke@coretek.io",    "X-User-Role": "central_admin"}
ASSESSOR = {"X-User-Email": "assessor@coretek.io", "X-User-Role": "assessor"}
# A wrong-market owner used ONLY for role-gate (403) checks.
WRONG_OWNER = {"X-User-Email": "nobody@external.com", "X-User-Role": "owner"}

FAILS = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    # TestClient runs startup/shutdown (init_db) via the context manager.
    with TestClient(app) as c:
        r = c.get("/assessments", headers=HDR)
        check(r.status_code == 200, f"GET /assessments -> {r.status_code}")
        assessments = r.json()
        check(len(assessments) > 0, "at least one assessment exists")
        assessment = assessments[0]
        aid = assessment["id"]
        # Pick the real owner for this assessment's market.
        market = (assessment.get("market_label") or assessment.get("market_id") or "").lower()
        owner_email = _MARKET_OWNERS.get(market, next(iter(_MARKET_OWNERS.values())))
        OWNER = {"X-User-Email": owner_email, "X-User-Role": "owner"}
        print(f"  using assessment {aid} (market={market})")
        print(f"  owner for happy path: {owner_email}")

        # ── Role gates: owners blocked from assessor-only actions ────────────
        print("\n  [role gates — owner blocked from assessor actions]")
        body_req = {
            "items": [{
                "evidence_type": "IAM access review report",
                "domain_code": "PR.AA", "domain_name": "Identity Management",
                "control_code": "PR.AA-01",
                "note": "Please provide the Q2 2026 access review export.",
            }],
            "requested_by": "smoke@coretek.io",
        }
        r = c.post(f"/assessments/{aid}/document-requests", json=body_req, headers=WRONG_OWNER)
        check(r.status_code == 403, f"owner blocked from create-request -> {r.status_code}")

        # ── Role gates: assessors blocked from owner-only actions ────────────
        print("\n  [role gates — assessor blocked from owner actions]")
        # Placeholder request_id for gate checks — the route guard fires before
        # the service looks up the request, so any UUID will do.
        fake_rid = "00000000-0000-0000-0000-000000000001"
        r = c.post(f"/assessments/{aid}/document-requests/{fake_rid}/prowler", headers=ASSESSOR)
        check(r.status_code == 403, f"assessor blocked from prowler-generate -> {r.status_code}")
        r = c.post(f"/assessments/{aid}/document-requests/{fake_rid}/prowler/submit",
                   json={"report_id": "x"}, headers=ASSESSOR)
        check(r.status_code == 403, f"assessor blocked from prowler-submit -> {r.status_code}")

        # ── 1. Assessor creates the evidence request ─────────────────────────
        print("\n  [happy path]")
        r = c.post(f"/assessments/{aid}/document-requests", json=body_req, headers=HDR)
        check(r.status_code == 200, f"create request -> {r.status_code}")
        req_id = r.json()["created"][0]["id"]
        print(f"  request id {req_id}")

        # ── 2. Owner generates a Prowler report (preview only, not submitted) ─
        r = c.post(f"/assessments/{aid}/document-requests/{req_id}/prowler", headers=OWNER)
        check(r.status_code == 200, f"prowler generate -> {r.status_code}")
        gen = r.json()
        report_id = gen.get("report_id")
        check(bool(report_id), "generate returned a report_id")
        check(gen.get("immutable") is True, "generated report flagged immutable")
        content = gen.get("content", "")
        check("prowler" in content.lower() and len(content) > 200,
              "report content looks like a Prowler report")
        # Report must be scoped to the IAM/identity area requested.
        check(
            any(kw in content.lower() for kw in ("iam", "access review", "identity", "pr.aa")),
            "report scoped to requested evidence area (IAM/identity)",
        )

        # Staging: not yet linked to the request.
        r = c.get(f"/assessments/{aid}/document-requests", headers=HDR)
        this = next(x for x in r.json() if x["id"] == req_id)
        check(this["status"] == "requested", "status still 'requested' before submit")
        check(len(this["provided_files"]) == 0, "no files linked before submit")

        # ── 3. Owner submits — provided_by must come from caller.email ───────
        # Do NOT send provided_by in the body; the server must use caller.email.
        r = c.post(f"/assessments/{aid}/document-requests/{req_id}/prowler/submit",
                   json={"report_id": report_id},
                   headers=OWNER)
        check(r.status_code == 200, f"prowler submit -> {r.status_code}")
        sub = r.json()
        check(sub["status"] == "provided", "status -> 'provided' after submit")
        check(len(sub["provided_files"]) == 1, "one file linked after submit")
        check(sub.get("provided_by") == owner_email,
              f"provided_by from caller identity (got '{sub.get('provided_by')}')")
        stored = sub["provided_files"][0]["stored_name"]

        # ── 4. Immutability in manifest + file on disk ────────────────────────
        man_path = os.path.join(settings.EVIDENCE_FOLDER, aid, "_engagement_manifest.json")
        with open(man_path, encoding="utf-8") as fh:
            man = json.load(fh)
        entry = next((d for d in man["documents"] if d.get("stored_name") == stored), None)
        check(entry is not None, "manifest has the generated report entry")
        check(entry and entry.get("source") == "prowler", "manifest entry source=prowler")
        check(entry and entry.get("immutable") is True, "manifest entry immutable=True")
        disk = os.path.join(settings.EVIDENCE_FOLDER, aid, stored)
        check(os.path.isfile(disk), "generated report written to evidence folder")

        # Staging copy cleaned up after submit.
        stage = os.path.join(settings.EVIDENCE_FOLDER, "_generated", aid, report_id)
        check(not os.path.isfile(stage), "staging copy removed after submit")

        # ── Role gates: owner blocked from review/delete ──────────────────────
        print("\n  [role gates — owner blocked from review/delete]")
        r = c.post(f"/assessments/{aid}/document-requests/{req_id}/review",
                   json={"action": "accept"}, headers=WRONG_OWNER)
        check(r.status_code == 403, f"owner blocked from review -> {r.status_code}")
        r = c.delete(f"/assessments/{aid}/document-requests/{req_id}", headers=WRONG_OWNER)
        check(r.status_code == 403, f"owner blocked from delete-request -> {r.status_code}")

        # ── 5. Assessor accepts ───────────────────────────────────────────────
        print("\n  [accept + cleanup]")
        r = c.post(f"/assessments/{aid}/document-requests/{req_id}/review",
                   json={"action": "accept"}, headers=HDR)
        check(r.status_code == 200, f"review accept -> {r.status_code}")
        check(r.json()["status"] == "accepted", "status -> 'accepted'")

        # Cleanup.
        c.delete(f"/assessments/{aid}/evidence-bulk?file_name={stored}", headers=HDR)
        c.delete(f"/assessments/{aid}/document-requests/{req_id}", headers=HDR)
        print("  cleaned up test request + file")

    print()
    if FAILS:
        print(f"SMOKE TEST FAILED — {len(FAILS)} check(s) failed")
        sys.exit(1)
    print("SMOKE TEST PASSED — all checks green")


if __name__ == "__main__":
    main()
