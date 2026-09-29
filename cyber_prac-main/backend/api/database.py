from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from backend.api.config import settings


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight migration: add columns introduced after a table already exists.
        from sqlalchemy import text
        for stmt in (
            "ALTER TABLE assessments ADD COLUMN selected_domains JSON",
            "ALTER TABLE assessments ADD COLUMN assigned_to VARCHAR(255)",
            "ALTER TABLE assessments ADD COLUMN generation_config JSON",
            "ALTER TABLE questions ADD COLUMN sub_topic VARCHAR(255)",
            "ALTER TABLE questions ADD COLUMN maturity_signals JSON",
            "ALTER TABLE questions ADD COLUMN gap_if_deficient TEXT",
            "ALTER TABLE controls ADD COLUMN category_id VARCHAR(36)",
            "ALTER TABLE followup_questions ADD COLUMN source VARCHAR(32) DEFAULT 'ai'",
            "ALTER TABLE followup_questions ADD COLUMN choices JSON",
            "ALTER TABLE followup_questions ADD COLUMN expected_evidence_types JSON",
            "ALTER TABLE followup_questions ADD COLUMN sub_topic VARCHAR(255)",
            "ALTER TABLE followup_questions ADD COLUMN maturity_signals JSON",
            "ALTER TABLE followup_questions ADD COLUMN gap_if_deficient TEXT",
            "ALTER TABLE category_ratings ADD COLUMN item_ratings JSON",
            # Direct market association on assessments (1:1 model)
            "ALTER TABLE assessments ADD COLUMN market_id VARCHAR(255)",
            "ALTER TABLE assessments ADD COLUMN market_label VARCHAR(255)",
            "ALTER TABLE document_requests ADD COLUMN provided_by VARCHAR(255)",
            "ALTER TABLE frameworks ADD COLUMN total_categories INTEGER DEFAULT 0",
            # Activity Log: audit_logs became the platform-wide event record.
            "ALTER TABLE audit_logs ADD COLUMN actor_name VARCHAR(255)",
            "ALTER TABLE audit_logs ADD COLUMN market VARCHAR(255)",
            "ALTER TABLE audit_logs ADD COLUMN summary TEXT",
            # Dotted event names ("assessment.findings_generated") outgrew VARCHAR(32).
            # No-op on SQLite (untyped), needed on Postgres.
            "ALTER TABLE audit_logs ALTER COLUMN action TYPE VARCHAR(64)",
            "CREATE INDEX ix_audit_created_at ON audit_logs (created_at)",
            "CREATE INDEX ix_audit_action ON audit_logs (action)",
            # Compliance evaluation: back-reference from a finding to the
            # evaluation that produced it. The compliance_evaluations table
            # itself is created by create_all above.
            "ALTER TABLE findings ADD COLUMN evaluation_id VARCHAR(36)",
            # Flat single-organization user model (legacy `market` column stays).
            "ALTER TABLE users ADD COLUMN organization VARCHAR(255)",
            # Evidence-gap targeting on follow-up questions. Without these a
            # question cannot name the control it closes, so a control-centric
            # assessment would fall back to asking questions nothing can answer.
            "ALTER TABLE followup_questions ADD COLUMN framework VARCHAR(64)",
            "ALTER TABLE followup_questions ADD COLUMN control_id VARCHAR(36)",
            "ALTER TABLE followup_questions ADD COLUMN evidence_gap TEXT",
            "ALTER TABLE followup_questions ADD COLUMN status VARCHAR(32) DEFAULT 'open'",
            "ALTER TABLE followup_questions ADD COLUMN response TEXT",
            "ALTER TABLE followup_questions ADD COLUMN response_score FLOAT",
            "ALTER TABLE followup_questions ADD COLUMN answered_at DATETIME",
            "ALTER TABLE followup_questions ADD COLUMN answered_by VARCHAR(255)",
        ):
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass  # column already exists

        # Backfill total_categories for frameworks imported before this column existed.
        # Counts only named (real) categories — synthetic pass-throughs have NULL name.
        await conn.execute(text("""
            UPDATE frameworks
            SET total_categories = (
                SELECT COUNT(*)
                FROM categories
                WHERE categories.framework_id = frameworks.id
                  AND categories.name IS NOT NULL
                  AND categories.name != ''
            )
            WHERE total_categories = 0
        """))
