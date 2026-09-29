# CyberAI FMS — Product & UX Design

The design document behind the Framework Management System. Written before the
code; the code implements it.

## 0. Critique of the original brief (what changed and why)

1. **One "Question" field → a questions sub-editor.** Controls have 0..N questions
   in the real schema (Market ~5–6). A single field would hide/clobber data.
2. **Weight/Guidance/Notes/Related aren't control columns.** `weight` is per
   *question*; guidance/related live in `controls.attributes` JSON. The panel
   shows promoted columns + an attributes view, and labels which is which.
3. **Navigation had 3 overlapping entries.** Collapsed to Dashboard · Explorer ·
   Validation · (Import) · Settings. "Assessment Items" = a *view mode* in Explorer,
   not a page. "Reports" belongs to the assessment product, deferred.
4. **VS Code single-tree won't scale to 473 controls.** Middle pane is dual-mode
   **Tree ⇄ Grid**.
5. **Version history/rollback reality.** Delivered: audit-log history per control.
   Point-in-time rollback needs snapshots — roadmap.
6. **Editing the source-of-truth catalog is dangerous.** Guardrail: warn + "clone
   as new version" when a framework has live assessments (schema supports versions).
7. **Undo/redo isn't DB state.** Scoped to form (Revert) + server audit history;
   global undo is roadmap.

## 1. Product architecture

Three tiers: **SQLite (existing)** → **FastAPI** (the validation/audit/safety layer;
the only writer) → **React SPA** (one of several future clients). The API is the
contract.

## 2. UX flow

Primary: open framework → Explorer → select control → edit → save (validate +
audit + concurrency check) → toast + cache update. Bulk: Grid → multi-select →
move/duplicate/delete. Global: ⌘K search from anywhere → jump to control.

## 3. Information architecture

`Framework(version) → node tree (domain → category/sub_domain → …) → control →
{questions[], evidence_types[], attributes{}}`. Runtime data (assessments) is a
separate consumer that pins a framework version.

## 4. Page hierarchy

`/` Dashboard · `/explorer` (the workspace) · `/validation` · (`/import` roadmap) ·
(`/settings` roadmap). Command palette overlays all routes.

## 5. Component hierarchy

```
AppShell (Sidebar, CommandPalette, Toaster, Outlet)
├─ DashboardPage (StatCard×6, FrameworkTable, ActivityFeed)
├─ ExplorerPage (PanelGroup)
│   ├─ FrameworkRail
│   ├─ TreeView (NodeRow* recursive → ControlLeaf) | ControlGrid (TanStack Table)
│   └─ DetailPanel (Editor tab: fields + QuestionEditor[] + Attributes; History tab)
└─ ValidationPage (summary + issue list → jump-to-control)
```

## 6. Wireframe

See README + the running app. Three resizable panes: rail (16%) · tree/grid (44%) ·
detail (40%).

## 7. Folder structure

See `webapp/README.md` → Architecture map.

## 8. API design

Read: `GET /frameworks`, `/frameworks/{id}`, `/frameworks/{id}/tree`,
`/frameworks/{id}/controls?node_id=&q=&question_type=`, `/frameworks/{id}/validate`,
`/frameworks/{id}/export`, `/controls/{pk}`, `/controls/{pk}/history`, `/search?q=`,
`/stats`.
Write (editor role): `POST /frameworks/{id}/controls`, `PATCH /controls/{pk}`,
`DELETE /controls/{pk}`, `POST /controls/{pk}/duplicate`, `POST /controls/{pk}/move`.

## 9. State management

- **Server state → React Query** (staleTime 30s, optimistic cache writes on save).
- **UI state → Zustand** (selected framework/node/control, view mode, tree
  expansion, command-palette open) — persisted subset.
- **Form → React Hook Form** with `useFieldArray` for questions, `isDirty`-gated
  Save + Revert, unsaved-changes indicator.

## 10. Performance

Tree endpoint returns counts only; controls fetched per-framework once and grouped
client-side; TanStack Table virtualization-ready; debounced search; single
aggregate control-detail call; React Query dedup + `defaultPreload: "intent"`.

## 11. Scalability

Stateless API; indexed lookups (schema already indexed); grid handles 10k+ rows;
framework versioning isolates catalog growth; recursive tree supports any depth.

## 12. Security

Role guard on all writes (`require_editor`); every mutation audit-logged with
before/after + user; soft optimistic concurrency (`row_hash`); parameterized SQL;
server-side Pydantic validation; write-guard for frameworks with live assessments
(roadmap enforcement). Auth is stubbed in `app/security.py` — single swap-in point.

## 13. UI component list

Button, Badge, Spinner, EmptyState, Toaster/useToasts, input/.input,
CommandPalette (cmdk), resizable Panels, TreeView, DataGrid (TanStack Table),
Tabs, StatCard, HistoryTab. shadcn/ui can be layered in via its CLI later; these
primitives keep the app self-contained and runnable today.

## 14. Roadmap

- **v1 (now):** catalog CRUD, tree/grid, validation, search, audit history.
- **v2:** Import UI (drag-drop → dry-run diff → commit → rollback), tree drag-to-move,
  real auth + RBAC UI, framework version cloning, optimistic-concurrency migration
  (`updated_at`/`row_version` columns), Settings.
- **v3:** AI-assisted editing (statement rewrite, question generation from statement,
  gap/duplicate detection), cross-framework control mapping (crosswalk table),
  collaborative presence, point-in-time rollback via snapshots.
