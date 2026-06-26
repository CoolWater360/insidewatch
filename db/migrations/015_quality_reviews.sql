-- =============================================================================
-- Migration 015: Quality Reviews Truth Table and Issuer False-Match Tracking
-- Phase 13 of the Italy Alpha Roadmap
--
-- Problem:
--   Quality has been measured using confidence scores and review flags
--   (needs_review, review_status) which describe the parser's own uncertainty,
--   not ground truth.  There is no table recording what a human reviewer
--   actually decided, so accuracy cannot be measured over time and
--   confidence-score calibration cannot be verified.
--
--   Additionally, unmatched_issuers.status has no way to distinguish
--   "never matched" from "was incorrectly matched and needs re-review".
--
-- Solution:
--   quality_reviews   — one row per human review decision; snapshot of
--                        original parser output + corrected values.
--                        Forms the truth set for accuracy/calibration metrics.
--   unmatched_issuers.false_match_* — tracks cases where a previously
--                        resolved match turned out to be wrong, without
--                        resetting the resolved_to value.
--
-- Safe to re-run.
-- Prerequisites: Migrations 001–014 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   DROP TABLE IF EXISTS quality_reviews;
--   ALTER TABLE unmatched_issuers DROP COLUMN IF EXISTS false_match_issuer_id;
--   ALTER TABLE unmatched_issuers DROP COLUMN IF EXISTS false_match_flagged_at;
--   ALTER TABLE unmatched_issuers DROP COLUMN IF EXISTS false_match_flagged_by;
-- =============================================================================


