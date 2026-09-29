-- =============================================================================
-- CyberAI — Enterprise Cybersecurity Assessment Platform
-- Canonical relational schema (SQLite dialect, PostgreSQL-portable)
-- -----------------------------------------------------------------------------
-- Design layers:
--   1. CATALOG  — versioned, read-mostly framework library generated from JSON.
--   2. RUNTIME  — per-tenant, mutable assessment lifecycle data.
--
-- Portability rules (so this migrates to PostgreSQL with minimal edits):
--   * Types used: INTEGER, TEXT, REAL. (PG: INTEGER/BIGINT, TEXT, DOUBLE PRECISION)
--   * Booleans stored as INTEGER 0/1 with CHECK (PG: BOOLEAN).
--   * Timestamps stored as TEXT ISO-8601 UTC (PG: TIMESTAMPTZ).
--   * JSON stored as TEXT with a valid-JSON CHECK (PG: JSONB).
--   * Enums enforced with CHECK constraints, not lookup tables.
--   * All FKs declare ON DELETE behaviour explicitly.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- =============================================================================
-- LAYER 1 — CATALOG (framework library)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- frameworks : one row per framework VERSION (NIST CSF, ISO 27001, PCI DSS 4.0…)
-- The (code, version) pair is the natural key. Assessments pin to frameworks.id,
-- so publishing a new version never mutates historical assessments.
-- -----------------------------------------------------------------------------
CREATE TABLE frameworks (
    id              INTEGER PRIMARY KEY,
    code            TEXT    NOT NULL,               -- stable machine code, e.g. 'NIST_CSF'
    name            TEXT    NOT NULL,               -- human name
    version         TEXT    NOT NULL DEFAULT '1.0',
    description     TEXT,
    source_format   TEXT,                           -- 'json' | 'pdf' | 'xlsx' (provenance)
    source_file     TEXT,                           -- original filename in f_data/
    external_uuid   TEXT,                           -- framework_id from source, if any
    scoring_scale   TEXT    CHECK (scoring_scale IS NULL OR json_valid(scoring_scale)),
    ingested_at     TEXT,                           -- source-declared ingest time
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (code, version)
);

-- -----------------------------------------------------------------------------
-- framework_nodes : recursive hierarchy (domain / category / sub_domain / …).
-- Replaces separate Domain & Category tables. parent_id NULL = root (domain).
-- node_type is descriptive only; the tree shape is data, not schema.
-- -----------------------------------------------------------------------------
CREATE TABLE framework_nodes (
    id                  INTEGER PRIMARY KEY,
    framework_id        INTEGER NOT NULL REFERENCES frameworks(id) ON DELETE CASCADE,
    parent_id           INTEGER REFERENCES framework_nodes(id) ON DELETE CASCADE,
    node_type           TEXT    NOT NULL,           -- 'domain' | 'category' | 'sub_domain'
    code                TEXT    NOT NULL,           -- source domain_id / category_id / sub_domain_id
    name                TEXT    NOT NULL,           -- source name (falls back to code)
    description         TEXT,
    criteria_statement  TEXT,                       -- Market category criteria
    sort_order          INTEGER NOT NULL DEFAULT 0,
    attributes          TEXT    CHECK (attributes IS NULL OR json_valid(attributes)),
    UNIQUE (framework_id, code)
);

-- -----------------------------------------------------------------------------
-- controls : atomic assessable item. node_id = its immediate parent group.
-- Universal fields are promoted to columns; framework-specific extras
-- (sub_topic, item_type, scope_cadence, maturity_guide, cross_references, …)
-- live in the JSON `attributes` column to avoid a wide sparse table.
-- -----------------------------------------------------------------------------
CREATE TABLE controls (
    id                  INTEGER PRIMARY KEY,
    framework_id        INTEGER NOT NULL REFERENCES frameworks(id) ON DELETE CASCADE,
    node_id             INTEGER NOT NULL REFERENCES framework_nodes(id) ON DELETE CASCADE,
    control_id          TEXT    NOT NULL,           -- source control_id, e.g. 'GV.OC-01', '7.1'
    name                TEXT,                        -- control_name (often empty)
    statement           TEXT    NOT NULL,           -- statement / control_statement
    requires_evidence   INTEGER NOT NULL DEFAULT 0 CHECK (requires_evidence IN (0,1)),
    sort_order          INTEGER NOT NULL DEFAULT 0,
    attributes          TEXT    CHECK (attributes IS NULL OR json_valid(attributes)),
    UNIQUE (framework_id, control_id)
);

