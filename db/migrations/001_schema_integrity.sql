-- =============================================================================
-- Migration 001: Schema Integrity, Security, and Backfill
-- Phase 1 of the Italy Alpha Roadmap
--
-- Safe to re-run: all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING
-- or are UPDATE-idempotent via WHERE guards.
--
-- Apply in Supabase SQL Editor (Dashboard → SQL Editor → New Query → Run).
-- Expected runtime on a production dataset of ~1,300 transactions: < 2 s.
--
-- Rollback:
--   ALTER TABLE transactions DROP COLUMN IF EXISTS economic_intent;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS extraction_confidence;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS classification_confidence;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS review_status;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS review_reason;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS source_filing_id;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS source_transaction_index;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS raw_document_sha256;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS parser_version;
-- =============================================================================


-- ─── Section 1: Backfill legacy NULL values (must run before NOT NULL) ────────

-- 1a. Fix NULLs in transaction_type.
--     The existing migration_phase1.sql set these correctly for rows that
--     existed at the time it was applied. Any rows inserted before that
--     migration, or with a code path that didn't set the field, may still
--     have NULLs. Do not assume open-market — use 'other' when the
--     direction/price context is ambiguous.
UPDATE transactions
SET transaction_type = CASE
    WHEN direction = 'buy'  AND total_value > 0                  THEN 'buy'
    WHEN direction = 'sell' AND total_value > 0                  THEN 'sell'
    WHEN unit_price = 0 AND total_value = 0 AND quantity > 0     THEN 'grant'
    WHEN direction = 'unknown'                                   THEN 'other'
    ELSE 'other'
END
WHERE transaction_type IS NULL;

-- 1b. Fix NULLs in needs_review.
UPDATE transactions
SET needs_review = FALSE
WHERE needs_review IS NULL;


-- ─── Section 2: Strengthen existing column constraints ────────────────────────

-- 2a. Make transaction_type NOT NULL now that NULLs are gone.
ALTER TABLE transactions
    ALTER COLUMN transaction_type SET NOT NULL,
    ALTER COLUMN transaction_type SET DEFAULT 'buy';

-- 2b. Make needs_review NOT NULL.
ALTER TABLE transactions
    ALTER COLUMN needs_review SET NOT NULL,
    ALTER COLUMN needs_review SET DEFAULT FALSE;

-- 2c. Add CHECK constraint on transaction_type (idempotent via DO block).
DO $$ BEGIN
    ALTER TABLE transactions ADD CONSTRAINT chk_transaction_type
        CHECK (transaction_type IN (
            'buy', 'sell', 'grant', 'option_exercise',
            'sell_to_cover', 'other'
        ));
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'chk_transaction_type already exists — skipping.';
END $$;

-- 2d. Add CHECK constraint on direction (was in schema.sql but may be absent
--     from live DB if the table was altered later).
DO $$ BEGIN
    ALTER TABLE transactions ADD CONSTRAINT chk_direction
        CHECK (direction IN ('buy', 'sell', 'unknown'));
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'chk_direction already exists — skipping.';
END $$;


-- ─── Section 3: New columns ───────────────────────────────────────────────────

-- Discretionary intent layer (Phase 7 will add full rules engine).
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS economic_intent TEXT DEFAULT 'unclear';

-- Rule-based confidence scores (Phase 5 will add full calculation).
-- Stored as REAL (4-byte float) — sufficient for 0.0–1.0 scores.
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS extraction_confidence REAL DEFAULT NULL;

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS classification_confidence REAL DEFAULT NULL;

-- Internal review workflow state.
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS review_status TEXT DEFAULT NULL;

-- Structured tag explaining why a record needs review.
-- Examples: 'ambiguous_direction', 'missing_issuer', 'truncated_name'
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS review_reason TEXT DEFAULT NULL;

-- Source filing linkage (FK to filings table, created in Phase 2).
-- Added as nullable here; FK constraint will be added in migration 002.
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS source_filing_id BIGINT DEFAULT NULL;

-- Position of this transaction within the source PDF (0-indexed).
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS source_transaction_index INT DEFAULT NULL;

-- SHA-256 of the PDF bytes at the time of ingestion.
-- Enables document integrity verification and re-parse idempotency.
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS raw_document_sha256 TEXT DEFAULT NULL;

-- Version string of the parser that produced this record.
-- Format: '<major>.<minor>.<patch>'  e.g. '1.0.0'
-- Legacy records (pre-Phase 1) receive '0.0.0'.
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS parser_version TEXT DEFAULT NULL;


-- ─── Section 4: CHECK constraints on new columns ─────────────────────────────

DO $$ BEGIN
    ALTER TABLE transactions ADD CONSTRAINT chk_economic_intent
        CHECK (economic_intent IN ('discretionary', 'mechanical', 'unclear'));
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'chk_economic_intent already exists — skipping.';
END $$;

DO $$ BEGIN
    ALTER TABLE transactions ADD CONSTRAINT chk_extraction_confidence
        CHECK (extraction_confidence IS NULL
            OR (extraction_confidence >= 0.0 AND extraction_confidence <= 1.0));
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'chk_extraction_confidence already exists — skipping.';
END $$;

