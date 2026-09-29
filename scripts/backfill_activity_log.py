"""Backfill the Activity Log from history already recorded in the database.

    .venv/bin/python scripts/backfill_activity_log.py            # write
    .venv/bin/python scripts/backfill_activity_log.py --dry-run  # preview only

The activity log (audit_logs, surfaced at GET /activity) only started capturing
platform-wide events recently, so on an existing database the screen would open
empty even though the platform has plenty of history. This script reconstructs
that history from the records that already exist:

    users                → user.created
    assessments          → assessment.created, assessment.scored / .submitted
    questionnaires       → questionnaire.generated
    engagement manifests → evidence.uploaded          (one event per upload batch)
    category_ratings     → ai.control_score
    document_requests    → document.requested / .provided / .accepted / .rejected
    findings             → assessment.findings_generated  (one event per run)
    audit_logs (catalog) → fills actor_name/summary on pre-existing rows

Every timestamp is the REAL timestamp of the source record — created_at,
generated_at, requested_at, uploaded_at and so on. Nothing is invented. Where a
source record does not name an actor (a questionnaire does not store who
generated it), the event is attributed to the assessment's assigned reviewer,
which is the best signal the data carries; user.created has no such signal at
all and is attributed to "System".

IDEMPOTENT. Each derived event gets a UUID5 id computed from its
(event, entity, timestamp), so re-running inserts nothing new and never
duplicates. The one UPDATE it performs — filling actor_name/market/summary on
catalog rows written before those columns existed — is guarded on
`summary IS NULL`, so it too runs at most once per row. This is the only
sanctioned write to an existing audit row anywhere in the codebase; the log is
otherwise strictly append-only.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text, update  # noqa: E402

from backend.api.config import settings  # noqa: E402
from backend.api.database import AsyncSessionLocal  # noqa: E402
from backend.api.models.assessment import (  # noqa: E402
    Assessment, CategoryRating, DocumentRequest, Finding, Questionnaire,
)
from backend.api.models.audit import AuditLog  # noqa: E402
from backend.api.models.user import User  # noqa: E402

# Stable namespace for derived event ids — do not change, it is what makes
# re-running this script a no-op.
NS = uuid.UUID("6f9d3b52-1d1e-5a4f-9a1e-4c2f7f6a1b00")

DRY_RUN = "--dry-run" in sys.argv


def event_id(action: str, entity_type: str, entity_id: str, ts: datetime) -> str:
    return str(uuid.uuid5(NS, f"{action}|{entity_type}|{entity_id}|{ts.isoformat()}"))


def hdr(m: str) -> None:
    print(f"\n{'=' * 68}\n  {m}\n{'=' * 68}")


ROLE_LABELS = {
    "org_owner": "Organization Owner",
    "compliance_manager": "Compliance Manager",
    "security_manager": "Security Manager",
    "auditor": "Auditor",
    "team_member": "Team Member",
    "evidence_contributor": "Evidence Contributor",
}


class Backfill:
    def __init__(self, db):
        self.db = db
        self.users: dict[str, User] = {}
        self.assessments: list[Assessment] = []
        self.by_id: dict[str, Assessment] = {}
        self.pending: list[AuditLog] = []
        self.counts: dict[str, int] = defaultdict(int)

    # ── helpers ──────────────────────────────────────────────────────────────
    def name_of(self, email: str | None) -> str | None:
        if not email:
            return None
        u = self.users.get(email.lower())
        return u.name if u else None

    def role_of(self, email: str | None) -> str | None:
        if not email:
            return None
        u = self.users.get(email.lower())
        return u.role if u else None

    def market_of(self, a: Assessment | None) -> str | None:
        if a is None:
            return None
        return a.market_label or a.organization or None

    def add(self, *, action, entity_type, entity_id, ts, summary,
            market=None, actor_email=None, actor_name=None, actor_role=None,
            origin=None):
        if ts is None:
            return
        actor_email = (actor_email or "").strip().lower() or None
        self.pending.append(AuditLog(
            id=event_id(action, entity_type, entity_id, ts),
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
            changes={"source": "backfill", "origin": origin or entity_type},
            user_email=actor_email,
            user_role=actor_role or self.role_of(actor_email),
            actor_name=actor_name or self.name_of(actor_email),
            market=market,
            summary=summary,
            created_at=ts,
        ))
        self.counts[action] += 1

    # ── load ─────────────────────────────────────────────────────────────────
    async def load(self):
        users = (await self.db.execute(select(User))).scalars().all()
        self.users = {u.email.lower(): u for u in users}
        self.assessments = (await self.db.execute(
            select(Assessment).order_by(Assessment.created_at))).scalars().all()
        self.by_id = {a.id: a for a in self.assessments}
        print(f"  loaded {len(users)} users, {len(self.assessments)} assessments")

    # ── derivations ──────────────────────────────────────────────────────────
    def derive_users(self):
        for u in self.users.values():
            role = ROLE_LABELS.get(u.role, u.role)
            where = f" in {u.organization}" if u.organization else ""
            self.add(
                action="user.created", entity_type="user", entity_id=u.id,
                ts=u.created_at, market=u.organization,
                # No creator is recorded on the users table — attributing this to
                # a named admin would be a guess, so it stays a platform event.
                actor_email=None, actor_name="System", actor_role="system",
                summary=f"{role} account created for {u.name} ({u.email}){where}.",
                origin="users",
            )

    def derive_assessments(self):
        for a in self.assessments:
            market = self.market_of(a)
            owner_email = a.created_by or a.assigned_to
            assignee = self.name_of(a.assigned_to) or a.assigned_to
            tail = f" and assigned it to {assignee}" if a.assigned_to else ""
            self.add(
                action="assessment.created", entity_type="assessment", entity_id=a.id,
                ts=a.created_at, market=market, actor_email=owner_email,
                summary=f"Created the assessment “{a.name}”{tail}.",
                origin="assessments",
            )

            finished = a.completed_at or (a.updated_at if a.status in
                                          ("completed", "in_review") else None)
            if a.status == "completed" and finished:
                score = f"{a.overall_score:.1f}%" if a.overall_score is not None else "no score"
                maturity = f", maturity level {a.maturity_level}" if a.maturity_level else ""
                self.add(
                    action="assessment.scored", entity_type="assessment", entity_id=a.id,
                    ts=finished, market=market, actor_email=a.assigned_to,
                    summary=f"Scored and completed “{a.name}” — {score}{maturity}.",
                    origin="assessments",
                )
            elif a.status == "in_review" and finished:
                self.add(
                    action="assessment.submitted", entity_type="assessment", entity_id=a.id,
                    ts=finished, market=market, actor_email=a.assigned_to,
                    summary=f"Submitted “{a.name}” for review.",
                    origin="assessments",
                )

    async def derive_questionnaires(self):
        rows = (await self.db.execute(select(Questionnaire))).scalars().all()
        for qn in rows:
            a = self.by_id.get(qn.assessment_id)
            if a is None:
                continue
            self.add(
                action="questionnaire.generated", entity_type="questionnaire",
                entity_id=qn.assessment_id, ts=qn.generated_at,
                market=self.market_of(a), actor_email=a.assigned_to,
                summary=(f"Generated a {qn.total_questions}-question questionnaire "
                         f"for “{a.name}”."),
                origin="questionnaires",
            )

    def derive_evidence(self):
        """One event per upload batch, read from each engagement's file manifest."""
        root = Path(settings.EVIDENCE_FOLDER)
        if not root.is_dir():
            return
        for folder in sorted(root.iterdir()):
            manifest = folder / "_engagement_manifest.json"
            if not manifest.is_file():
                continue
            a = self.by_id.get(folder.name)
            if a is None:
                continue
            try:
                docs = json.loads(manifest.read_text()).get("documents") or []
            except (json.JSONDecodeError, OSError):
                continue
            # Group by doc_type — a batch upload lands as one user action, not N.
            batches: dict[str, list[dict]] = defaultdict(list)
            for d in docs:
                batches[d.get("doc_type") or "evidence"].append(d)
            for doc_type, items in sorted(batches.items()):
                stamps = []
                for d in items:
                    try:
                        stamps.append(datetime.fromisoformat(d["uploaded_at"]))
                    except (KeyError, TypeError, ValueError):
                        pass
                if not stamps:
                    continue
                n = len(items)
                names = ", ".join(sorted(d.get("original_name", "?") for d in items)[:3])
                more = f" and {n - 3} more" if n > 3 else ""
                self.add(
                    action="evidence.uploaded", entity_type="assessment",
                    entity_id=f"{a.id}:{doc_type}", ts=min(stamps),
                    market=self.market_of(a), actor_email=a.assigned_to,
                    summary=(f"Uploaded {n} {doc_type} document{'s' if n != 1 else ''} "
                             f"to “{a.name}” ({names}{more})."),
                    origin="engagement_manifest",
                )

    async def derive_ratings(self):
        rows = (await self.db.execute(select(CategoryRating))).scalars().all()
        for r in rows:
            a = self.by_id.get(r.assessment_id)
            target = r.category_code or r.category_name or "the control set"
            label = f" ({r.category_name})" if r.category_code and r.category_name else ""
            verdict = f" — {r.verdict}" if r.verdict else ""
            self.add(
                action="ai.control_score", entity_type="category_rating", entity_id=r.id,
                ts=r.created_at, market=self.market_of(a),
                actor_email=a.assigned_to if a else None,
                summary=(f"AI control scoring ran for {target}{label}"
                         f"{verdict}, scored {r.score:.1f}%."),
                origin="category_ratings",
            )

    async def derive_document_requests(self):
        rows = (await self.db.execute(select(DocumentRequest))).scalars().all()
        for dr in rows:
            a = self.by_id.get(dr.assessment_id)
            market = self.market_of(a)
            name = a.name if a else "an assessment"
            scope = f" for {dr.control_code}" if dr.control_code else ""
            self.add(
                action="document.requested", entity_type="document_request", entity_id=dr.id,
                ts=dr.requested_at, market=market, actor_email=dr.requested_by,
                summary=f"Requested {dr.evidence_type} evidence{scope} for “{name}”.",
                origin="document_requests",
            )
            if dr.provided_at:
                n = len(dr.provided_files or [])
                self.add(
                    action="document.provided", entity_type="document_request", entity_id=dr.id,
                    ts=dr.provided_at, market=market, actor_email=dr.provided_by,
                    summary=(f"Provided {n} file{'s' if n != 1 else ''} against the "
                             f"{dr.evidence_type} request for “{name}”."),
                    origin="document_requests",
                )
            if dr.reviewed_at and dr.status in ("accepted", "rejected"):
                note = f" {dr.review_note}" if dr.review_note else ""
                self.add(
                    action=f"document.{dr.status}", entity_type="document_request",
                    entity_id=dr.id, ts=dr.reviewed_at, market=market,
                    actor_email=dr.requested_by,
                    summary=(f"{dr.status.capitalize()} the {dr.evidence_type} evidence "
                             f"provided for “{name}”.{note}"),
                    origin="document_requests",
                )

    async def derive_findings(self):
        """Findings are written in bulk by one scoring run — log the run, not 649 rows."""
        rows = (await self.db.execute(select(Finding))).scalars().all()
        runs: dict[str, list[Finding]] = defaultdict(list)
        for f in rows:
            runs[f.assessment_id].append(f)
        for aid, fs in runs.items():
            a = self.by_id.get(aid)
            stamps = [f.created_at for f in fs if f.created_at]
            if not stamps:
                continue
            crit = sum(1 for f in fs if f.severity == "critical")
            high = sum(1 for f in fs if f.severity == "high")
            self.add(
                action="assessment.findings_generated", entity_type="assessment",
                entity_id=aid, ts=min(stamps), market=self.market_of(a),
                actor_email=a.assigned_to if a else None,
                summary=(f"Generated {len(fs)} findings for "
                         f"“{a.name if a else aid}” ({crit} critical, {high} high)."),
                origin="findings",
            )

    # ── legacy catalog rows ──────────────────────────────────────────────────
    async def enrich_catalog_rows(self) -> int:
        """Give pre-existing catalog audit rows an actor name and a readable summary.

        Their `action` ("create"/"update"/…) is left exactly as written — rewriting
        recorded history is precisely what this log must never do. The Activity Log
        normalises those verbs to catalog.control_* on read instead.
        """
        rows = (await self.db.execute(
            select(AuditLog).where(AuditLog.summary.is_(None))
        )).scalars().all()
        verbs = {"create": "Created", "update": "Updated", "delete": "Deleted",
                 "move": "Moved", "duplicate": "Duplicated"}
        n = 0
        for row in rows:
            changes = row.changes or {}
            after = changes.get("after") if isinstance(changes, dict) else None
            before = changes.get("before") if isinstance(changes, dict) else None
            src = after if isinstance(after, dict) else (before if isinstance(before, dict) else {})
            label = src.get("code") or src.get("name") or row.entity_id[:8]
            verb = verbs.get(row.action, row.action.capitalize())
            summary = f"{verb} the catalog {row.entity_type} {label}."
            if not DRY_RUN:
                await self.db.execute(
                    update(AuditLog).where(AuditLog.id == row.id).values(
                        summary=summary,
                        actor_name=self.name_of(row.user_email),
                    )
                )
            n += 1
        return n

    # ── persist ──────────────────────────────────────────────────────────────
    async def persist(self) -> tuple[int, int]:
        existing = set((await self.db.execute(select(AuditLog.id))).scalars().all())
        fresh = [r for r in self.pending if r.id not in existing]
        # Two derived events could theoretically collapse onto the same id; keep
        # the first so the insert never trips the primary key.
        seen, deduped = set(), []
        for r in fresh:
            if r.id in seen:
                continue
            seen.add(r.id)
            deduped.append(r)
        if not DRY_RUN:
            self.db.add_all(deduped)
        return len(deduped), len(self.pending) - len(deduped)


