-- =============================================================================
-- Migration 006: Extended Classification Taxonomy and Rationale Storage
-- Phase 7 of the Italy Alpha Roadmap
--
-- Problem with the old taxonomy:
--   The six-value transaction_type (buy | sell | grant | option_exercise |
--   sell_to_cover | other) collapses economically distinct events:
--   - Subscriptions to rights issues look like open-market buys.
--   - Gifts look like sells.
--   - Inheritance looks like "other".
--   No rationale is stored, making classifications opaque.
--
-- Solution:
--   Extended 13-value taxonomy covers every MAR Art 19 event class observed
--   in Borsa Italiana filings.  A classification_rationale TEXT column stores
--   the rule that produced the type.  raw_nature_text preserves the raw
--   section-4b text from the PDF for audit and re-classification.
--   Three override columns track manual corrections with full attribution.
--
-- Safe to re-run: all ALTER TABLE statements use IF NOT EXISTS.
-- The CHECK constraint is recreated with the new value list.
--
-- Prerequisites:
--   Migrations 001–005 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   ALTER TABLE transactions DROP COLUMN IF EXISTS classification_rationale;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS raw_nature_text;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS classification_override;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS classification_overridden_by;
--   ALTER TABLE transactions DROP COLUMN IF EXISTS classification_overridden_at;
--   (Then restore the original CHECK constraint manually.)
-- =============================================================================


-- ─── Step 1: Drop the old transaction_type CHECK constraint ──────────────────
--
-- PostgreSQL auto-names inline CHECK constraints as <table>_<col>_check.
-- We try the expected name first; swallow the error if it does not exist.

DO $$ BEGIN
    ALTER TABLE transactions DROP CONSTRAINT transactions_transaction_type_check;
EXCEPTION WHEN undefined_object THEN
    RAISE NOTICE 'transactions_transaction_type_check does not exist — skipping drop.';
END $$;


-- ─── Step 2: Re-add CHECK with the extended 13-value taxonomy ─────────────────
--
-- Extended values vs. Phase 1:
--   subscription  — rights issue or capital-increase subscription (was 'buy')
--   conversion    — convertible instrument → equity exchange      (was 'other')
--   inheritance   — shares acquired through succession            (was 'other')
--   gift_in       — shares received as a gift / donation          (was 'other')
--   gift_out      — shares given away as a gift / donation        (was 'sell')
--   transfer_in   — inbound portfolio/custody account transfer    (was 'other')
--   transfer_out  — outbound portfolio/custody account transfer   (was 'sell')
--
-- 'other' is retained for events that cannot be mapped to any named class.

DO $$ BEGIN
    ALTER TABLE transactions ADD CONSTRAINT transactions_transaction_type_check
        CHECK (transaction_type IN (
            'buy', 'sell', 'grant', 'option_exercise', 'sell_to_cover',
            'subscription', 'conversion', 'inheritance',
            'gift_in', 'gift_out', 'transfer_in', 'transfer_out',
            'other'
        ));
EXCEPTION WHEN duplicate_object THEN
    RAISE NOTICE 'transactions_transaction_type_check already exists — skipping add.';
END $$;


-- ─── Step 3: New columns ──────────────────────────────────────────────────────

-- Human-readable explanation of why this classification was chosen.
-- Format: "<rule_name>: <detail>"  e.g. "zero_price_grant: unit_price=0, qty=5000"
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS classification_rationale TEXT;

-- Raw section-4b text extracted from the PDF (the "Natura dell'operazione" field).
-- Preserved verbatim for audit, re-classification, and regulator queries.
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS raw_nature_text TEXT;

-- Set TRUE when an operator has manually overridden the parser/classifier output.
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS classification_override
    BOOLEAN NOT NULL DEFAULT FALSE;

-- Who performed the override (e.g. 'operator', 'pipeline_v2').
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS classification_overridden_by TEXT;

-- When the override was applied.
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS classification_overridden_at TIMESTAMPTZ;


-- ─── Step 4: Index on classification_override for ops queue ──────────────────

CREATE INDEX IF NOT EXISTS idx_transactions_classification_override
    ON transactions(classification_override)
    WHERE classification_override = TRUE;


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    COUNT(*)                                                          AS total,
    COUNT(*) FILTER (WHERE transaction_type = 'buy')                 AS buys,
    COUNT(*) FILTER (WHERE transaction_type = 'sell')                AS sells,
    COUNT(*) FILTER (WHERE transaction_type = 'grant')               AS grants,
    COUNT(*) FILTER (WHERE transaction_type = 'option_exercise')     AS option_exercises,
    COUNT(*) FILTER (WHERE transaction_type = 'sell_to_cover')       AS sell_to_covers,
    COUNT(*) FILTER (WHERE transaction_type = 'subscription')        AS subscriptions,
    COUNT(*) FILTER (WHERE transaction_type = 'conversion')          AS conversions,
    COUNT(*) FILTER (WHERE transaction_type = 'inheritance')         AS inheritances,
    COUNT(*) FILTER (WHERE transaction_type IN ('gift_in','gift_out'))AS gifts,
    COUNT(*) FILTER (WHERE transaction_type IN ('transfer_in','transfer_out')) AS transfers,
    COUNT(*) FILTER (WHERE transaction_type = 'other')               AS other,
    COUNT(*) FILTER (WHERE classification_override = TRUE)           AS overrides,
    COUNT(*) FILTER (WHERE classification_rationale IS NOT NULL)     AS with_rationale
FROM transactions;