-- -----------------------------------------------------------------------------
-- questions : 0..N per control. Flat frameworks yield one synthesized question;
-- Market yields several; PCI yields a synthesized maturity question.
-- -----------------------------------------------------------------------------
CREATE TABLE questions (
    id              INTEGER PRIMARY KEY,
    control_id      INTEGER NOT NULL REFERENCES controls(id) ON DELETE CASCADE,
    question_code   TEXT,                           -- source question_id (e.g. 'IAM-1-Q1')
    text            TEXT    NOT NULL,
    question_type   TEXT    NOT NULL DEFAULT 'FREE_TEXT',  -- YES_NO|FREE_TEXT|MULTI_CHOICE|MATURITY_SCALE|NUMERIC
    choices         TEXT    CHECK (choices IS NULL OR json_valid(choices)),
    help_text       TEXT,
    weight          REAL,
    is_synthesized  INTEGER NOT NULL DEFAULT 0 CHECK (is_synthesized IN (0,1)),
    sort_order      INTEGER NOT NULL DEFAULT 0
);

-- -----------------------------------------------------------------------------
-- maturity_levels : framework-level maturity model (Market's 0-3 scale, etc.).
-- Per-control maturity guidance is kept in controls.attributes.
-- -----------------------------------------------------------------------------
CREATE TABLE maturity_levels (
    id              INTEGER PRIMARY KEY,
    framework_id    INTEGER NOT NULL REFERENCES frameworks(id) ON DELETE CASCADE,
    level           INTEGER NOT NULL,
    code            TEXT,
    name            TEXT    NOT NULL,
    definition      TEXT,
    attributes      TEXT    CHECK (attributes IS NULL OR json_valid(attributes)),
    UNIQUE (framework_id, level)
);

-- -----------------------------------------------------------------------------
-- control_evidence_types : normalized expected_evidence_types[] list.
-- -----------------------------------------------------------------------------
CREATE TABLE control_evidence_types (
    id              INTEGER PRIMARY KEY,
    control_id      INTEGER NOT NULL REFERENCES controls(id) ON DELETE CASCADE,
    evidence_type   TEXT    NOT NULL,
    UNIQUE (control_id, evidence_type)
);

-- =============================================================================
-- LAYER 2 — RUNTIME (tenants, assessments, workflow)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- organizations : multi-tenant root.
-- -----------------------------------------------------------------------------
CREATE TABLE organizations (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    slug        TEXT    NOT NULL UNIQUE,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE users (
    id              INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email           TEXT    NOT NULL UNIQUE,
    full_name       TEXT,
    password_hash   TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE roles (
    id          INTEGER PRIMARY KEY,
    code        TEXT    NOT NULL UNIQUE,            -- 'admin' | 'assessor' | 'reviewer' | 'viewer'
    name        TEXT    NOT NULL,
    description TEXT
);

CREATE TABLE user_roles (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id     INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- -----------------------------------------------------------------------------
-- assessments : one engagement. Pins framework_id (a specific version).
-- -----------------------------------------------------------------------------
CREATE TABLE assessments (
    id              INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    framework_id    INTEGER NOT NULL REFERENCES frameworks(id) ON DELETE RESTRICT,
    name            TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','in_progress','under_review','completed','archived')),
    overall_score   REAL,
    created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    assigned_to     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    due_date        TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- -----------------------------------------------------------------------------
-- assessment_responses : one answer per (assessment, question).
-- -----------------------------------------------------------------------------
CREATE TABLE assessment_responses (
    id              INTEGER PRIMARY KEY,
    assessment_id   INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    question_id     INTEGER NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
    control_id      INTEGER NOT NULL REFERENCES controls(id) ON DELETE RESTRICT,  -- denormalized for reporting
    answer_value    TEXT,
    answer_score    REAL,
    maturity_level  INTEGER,
    notes           TEXT,
    status          TEXT    NOT NULL DEFAULT 'unanswered'
                        CHECK (status IN ('unanswered','answered','flagged','reviewed')),
    answered_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    answered_at     TEXT,
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (assessment_id, question_id)
);

-- -----------------------------------------------------------------------------
-- evidence : a logical evidence claim tied to an assessment + control.
-- attachments : the physical file(s) backing an evidence item (1 -> N).
-- -----------------------------------------------------------------------------
CREATE TABLE evidence (
    id              INTEGER PRIMARY KEY,
    assessment_id   INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    control_id      INTEGER NOT NULL REFERENCES controls(id) ON DELETE RESTRICT,
    response_id     INTEGER REFERENCES assessment_responses(id) ON DELETE SET NULL,
    title           TEXT    NOT NULL,
    description     TEXT,
    collected_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    collected_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE attachments (
    id              INTEGER PRIMARY KEY,
    evidence_id     INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    file_name       TEXT    NOT NULL,
    file_path       TEXT    NOT NULL,               -- local path now; object-store key later
    mime_type       TEXT,
    size_bytes      INTEGER,
    checksum_sha256 TEXT,
    uploaded_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- -----------------------------------------------------------------------------
-- ai_findings : output of the AI evaluation step.
-- -----------------------------------------------------------------------------
CREATE TABLE ai_findings (
    id              INTEGER PRIMARY KEY,
    assessment_id   INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    control_id      INTEGER REFERENCES controls(id) ON DELETE SET NULL,
    response_id     INTEGER REFERENCES assessment_responses(id) ON DELETE SET NULL,
    finding_type    TEXT    NOT NULL DEFAULT 'observation'
                        CHECK (finding_type IN ('gap','strength','observation','risk')),
    severity        TEXT    CHECK (severity IN ('info','low','medium','high','critical')),
    summary         TEXT    NOT NULL,
    detail          TEXT,
    confidence      REAL,
    model_name      TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- -----------------------------------------------------------------------------
-- recommendations : remediation guidance, optionally derived from a finding.
-- -----------------------------------------------------------------------------
CREATE TABLE recommendations (
    id              INTEGER PRIMARY KEY,
    assessment_id   INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    control_id      INTEGER REFERENCES controls(id) ON DELETE SET NULL,
    finding_id      INTEGER REFERENCES ai_findings(id) ON DELETE SET NULL,
    title           TEXT    NOT NULL,
    description     TEXT,
    priority        TEXT    CHECK (priority IN ('low','medium','high','critical')),
    effort          TEXT    CHECK (effort IN ('low','medium','high')),
    status          TEXT    NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','in_progress','resolved','accepted_risk','dismissed')),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- -----------------------------------------------------------------------------
-- reports : generated deliverables (permanent record of an assessment).
-- -----------------------------------------------------------------------------
CREATE TABLE reports (
    id              INTEGER PRIMARY KEY,
    assessment_id   INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    report_type     TEXT    NOT NULL DEFAULT 'summary',   -- 'summary' | 'detailed' | 'executive'
    format          TEXT    NOT NULL DEFAULT 'pdf',        -- 'pdf' | 'html' | 'json'
    title           TEXT    NOT NULL,
    file_path       TEXT,
    content         TEXT,                                  -- inline HTML/JSON if not a file
    score_snapshot  TEXT    CHECK (score_snapshot IS NULL OR json_valid(score_snapshot)),
    version         INTEGER NOT NULL DEFAULT 1,
    generated_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    generated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- -----------------------------------------------------------------------------
-- audit_logs : generic, append-only change history (absorbs "History").
-- -----------------------------------------------------------------------------
CREATE TABLE audit_logs (
    id              INTEGER PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    entity_type     TEXT    NOT NULL,               -- 'assessment' | 'response' | 'evidence' | …
    entity_id       INTEGER NOT NULL,
    action          TEXT    NOT NULL,               -- 'create' | 'update' | 'delete' | 'status_change'
    changes         TEXT    CHECK (changes IS NULL OR json_valid(changes)),  -- {before, after}
    ip_address      TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- =============================================================================
-- INDEXES — tuned for the platform's hot query paths.
-- (UNIQUE constraints above already create indexes; these cover FK lookups
--  and reporting filters that are not the leading column of a unique key.)
-- =============================================================================

-- Catalog traversal
CREATE INDEX idx_nodes_framework     ON framework_nodes(framework_id);
CREATE INDEX idx_nodes_parent        ON framework_nodes(parent_id);
CREATE INDEX idx_controls_framework  ON controls(framework_id);
CREATE INDEX idx_controls_node       ON controls(node_id);
CREATE INDEX idx_questions_control   ON questions(control_id);
CREATE INDEX idx_evtypes_control     ON control_evidence_types(control_id);
CREATE INDEX idx_maturity_framework  ON maturity_levels(framework_id);

-- Runtime
CREATE INDEX idx_users_org           ON users(organization_id);
CREATE INDEX idx_assessments_org     ON assessments(organization_id);
CREATE INDEX idx_assessments_fw      ON assessments(framework_id);
CREATE INDEX idx_assessments_status  ON assessments(status);
CREATE INDEX idx_responses_assess    ON assessment_responses(assessment_id);
CREATE INDEX idx_responses_control   ON assessment_responses(control_id);
CREATE INDEX idx_evidence_assess     ON evidence(assessment_id);
CREATE INDEX idx_evidence_control    ON evidence(control_id);
CREATE INDEX idx_attachments_ev      ON attachments(evidence_id);
CREATE INDEX idx_findings_assess     ON ai_findings(assessment_id);
CREATE INDEX idx_recos_assess        ON recommendations(assessment_id);
CREATE INDEX idx_reports_assess      ON reports(assessment_id);
CREATE INDEX idx_audit_entity        ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_org           ON audit_logs(organization_id);

-- =============================================================================
-- SEED — baseline RBAC roles (idempotent).
-- =============================================================================
INSERT OR IGNORE INTO roles (code, name, description) VALUES
    ('admin',    'Administrator', 'Full platform administration'),
    ('assessor', 'Assessor',      'Answers questions and uploads evidence'),
    ('reviewer', 'Reviewer',      'Reviews and approves responses'),
    ('viewer',   'Viewer',        'Read-only access to reports');