-- ─── quality_reviews ─────────────────────────────────────────────────────────
--
-- One row per human review of a transaction's parser/classifier output.
-- The table forms the truth set for accuracy and calibration measurement.
--
-- Relationship to transaction_versions:
--   transaction_versions records changes to the transaction row (the "what
--   changed" history).  quality_reviews records the human decision that
--   triggered or validated a change, plus the original classifier output
--   at time of review (the "why" and "what the parser originally said").
--   The two tables complement each other; they are not duplicates.

CREATE TABLE IF NOT EXISTS quality_reviews (
    id                  BIGSERIAL PRIMARY KEY,

    -- Which transaction was reviewed.
    transaction_id      BIGINT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,

    -- Source document for this transaction (for fixture creation).
    source_filing_id    BIGINT REFERENCES filings(id) ON DELETE SET NULL,

    -- Parser version that produced the original output.
    parser_version      TEXT NOT NULL,

    -- ── Snapshot of original classifier output at review time ────────────────
    -- These values are captured at review time and never updated, so the
    -- truth set remains valid even after corrections are applied to the
    -- live transaction row.
    original_direction                  TEXT,
    original_transaction_type           TEXT,
    original_economic_intent            TEXT,
    original_extraction_confidence      DOUBLE PRECISION,
    original_classification_confidence  DOUBLE PRECISION,
    original_classification_rationale   TEXT,
    original_needs_review               BOOLEAN,
    original_review_reason              TEXT,

    -- ── Sampling category ────────────────────────────────────────────────────
    -- The quality concern that caused this transaction to be sampled for review.
    -- Drives category-level accuracy breakdown in the eval report.
    review_category     TEXT NOT NULL CHECK (review_category IN (
        'unknown_direction',   -- direction = 'unknown'
        'unknown_type',        -- transaction_type = 'unknown'
        'low_confidence',      -- extraction_confidence below threshold
        'corporate_action',    -- mechanical/non-discretionary event type
        'vehicle_entity',      -- holding vehicle, trust, nominee, related person
        'issuer_resolution',   -- company not linked to issuer master
        'other'                -- needs_review=true, not in the above categories
    )),

    -- ── Review decision ──────────────────────────────────────────────────────
    outcome             TEXT NOT NULL CHECK (outcome IN (
        'confirmed',  -- original parser output was correct
        'corrected',  -- one or more fields were wrong; corrections are below
        'rejected'    -- transaction record is invalid and should not exist
    )),

    -- ── Corrected values ─────────────────────────────────────────────────────
    -- NULL means the field was not the subject of a correction.
    -- When outcome='corrected', at least one corrected_* field is non-NULL.
    corrected_direction         TEXT,
    corrected_transaction_type  TEXT,
    corrected_economic_intent   TEXT,
    -- Free-text explanation of what was wrong and why the correction was made.
    correction_notes            TEXT,

    -- ── Review provenance ────────────────────────────────────────────────────
    reviewed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_by         TEXT        NOT NULL DEFAULT 'operator',

    -- ── Regression fixture pipeline ──────────────────────────────────────────
    -- fixture_eligible: operator has confirmed this correction should become a
    --   regression test.  Set via sample_review_queue --mark-fixture.
    -- fixture_created:  create_regression_fixture has written the JSON file.
    -- fixture_id:       slug used as the filename (e.g. 'unknown_dir_tx42').
    fixture_eligible    BOOLEAN NOT NULL DEFAULT FALSE,
    fixture_created     BOOLEAN NOT NULL DEFAULT FALSE,
    fixture_id          TEXT
);

-- Index: lookup by transaction (integrity checks, dedup)
CREATE INDEX IF NOT EXISTS idx_quality_reviews_transaction_id
    ON quality_reviews(transaction_id);

-- Index: accuracy breakdown by sampling category
CREATE INDEX IF NOT EXISTS idx_quality_reviews_category
    ON quality_reviews(review_category);

-- Index: outcome counts
CREATE INDEX IF NOT EXISTS idx_quality_reviews_outcome
    ON quality_reviews(outcome);

-- Index: per-version calibration analysis
CREATE INDEX IF NOT EXISTS idx_quality_reviews_parser_version
    ON quality_reviews(parser_version);

-- Partial index: fixture pipeline management
CREATE INDEX IF NOT EXISTS idx_quality_reviews_fixture_pending
    ON quality_reviews(fixture_eligible, fixture_created)
    WHERE fixture_eligible = TRUE AND fixture_created = FALSE;


-- ─── unmatched_issuers: false-match tracking ─────────────────────────────────
--
-- Distinguishes false-positive matches from genuinely unresolved entries.
--
-- A "false match" occurs when an entry was previously resolved to issuer X,
-- but X turned out to be incorrect.  Recording the wrong issuer separately
-- from resolved_to allows both:
--   a) re-linking to the correct issuer (update resolved_to)
--   b) blocking the same wrong suggestion from appearing again
--
-- false_match_issuer_id  — the issuer that was incorrectly linked
-- false_match_flagged_at — when the error was discovered
-- false_match_flagged_by — who flagged it

ALTER TABLE unmatched_issuers
    ADD COLUMN IF NOT EXISTS false_match_issuer_id   BIGINT REFERENCES issuers(id) ON DELETE SET NULL;

ALTER TABLE unmatched_issuers
    ADD COLUMN IF NOT EXISTS false_match_flagged_at  TIMESTAMPTZ;

ALTER TABLE unmatched_issuers
    ADD COLUMN IF NOT EXISTS false_match_flagged_by  TEXT;

-- Index: locate all known false matches (used by the resolver to avoid re-suggesting)
CREATE INDEX IF NOT EXISTS idx_unmatched_issuers_false_match
    ON unmatched_issuers(false_match_issuer_id)
    WHERE false_match_issuer_id IS NOT NULL;


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    (SELECT COUNT(*) FROM quality_reviews)                              AS quality_reviews,
    (SELECT COUNT(*) FROM quality_reviews WHERE outcome = 'confirmed')  AS confirmed,
    (SELECT COUNT(*) FROM quality_reviews WHERE outcome = 'corrected')  AS corrected,
    (SELECT COUNT(*) FROM quality_reviews WHERE outcome = 'rejected')   AS rejected,
    (SELECT COUNT(*) FROM quality_reviews WHERE fixture_eligible = TRUE) AS fixture_eligible,
    (SELECT COUNT(*) FROM unmatched_issuers WHERE false_match_issuer_id IS NOT NULL) AS false_matches;