DO $$ BEGIN
    ALTER TABLE transactions ADD CONSTRAINT chk_classification_confidence
        CHECK (classification_confidence IS NULL
            OR (classification_confidence >= 0.0 AND classification_confidence <= 1.0));
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'chk_classification_confidence already exists — skipping.';
END $$;

DO $$ BEGIN
    ALTER TABLE transactions ADD CONSTRAINT chk_review_status
        CHECK (review_status IS NULL OR review_status IN (
            'pending_review', 'under_review', 'confirmed', 'rejected', 'corrected'
        ));
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'chk_review_status already exists — skipping.';
END $$;


-- ─── Section 5: Backfill new columns for existing records ────────────────────

-- 5a. economic_intent — derived from transaction_type.
--     Only touches rows where economic_intent has never been set (IS NULL).
--     Does NOT overwrite 'unclear' — a future manual or Phase 5 reclassification
--     must survive a re-run of this migration without being clobbered.
UPDATE transactions
SET economic_intent = CASE
    WHEN transaction_type IN ('buy', 'sell')                               THEN 'discretionary'
    WHEN transaction_type IN ('grant', 'option_exercise', 'sell_to_cover') THEN 'mechanical'
    ELSE 'unclear'
END
WHERE economic_intent IS NULL;

-- 5b. review_status — derived from needs_review flag.
UPDATE transactions
SET review_status = CASE
    WHEN needs_review = TRUE  THEN 'pending_review'
    ELSE                           'confirmed'
END
WHERE review_status IS NULL;

-- 5c. parser_version — legacy records get sentinel '0.0.0'.
UPDATE transactions
SET parser_version = '0.0.0'
WHERE parser_version IS NULL;

-- 5d. Coarse extraction_confidence for legacy records.
--     Phase 5 will recalculate these with a proper per-field scoring model.
--     0.8 = well-formed record that passed validation (needs_review=false)
--     0.4 = record flagged for review (ambiguous parse)
UPDATE transactions
SET extraction_confidence = CASE
    WHEN needs_review = FALSE THEN 0.8
    ELSE                           0.4
END
WHERE extraction_confidence IS NULL;

-- 5e. Coarse classification_confidence for legacy records.
--     0.9 = known direction + specific transaction type
--     0.6 = known direction but type is 'other'
--     0.3 = unknown direction
UPDATE transactions
SET classification_confidence = CASE
    WHEN direction IN ('buy','sell') AND transaction_type NOT IN ('other') THEN 0.9
    WHEN direction IN ('buy','sell') AND transaction_type = 'other'        THEN 0.6
    ELSE 0.3
END
WHERE classification_confidence IS NULL;


-- ─── Section 6: New indexes for query patterns ────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_transactions_review_status
    ON transactions(review_status);

CREATE INDEX IF NOT EXISTS idx_transactions_economic_intent
    ON transactions(economic_intent);

CREATE INDEX IF NOT EXISTS idx_transactions_needs_review
    ON transactions(needs_review)
    WHERE needs_review = TRUE;

CREATE INDEX IF NOT EXISTS idx_transactions_source_filing
    ON transactions(source_filing_id)
    WHERE source_filing_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_transactions_filed_date
    ON transactions(filed_date DESC);


-- ─── Section 7: Insiders table — ensure columns exist ────────────────────────

-- These were added in migration_phase4.sql but may be absent from schema.sql.
-- Safe to re-run via IF NOT EXISTS.
ALTER TABLE insiders
    ADD COLUMN IF NOT EXISTS insider_verified BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS role_category     TEXT    DEFAULT 'other';

DO $$ BEGIN
    ALTER TABLE insiders ADD CONSTRAINT chk_role_category
        CHECK (role_category IN (
            'executive', 'board', 'major_shareholder', 'related_person', 'other'
        ));
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'chk_role_category already exists — skipping.';
END $$;


-- ─── Section 8: Companies table — ensure columns exist ───────────────────────

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS priority_tier INT DEFAULT 2;

DO $$ BEGIN
    ALTER TABLE companies ADD CONSTRAINT chk_priority_tier
        CHECK (priority_tier BETWEEN 1 AND 3);
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'chk_priority_tier already exists — skipping.';
END $$;


-- ─── Section 9: Verification report ─────────────────────────────────────────
-- Run this block after applying the migration to confirm the results.

SELECT
    'transactions' AS tbl,
    COUNT(*)                                                   AS total_rows,
    COUNT(*) FILTER (WHERE transaction_type IS NULL)           AS null_type,
    COUNT(*) FILTER (WHERE needs_review IS NULL)               AS null_review_flag,
    COUNT(*) FILTER (WHERE economic_intent IS NULL)            AS null_intent,
    COUNT(*) FILTER (WHERE review_status IS NULL)              AS null_review_status,
    COUNT(*) FILTER (WHERE parser_version IS NULL)             AS null_parser_version,
    COUNT(*) FILTER (WHERE review_status = 'pending_review')   AS pending_review,
    COUNT(*) FILTER (WHERE review_status = 'confirmed')        AS confirmed
FROM transactions;
