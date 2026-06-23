-- =============================================================================
-- Migration 003: Transaction Identity Redesign
-- Phase 4 of the Italy Alpha Roadmap
--
-- Problem with the old raw_hash:
--   SHA256(insider_name|company|tx_date|quantity|unit_price)
--   Two legitimate same-day same-price same-insider transactions (e.g. two
--   batches in one PDF) produce the identical hash → the second is silently
--   dropped.  This is data loss that looks like correct deduplication.
--
-- Solution — identity_hash:
--   SHA256(source_filing_id|pdf_sha256|source_transaction_index|isin|direction)
--   Every transaction is uniquely positioned by its location within its source
--   filing.  Same-day same-price transactions are distinct because they occupy
--   different indices.  Reprocessing the same filing with the same parser
--   produces identical identity_hashes → true idempotent re-ingest.
--
-- raw_hash is retained (NOT NULL, value unchanged) for forensic comparison but
-- loses its UNIQUE constraint — it is no longer the deduplication key.
--
-- Also adds versioning columns so the re-ingest and correction paths in Phase 4
-- can track changes over time.
--
-- Safe to re-run: all statements are guarded with IF NOT EXISTS or DO/EXCEPTION.
--
-- Prerequisites:
--   Migrations 001 and 002 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   ALTER TABLE transactions DROP COLUMN IF EXISTS identity_hash;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS is_current;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS version_number;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS superseded_by;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS superseded_at;
--   ALTER TABLE transactions ADD CONSTRAINT transactions_raw_hash_key UNIQUE (raw_hash);
-- =============================================================================


-- ─── Step 1: Add identity_hash column ────────────────────────────────────────

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS identity_hash TEXT;


-- ─── Step 2: Backfill identity_hash for all existing rows ────────────────────
--
-- Two-branch formula:
--
--   Full lineage (source_filing_id + raw_document_sha256 + source_transaction_index
--   all present) → new formula: SHA256(filing_id|sha256|index|isin|direction).
--   This is deterministic and will produce the same value when the same filing
--   is reprocessed by the same parser.
--
--   Missing lineage (pre-Phase-2 or partial) → 'legacy_' || raw_hash.
--   The old raw_hash was already unique per UNIQUE constraint, so prefixing it
--   preserves uniqueness while distinguishing these rows from new-formula rows.

UPDATE transactions
SET identity_hash = CASE
    WHEN source_filing_id      IS NOT NULL
     AND raw_document_sha256   IS NOT NULL
     AND source_transaction_index IS NOT NULL
    THEN encode(
             sha256((
                 source_filing_id::text      || '|' ||
                 raw_document_sha256         || '|' ||
                 source_transaction_index::text || '|' ||
                 COALESCE(isin,      'no_isin') || '|' ||
                 COALESCE(direction, 'no_dir')
             )::bytea),
             'hex'
         )
    ELSE 'legacy_' || raw_hash
END
WHERE identity_hash IS NULL;


-- ─── Step 3: Enforce NOT NULL (all rows now have a value) ─────────────────────

ALTER TABLE transactions ALTER COLUMN identity_hash SET NOT NULL;


-- ─── Step 4: Drop old UNIQUE constraint from raw_hash ─────────────────────────
--
-- raw_hash is retained for forensic comparison but is no longer the dedup key.

DO $$ BEGIN
    ALTER TABLE transactions DROP CONSTRAINT transactions_raw_hash_key;
EXCEPTION WHEN undefined_object THEN
    RAISE NOTICE 'transactions_raw_hash_key does not exist — skipping.';
END $$;


-- ─── Step 5: Unique constraint + index on identity_hash ───────────────────────

DO $$ BEGIN
    ALTER TABLE transactions ADD CONSTRAINT transactions_identity_hash_unique UNIQUE (identity_hash);
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'transactions_identity_hash_unique already exists — skipping.';
END $$;

CREATE INDEX IF NOT EXISTS idx_transactions_identity_hash ON transactions(identity_hash);


-- ─── Step 6: Versioning columns ───────────────────────────────────────────────
--
-- is_current:      False when a row has been superseded by a correction or re-parse.
-- version_number:  Incremented on every correction or re-parse that changes data.
-- superseded_by:   FK to the row that replaced this one (NULL if still current).
-- superseded_at:   When this row was superseded.

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_current      BOOLEAN   NOT NULL DEFAULT TRUE;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS version_number  INT       NOT NULL DEFAULT 1;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS superseded_by   BIGINT    REFERENCES transactions(id);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS superseded_at   TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_transactions_is_current
    ON transactions(is_current)
    WHERE is_current = FALSE;   -- only useful for finding superseded rows


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    COUNT(*)                                    AS total_transactions,
    COUNT(*) FILTER (WHERE identity_hash LIKE 'legacy_%') AS legacy_identity,
    COUNT(*) FILTER (WHERE identity_hash NOT LIKE 'legacy_%') AS new_identity,
    COUNT(*) FILTER (WHERE identity_hash IS NULL)          AS null_identity,
    COUNT(*) FILTER (WHERE is_current = FALSE)             AS superseded
FROM transactions;
