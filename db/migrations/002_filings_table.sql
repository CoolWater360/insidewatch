-- =============================================================================
-- Migration 002: Durable Filing Ledger
-- Phase 2 of the Italy Alpha Roadmap
--
-- Creates the filings table, which tracks every PDF document the scraper has
-- discovered, along with its processing status and retry history. Adds the FK
-- from transactions.source_filing_id → filings.id (the column was added as
-- nullable in migration 001 precisely so this FK could be deferred until now).
--
-- Safe to re-run: all statements use IF NOT EXISTS or DO/EXCEPTION guards.
--
-- Prerequisites:
--   · Migration 001_schema_integrity.sql applied and verified.
--   · SUPABASE_SERVICE_ROLE_KEY confirmed working in at least one scraper run
--     (scraper must be able to INSERT into filings, which requires bypassing RLS
--     once RLS is enabled in a future phase).
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   ALTER TABLE transactions DROP CONSTRAINT IF EXISTS fk_transactions_filing;
--   DROP TABLE IF EXISTS filings;
-- =============================================================================


-- ─── Table ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS filings (
    id                          BIGSERIAL PRIMARY KEY,

    -- Natural key — one row per unique PDF URL.
    pdf_url                     TEXT NOT NULL,

    -- Metadata from the Borsa Italiana listing page.
    filing_date                 DATE,
    company_name                TEXT,

    -- ── State machine ──────────────────────────────────────────────────────
    -- pending     → scraper has seen this URL but not yet attempted to process it
    -- in_progress → scraper has claimed it and is downloading/parsing now
    -- completed   → transactions successfully extracted and inserted
    -- failed      → last attempt errored; will be retried after next_attempt_after
    -- skipped     → permanently skipped (blocked PDF, no transactions, max retries hit)
    status                      TEXT NOT NULL DEFAULT 'pending',

    -- ── Retry tracking ─────────────────────────────────────────────────────
    attempt_count               INT NOT NULL DEFAULT 0,
    max_attempts                INT NOT NULL DEFAULT 3,
    last_attempted_at           TIMESTAMPTZ,
    -- Exponential backoff: scraper will not retry before this timestamp.
    -- NULL when status is not 'failed'.
    next_attempt_after          TIMESTAMPTZ,

    -- ── Outcome ────────────────────────────────────────────────────────────
    completed_at                TIMESTAMPTZ,
    transactions_inserted       INT DEFAULT 0,
    transactions_skipped_dedup  INT DEFAULT 0,

    -- ── Diagnostics ────────────────────────────────────────────────────────
    -- Last error message, truncated to 1000 chars.
    last_error                  TEXT,
    -- SHA-256 of the PDF bytes at the time of successful processing.
    -- Enables re-parse idempotency if the PDF is later found to have changed.
    pdf_sha256                  TEXT,
    -- Version of the scraper that last processed (or attempted to process) this filing.
    scraper_version             TEXT,

    -- ── Timestamps ─────────────────────────────────────────────────────────
    -- first_seen_at is set once on INSERT and never updated.
    first_seen_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ─── Constraints ─────────────────────────────────────────────────────────────

DO $$ BEGIN
    ALTER TABLE filings ADD CONSTRAINT filings_pdf_url_unique UNIQUE (pdf_url);
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'filings_pdf_url_unique already exists — skipping.';
END $$;

DO $$ BEGIN
    ALTER TABLE filings ADD CONSTRAINT chk_filing_status
        CHECK (status IN ('pending','in_progress','completed','failed','skipped'));
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'chk_filing_status already exists — skipping.';
END $$;

DO $$ BEGIN
    ALTER TABLE filings ADD CONSTRAINT chk_filing_max_attempts
        CHECK (max_attempts BETWEEN 1 AND 10);
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'chk_filing_max_attempts already exists — skipping.';
END $$;


-- ─── Indexes ──────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_filings_status
    ON filings(status);

-- Drives the retry query: failed filings whose backoff window has elapsed.
CREATE INDEX IF NOT EXISTS idx_filings_retryable
    ON filings(next_attempt_after)
    WHERE status = 'failed';

-- Drives the "have we seen this URL?" lookup in register_filing.
CREATE INDEX IF NOT EXISTS idx_filings_pdf_url
    ON filings(pdf_url);

CREATE INDEX IF NOT EXISTS idx_filings_company
    ON filings(company_name)
    WHERE company_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_filings_filing_date
    ON filings(filing_date DESC)
    WHERE filing_date IS NOT NULL;

-- Drives the stale-filing reaper:
--   WHERE status = 'in_progress' AND last_attempted_at < now() - stale_threshold
CREATE INDEX IF NOT EXISTS idx_filings_stale
    ON filings(last_attempted_at)
    WHERE status = 'in_progress';


-- ─── RPC: claim_filing ────────────────────────────────────────────────────────
-- Atomically claims a filing for processing. A single UPDATE … WHERE …
-- RETURNING * is atomic under PostgreSQL's row-level locking: if two concurrent
-- workers both call this for the same filing_id, exactly one will match the
-- WHERE clause and receive the row; the other will receive 0 rows.
--
-- Python callers MUST check whether a row was returned. Empty result = race lost;
-- the caller must not process the filing.
--
-- Claimable states:
--   · status = 'pending'
--     → new filing, first attempt
--   · status = 'failed' AND next_attempt_after <= now()
--     → retry window has elapsed
--   · status = 'in_progress' AND last_attempted_at < now() - stale_threshold
--     → previous worker crashed; stale-claim recovery path
--     (the reaper normally handles this at startup; this clause is defence-in-depth)
--
-- attempt_count is incremented atomically in the same statement. The returned
-- row reflects the post-increment value, which callers should use for all
-- subsequent fail_filing / complete_filing calls.

CREATE OR REPLACE FUNCTION claim_filing(
    p_filing_id     BIGINT,
    p_stale_minutes INT DEFAULT 30
) RETURNS SETOF filings
LANGUAGE plpgsql AS $$
DECLARE
    v_now TIMESTAMPTZ := now();
BEGIN
    RETURN QUERY
    UPDATE filings
    SET
        status             = 'in_progress',
        attempt_count      = attempt_count + 1,
        last_attempted_at  = v_now,
        next_attempt_after = NULL,
        updated_at         = v_now
    WHERE id = p_filing_id
      AND (
             status = 'pending'
          OR (status = 'failed'      AND next_attempt_after  <= v_now)
          OR (status = 'in_progress' AND last_attempted_at   <  v_now - make_interval(mins => p_stale_minutes))
      )
    RETURNING *;
END;
$$;


-- ─── RPC: reap_stale_filings ─────────────────────────────────────────────────
-- Recovers filings stuck in 'in_progress' for longer than p_stale_minutes.
-- Without this, a worker crash leaves the filing in_progress indefinitely:
-- is_eligible() returns False for in_progress, so it is never retried.
--
-- Transition rules:
--   · attempt_count <  max_attempts → status = 'failed', exponential backoff set
--   · attempt_count >= max_attempts → status = 'skipped', no further retry
--
-- Backoff uses the same formula as fail_filing:
--   delay = LEAST(2^attempt_count, 60) minutes
--
-- Should be called:
--   1. at listener/sweep startup (before processing any filings)
--   2. before get_retryable_filings() selects failed rows
--
-- Returns all affected rows (empty set if nothing was stale).

CREATE OR REPLACE FUNCTION reap_stale_filings(
    p_stale_minutes INT DEFAULT 30
) RETURNS SETOF filings
LANGUAGE plpgsql AS $$
DECLARE
    v_now TIMESTAMPTZ := now();
BEGIN
    RETURN QUERY
    UPDATE filings
    SET
        status             = CASE
                                 WHEN attempt_count >= max_attempts THEN 'skipped'
                                 ELSE 'failed'
                             END,
        last_error         = format(
                                 'Stale in_progress: no completion signal for %s min '
                                 '(attempt %s of %s). Likely a worker crash or runner timeout.',
                                 p_stale_minutes, attempt_count, max_attempts
                             ),
        next_attempt_after = CASE
                                 WHEN attempt_count >= max_attempts THEN NULL
                                 ELSE v_now + make_interval(mins => LEAST(POWER(2, attempt_count)::INT, 60))
                             END,
        updated_at         = v_now
    WHERE status = 'in_progress'
      AND last_attempted_at < v_now - make_interval(mins => p_stale_minutes)
    RETURNING *;
END;
$$;


-- ─── FK: transactions.source_filing_id → filings.id ─────────────────────────
-- This column was added as nullable BIGINT in migration 001 with no FK so that
-- migration 001 could be applied before the filings table existed. Now that the
-- table exists, we add the constraint. ON DELETE SET NULL means if a filing row
-- is manually removed, its transactions are not deleted — they just lose the
-- lineage link.

DO $$ BEGIN
    ALTER TABLE transactions
        ADD CONSTRAINT fk_transactions_filing
        FOREIGN KEY (source_filing_id)
        REFERENCES filings(id)
        ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'fk_transactions_filing already exists — skipping.';
END $$;


-- ─── Verification report ─────────────────────────────────────────────────────

SELECT
    'filings' AS tbl,
    COUNT(*) AS total_rows,
    COUNT(*) FILTER (WHERE status = 'pending')     AS pending,
    COUNT(*) FILTER (WHERE status = 'in_progress') AS in_progress,
    COUNT(*) FILTER (WHERE status = 'completed')   AS completed,
    COUNT(*) FILTER (WHERE status = 'failed')      AS failed,
    COUNT(*) FILTER (WHERE status = 'skipped')     AS skipped
FROM filings;