async def main() -> int:
    hdr("ACTIVITY LOG BACKFILL" + ("  (dry run)" if DRY_RUN else ""))
    print(f"  database: {settings.DATABASE_URL}")

    async with AsyncSessionLocal() as db:
        # The new columns land via init_db()'s migration list when the API boots;
        # add them here too so the script stands alone on a cold database.
        for stmt in ("ALTER TABLE audit_logs ADD COLUMN actor_name VARCHAR(255)",
                     "ALTER TABLE audit_logs ADD COLUMN market VARCHAR(255)",
                     "ALTER TABLE audit_logs ADD COLUMN summary TEXT"):
            try:
                await db.execute(text(stmt))
                await db.commit()
            except Exception:
                await db.rollback()  # column already exists

        bf = Backfill(db)
        await bf.load()

        bf.derive_users()
        bf.derive_assessments()
        await bf.derive_questionnaires()
        bf.derive_evidence()
        await bf.derive_ratings()
        await bf.derive_document_requests()
        await bf.derive_findings()

        enriched = await bf.enrich_catalog_rows()
        inserted, skipped = await bf.persist()

        if not DRY_RUN:
            await db.commit()

        hdr("RESULT")
        for action in sorted(bf.counts):
            print(f"  {action:<36} {bf.counts[action]:>4} derived")
        print(f"\n  {'derived total':<36} {len(bf.pending):>4}")
        print(f"  {'inserted (new)':<36} {inserted:>4}")
        print(f"  {'already present (skipped)':<36} {skipped:>4}")
        print(f"  {'legacy catalog rows enriched':<36} {enriched:>4}")

        total = await db.scalar(text("SELECT COUNT(*) FROM audit_logs"))
        print(f"  {'rows in audit_logs now':<36} {total:>4}")
        if DRY_RUN:
            print("\n  --dry-run: nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
