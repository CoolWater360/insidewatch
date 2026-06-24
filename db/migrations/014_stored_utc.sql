-- =============================================================================
-- Migration 014: stored_utc — download-to-stored latency stage
-- =============================================================================
-- Adds stored_utc to the filings table.  This column is stamped by
-- record_storage() immediately after the storage metadata row is confirmed
-- written to the DB, enabling the download→stored latency stage.
--
-- Pipeline stage coverage after this migration:
--   source_published_utc → discovered_utc     publication → detection
--   discovered_utc       → downloaded_utc     detection   → download
--   downloaded_utc       → stored_utc         download    → stored     ← NEW
--   stored_utc           → parsed_utc         stored      → parsed     ← NEW
--   parsed_utc           → delivered_utc      parsed      → delivered
--   source_published_utc → delivered_utc      end-to-end  (wall time)
--
-- Historical filings (before this migration) will have stored_utc = NULL.
-- Those rows contribute to publication→detection, detection→download,
-- and parsed→delivered but NOT to the two new stages.
--
-- Backfill for historical filings:
--   Run: python3 -m scraper.cli verify-storage-lineage
--   With --fix, missing-evidence filings are reset to 'failed' so the scraper
--   re-downloads them; on re-processing, stored_utc will be stamped prospectively.
--
-- Safe to re-run (IF NOT EXISTS guards).
-- Apply via Supabase Dashboard → SQL Editor, or:
--   psql $DATABASE_URL -f db/migrations/014_stored_utc.sql
-- =============================================================================

ALTER TABLE filings ADD COLUMN IF NOT EXISTS stored_utc TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_filings_stored_utc
    ON filings(stored_utc DESC)
    WHERE stored_utc IS NOT NULL;

-- Verification:
-- SELECT
--     COUNT(*)                                                         AS total_completed,
--     COUNT(*) FILTER (WHERE stored_utc IS NOT NULL)                  AS have_stored_utc,
--     COUNT(*) FILTER (WHERE stored_utc IS NULL AND storage_path IS NOT NULL) AS missing_stored_utc
-- FROM filings
-- WHERE status = 'completed';
-- Expected after prospective stamping: have_stored_utc increases with each processed filing.
