# AI Questionnaire Designer — Implementation Plan (Tier 1)

**Status:** Proposed — for review, no code written yet.
**Author:** Claude (planning session)
**Goal:** Give assessment designers control over *how* the AI generates the questionnaire, and activate the maturity-rubric-aware generator that already exists but is disabled — without a 15-panel wizard.

---

## 1. Guiding principles

1. **Expose, don't rebuild.** ~70% of the "15 features" already exist in the backend (maturity guides, evidence types, weights, follow-up engine, diagnostic prompt). The work is surfacing and wiring them.
2. **One config object, threaded end-to-end.** Today every AI endpoint (`generateQuestionnaire`, `aiRateControls`, `prefill`) is parameterless. We introduce a single `generation_config` that flows UI → API → generator.
3. **Per-assessment choice, no silent default.** Per the decision, the builder *requires* the designer to pick a generation strategy before questions are produced. No hidden default behavior.
4. **Small, safe surface.** Add a section to the existing `CreateForm`; do not fork a new wizard. Keep deterministic scoring untouched.

---

## 2. The `generation_config` object (the contract)

A single JSON object created in the builder, persisted on the assessment, and read by the generator.

```jsonc
{
  "strategy": "framework_only" | "framework_plus_ai" | "ai_rewrite",  // REQUIRED, no default
  "depth": "standard" | "detailed",            // maps to DIAGNOSTIC_QUESTIONS_PER_DOMAIN (e.g. 6 vs 10)
  "evidence_policy": "always" | "high_and_critical" | "optional",
  "custom_instructions": "string, optional — appended to the generation system prompt",
  "recommendation_mode": "framework" | "ai" | "hybrid"   // default hybrid (already the de-facto behavior)
}
```

- `strategy` has **no default** — the API rejects a generate call for an assessment whose `generation_config.strategy` is unset, and the UI blocks "Generate" until chosen.
- Everything else has a sensible fallback so the object stays small.

---

## 3. Backend changes

### 3.1 Persist the config
- **`backend/api/models/assessment.py`** — add `generation_config` (JSON, nullable) to the `Assessment` model. Requires a lightweight migration / `create_all` (project already uses SQLAlchemy models; confirm migration approach — `database.py`).
- **`backend/api/services/assessment_service.py`** — `create_assessment(...)` accepts and stores `generation_config`.

### 3.2 Make the diagnostic generator an *augment*, not a *replace* (the accuracy win)
Current: `_ensure_diagnostic_questions()` (`assessment_service.py:462-561`) is gated on `ENABLE_DIAGNOSTIC_QUESTIONS` env + `GROQ_API_KEY`, and **replaces** a domain's questions.

Changes:
- Gate on `generation_config.strategy` instead of the global env flag:
  - `framework_only` → skip diagnostic entirely (current default behavior).
  - `framework_plus_ai` → keep framework questions **and append** diagnostic questions (de-dupe by sub_topic).
  - `ai_rewrite` → replace (current behavior), but grounded (below).
- **Ground the generator**: pass each control's `maturity_guide` and `expected_evidence_types` (already stored inside `Control.maturity_criteria`, see `framework_service.py:318-325`) into `generate_diagnostic_questions_for_domain()` so generated `maturity_signals` align with the framework's own rubric instead of a generic one.
- Honor `depth` → set the per-domain question count (`DIAGNOSTIC_QUESTIONS_PER_DOMAIN` equivalent, `questionnaire.py`).
- Append `custom_instructions` to `MATURITY_DIAGNOSTIC_SYSTEM_PROMPT` (`questionnaire.py:224`).

### 3.3 Evidence policy
- In questionnaire build, use `evidence_policy` to decide which questions carry a required `expected_evidence_types` prompt vs. optional. Wire into `evidence_requirements()` (`assessment_service.py:1639`) so the owner-facing document requests reflect the policy.

