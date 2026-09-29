# CyberAI — Assessment Platform Database

Production-grade, normalized SQLite schema (PostgreSQL-portable) for an
enterprise cybersecurity GRC assessment platform. The database is **generated
from the framework JSON in `f_data/`**, which is the single source of truth.

> Full design rationale, ER diagram, and every table/column is in
> **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Requirements

- Python **3.10+** (standard library only — no third-party runtime deps).

## Quick start

```bash
python create_database.py       # build cyber_ai.db from schema.sql
python import_frameworks.py     # load every framework in f_data/
python verify_database.py       # integrity checks + statistics
```

Expected result: **5 frameworks, 152 nodes, 473 controls, 707 questions**, all
integrity checks passing.

### Options

```bash
python create_database.py --force        # drop and recreate the database
python import_frameworks.py --force       # re-import (replace) existing versions
python import_frameworks.py --verbose     # debug logging
```

Re-running `import_frameworks.py` without `--force` is safe — frameworks already
present (by `code` + `version`) are skipped.

## What the importer does

1. Scans `f_data/*.json`.
2. Picks the right **adapter** per file (auto-detects unknown files by shape):
   - `FlatAdapter` — `nist`, `iso`, `cis` (flat item arrays)
   - `PciAdapter` — `pci` (domain → sub-domain → control; de-duplicates PCI's
     controls, which the source lists twice)
   - `MarketAdapter` — `market_assesment` (rich; multi-question, maturity model)
3. Normalizes each to a shared intermediate representation (IR).
4. Writes frameworks, hierarchy nodes, controls, questions, evidence types, and
   maturity levels — **one transaction per file**, rolled back on any error.

Controls whose source has no question (all of PCI) get one clearly-flagged
synthesized question (`questions.is_synthesized = 1`) so the answer workflow is
uniform across frameworks.

## Adding a new framework

Drop the JSON in `f_data/` and run `import_frameworks.py`.
- Same shape as an existing file → it just works (auto-detected).
- A genuinely new shape → add one adapter class in `cyberai/adapters.py` and
  register it. **No schema change is ever required** — the hierarchy is recursive.

Add its filename → code mapping in `cyberai/settings.py::FILENAME_TO_CODE`.

## Project layout

```
schema.sql            canonical DDL (18 tables, indexes, seed roles)
create_database.py    entrypoint — build the database
import_frameworks.py  entrypoint — load frameworks
verify_database.py    entrypoint — verify + report
cyberai/              reusable package (settings, database, ir, adapters, importer)
f_data/               source framework JSON
ARCHITECTURE.md       full design document
```

## Schema at a glance

- **Catalog (library):** `frameworks → framework_nodes (recursive) → controls →
  questions`, plus `maturity_levels`, `control_evidence_types`.
- **Runtime (per-tenant):** `organizations, users, roles, user_roles,
  assessments, assessment_responses, evidence, attachments, ai_findings,
  recommendations, reports, audit_logs`.

Assessments **pin a framework version**, so updating the catalog never rewrites
historical assessments.

## PostgreSQL migration

The DDL avoids SQLite-only constructs (JSON stored as TEXT + `json_valid` CHECK →
`JSONB`; ISO-8601 TEXT timestamps → `TIMESTAMPTZ`; `INTEGER PK` → `IDENTITY`).
`cyberai/database.py` is the single connection seam to swap for
`psycopg`/SQLAlchemy. See ARCHITECTURE.md §15.
# CyberAI-DB
