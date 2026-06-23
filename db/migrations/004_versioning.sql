-- =============================================================================
-- Migration 004: Transaction Versioning and Processing Run Audit
-- Phase 4 of the Italy Alpha Roadmap
--
-- transaction_versions:
--   Preserves the full state of a transaction row at each point in time.
--   A version record is created before any update (re-parse or correction).
--   The snapshot column stores the complete row as JSONB, so future schema
--   additions do not require schema changes here.
--
-- filing_processing_runs:
--   Records each time a filing is processed (or re-processed).
--   Links to filings.id so the full processing history of a filing is queryable.
--
-- Safe to re-run.
--
-- Prerequisites:
--   Migrations 001, 002, 003 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   DROP TABLE IF EXISTS filing_processing_runs;
--   DROP TABLE IF EXISTS transaction_versions;
-- =============================================================================


-- ─── transaction_versions ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS transaction_versions (
    id              BIGSERIAL PRIMARY KEY,

    -- The transaction this version belongs to.
    transaction_id  BIGINT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,

    -- Version number at the time this snapshot was taken.
    -- Matches transactions.version_number before the update that created this record.
    version_number  INT NOT NULL,

    -- Full copy of the transactions row as it existed before the change that
    -- produced the next version.  Stored as JSONB for future-proof schema evolution.
    snapshot        JSONB NOT NULL,

    -- Who or what made the change that produced the next version.
    -- Examples: 'scraper', 'operator', 'parser_v2.1.0'
    changed_by      TEXT NOT NULL,

    -- Human-readable reason for the change.
    change_reason   TEXT,

    changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (transaction_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_tx_versions_transaction_id
    ON transaction_versions(transaction_id);


-- ─── filing_processing_runs ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS filing_processing_runs (
    id                      BIGSERIAL PRIMARY KEY,

    -- The filing that was (re-)processed.
    filing_id               BIGINT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,

    -- Parser version active during this run.
    parser_version          TEXT,

    -- Timestamp of this run.
    run_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- How many transactions did the parser produce?
    transactions_found      INT NOT NULL DEFAULT 0,

    -- New rows inserted.
    transactions_inserted   INT NOT NULL DEFAULT 0,

    -- Existing rows that changed (re-parse corrections or field updates).
    transactions_versioned  INT NOT NULL DEFAULT 0,

    -- Existing rows with no field changes (true idempotent re-ingest).
    transactions_unchanged  INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_processing_runs_filing_id
    ON filing_processing_runs(filing_id);


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    (SELECT COUNT(*) FROM transaction_versions)    AS version_records,
    (SELECT COUNT(*) FROM filing_processing_runs)  AS processing_runs;