### 3.4 API endpoint
- **`backend/api/main.py`** — `POST /assessments/{id}/generate-questionnaire` (line ~457) accepts an optional `generation_config` body (or reads the persisted one). Returns `400` if `strategy` is unset.
- Keep `ai/rate-controls` and `prefill` unchanged for now (Tier 2 threads config into them).

---

## 4. Frontend changes

### 4.1 Builder — add a "Generation Settings" section to `CreateForm`
File: `frontend/src/components/workspace/AssessmentsView.js` (`CreateForm`, ~lines 235-518). Insert after the Domains checkboxes, before Scope:

- **Question strategy** (radio, REQUIRED — no preselection):
  - Framework questions only
  - Framework + AI diagnostic (maturity-graded)
  - AI-rewrite framework questions
  - Helper text explaining LLM cost / accuracy trade-off.
- **Depth** (segmented: Standard / Detailed) — only shown when strategy involves AI.
- **Evidence policy** (select: Always / High-risk & Critical-gap only / Optional).
- **Custom instructions** (collapsible textarea, optional) — "Guide the AI: tone, focus areas, question limits, house style."
- **Recommendation mode** (select: Framework / AI / Hybrid) — default Hybrid.

Validation: "Generate"/"Create" disabled until `strategy` is chosen. Tooltip explains why.

### 4.2 Wire through the API client
File: `frontend/src/lib/api.js`
- `assessments.create(...)` includes `generation_config`.
- `assessments.generateQuestionnaire(id, generationConfig?)` sends the body.

### 4.3 Workspace auto-generate
File: `frontend/src/components/workspace/AssessmentWorkspace.js` (~lines 114-123) — the silent auto-generate on workspace open now passes the persisted `generation_config`. If strategy is somehow unset, show a prompt to configure instead of silently generating.

---

## 5. What this delivers against the original 15 features

| Feature | Delivered by Tier 1 |
|---|---|
| 1 Domains | Already present (no change) |
| 3 Generation strategy | ✅ radio in builder + backend branch |
| 4 Maturity model | Grounded generation uses existing `maturity_guide` |
| 5 Evidence strategy | ✅ evidence_policy select |
| 7 Recommendation strategy | ✅ recommendation_mode select |
| 15 Prompt Studio | ✅ custom_instructions (minimal) |
| 13 Domain preview | Deferred to Tier 2 |
| 6 Follow-up rules | Deferred to Tier 2 (engine already exists) |
| 8/11 AI behavior / per-domain prompts | Deferred (global custom_instructions covers most) |
| 2 Domain config panel | Deferred |
| 14 Coverage analyzer | Deferred |
| 9 Dependency/skip trees | **Not planned** — high complexity, low fit |
| 10 Weightage editor | **Not planned** — weights already calibrated in scoring |

---

## 6. Testing

- **Backend:** unit-test each strategy branch of `_ensure_diagnostic_questions` (framework_only skips LLM; framework_plus_ai appends & de-dupes; ai_rewrite replaces). Assert generated `maturity_signals` reference the control's `maturity_guide`. Assert `400` when strategy unset.
- **Frontend:** builder blocks generate until strategy chosen; config reaches `create` and `generateQuestionnaire` payloads.
- **Regression:** deterministic scoring path (`report_builder.py:179`) unchanged; existing `framework_only` assessments behave exactly as today.

---

## 7. Out of scope for Tier 1
- Switching the LLM provider (currently Groq/Llama-3.3-70b, `core/utils.py:215`). Worth a separate discussion if enterprise accuracy demands it.
- Runtime question branching / skip logic (Feature 9).
- Question weight editing (Feature 10).
- Per-domain prompt overrides (Feature 11).

---

## 8. Open questions for reviewer
1. Migration mechanism for the new `generation_config` column — is there an existing migration flow, or is `Base.metadata.create_all` acceptable here?
2. Should `custom_instructions` be capped (length) and sanitized before entering the system prompt?
3. For `ai_rewrite`, do you want to keep the original framework questions retrievable (audit trail), or fully replace?
