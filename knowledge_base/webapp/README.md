# CyberAI — Framework Management System (FMS)

Internal web app for managing the CyberAI framework catalog. Replaces DB Browser
and manual JSON editing with a safe, audited, validated editing surface over the
existing SQLite database.

- **Backend:** FastAPI (thin service over the existing `cyber_ai.db`) — every
  write is validated, audit-logged, and concurrency-checked.
- **Frontend:** React + TypeScript + Vite + Tailwind + TanStack (Query/Router/Table)
  + Zustand + React Hook Form. Dark, keyboard-driven, three-pane Explorer.

> Product & UX design rationale: [`DESIGN.md`](DESIGN.md).

## Prerequisites

- Python 3.10+ and Node 18+.
- The database `cyber_ai.db` must exist in the project root (built by the
  `import_frameworks.py` pipeline one level up).

## Run

**1. Backend** (terminal 1):
```bash
cd webapp/backend
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt   # first time
./.venv/bin/uvicorn app.main:app --reload --port 8787
```
API at http://127.0.0.1:8787 · interactive docs at http://127.0.0.1:8787/docs

**2. Frontend** (terminal 2):
```bash
cd webapp/frontend
npm install       # first time
npm run dev
```
App at http://localhost:5173 (Vite proxies `/api/*` to the backend on :8787).

## What works today (verified end-to-end)

- **Dashboard** — live counts, per-framework table, recent audit activity.
- **Explorer** — framework rail · resizable 3-pane · **Tree ⇄ Grid** view toggle.
  - Tree: recursive expand/collapse with rolled-up control counts.
  - Grid: sortable/filterable TanStack Table for bulk browsing at scale.
- **Detail panel** — edit statement, name, evidence flag, **multiple questions**
  (add/remove/type/weight), view framework-specific `attributes`, and an
  **edit History** tab (from the audit log).
- **Save** with soft **optimistic-concurrency** (`row_hash`) → 409 on stale edits.
- **Duplicate / Delete** controls; **Revert** unsaved changes.
- **Command palette** (⌘K / Ctrl+K) — global control search, jump-to-edit.
- **Validation** page — duplicate controls, missing questions, empty statements,
  broken hierarchy, orphan controls, unused nodes; click an issue to jump to it.

## Safety model

- Every mutation writes an `audit_logs` row (before/after JSON).
- Writes require an editor role (`require_editor`; auth is stubbed — see
  `app/security.py`).
- Stale-edit detection via `row_hash` (no schema change needed).
- The DB is never edited raw — the API is the only writer.

## Architecture map

```
webapp/
├── backend/
│   └── app/
│       ├── main.py            FastAPI app + CORS + routers
│       ├── settings.py        paths, stub identity
│       ├── db.py              connection, transaction(), JSON helpers
│       ├── security.py        role guard (swap-in point for real auth)
│       ├── schemas/models.py  Pydantic API contract
│       ├── routers/           catalog (read) + controls (write)
│       └── services/          audit · controls · catalog · validation
└── frontend/
    └── src/
        ├── app/               queryClient, router
        ├── components/        AppShell, CommandPalette, ui primitives
        ├── features/          dashboard · explorer · validation
        ├── lib/               api, types, queries (React Query), utils
        └── stores/ui.ts       Zustand (selection, view mode, tree state)
```

## Not yet built (roadmap — see DESIGN.md §14)

Import/export UI with diff & rollback (API `export` exists), inline tree
drag-to-move, real auth/RBAC, framework version cloning, AI-assisted editing.
