-- =============================================================================
-- Migration 007: Review Queue Indexes and Operator Notes
-- Phase 8 of the Italy Alpha Roadmap
--
-- Adds:
--   · review_notes column — free-text annotation by the reviewing operator
--   · Partial indexes on needs_review and review_status for fast queue queries
--   · Partial index on filings.status for failed-filing queue
--   · Partial index on unmatched_issuers.status for pending issuer queue
--
-- Safe to re-run.
-- Prerequisites: Migrations 001–006 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   ALTER TABLE transactions DROP COLUMN IF EXISTS review_notes;
--   DROP INDEX IF EXISTS idx_transactions_needs_review;
--   DROP INDEX IF EXISTS idx_transactions_review_status_pending;
--   DROP INDEX IF EXISTS idx_filings_status_failed;
--   DROP INDEX IF EXISTS idx_unmatched_issuers_pending;
-- =============================================================================


-- ─── Operator annotation column ───────────────────────────────────────────────
-- Free-text notes left by the reviewing operator.  Not version-controlled;
-- the full audit trail is in transaction_versions.

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS review_notes TEXT;


-- ─── Review queue indexes ─────────────────────────────────────────────────────

-- Fast lookup of all transactions that need attention (primary review queue).
CREATE INDEX IF NOT EXISTS idx_transactions_needs_review
    ON transactions(needs_review, created_at DESC)
    WHERE needs_review = TRUE;

-- Secondary filter: which of those have not yet been reviewed.
CREATE INDEX IF NOT EXISTS idx_transactions_review_status_pending
    ON transactions(review_status, created_at DESC)
    WHERE review_status IS NULL OR review_status IN ('pending_review', 'under_review');

-- Classification override queue.
CREATE INDEX IF NOT EXISTS idx_transactions_classification_override
    ON transactions(classification_override, updated_at DESC)
    WHERE classification_override = TRUE;


-- ─── Failed-filing queue index ────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_filings_status_failed
    ON filings(status, last_attempted_at DESC)
    WHERE status IN ('failed', 'skipped');


-- ─── Unmatched-issuer queue index ────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_unmatched_issuers_pending
    ON unmatched_issuers(status, created_at DESC)
    WHERE status = 'pending';


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    (SELECT COUNT(*) FROM transactions WHERE needs_review = TRUE)                   AS needs_review_total,
    (SELECT COUNT(*) FROM transactions WHERE review_status IS NULL AND needs_review = TRUE) AS unreviewed,
    (SELECT COUNT(*) FROM filings WHERE status IN ('failed', 'skipped'))            AS failed_filings,
    (SELECT COUNT(*) FROM unmatched_issuers WHERE status = 'pending')               AS pending_issuers;
