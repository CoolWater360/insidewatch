-- =============================================================================
-- Migration 019: Context Event Links
-- Phase 17A of the Italy Alpha Roadmap
--
-- context_event_links is the join table that connects insider transactions
-- (or issuers) to context events (ownership, governance, buyback, corporate).
-- Each row says "this context event is contextually relevant to this
-- transaction or issuer."
--
-- Design: concrete FK columns (not polymorphic)
-- ─────────────────────────────────────────────
-- The original draft design used a polymorphic pair (link_target_type TEXT,
-- link_target_id BIGINT) because it was simpler to write.  That design was
-- replaced with concrete FK columns (transaction_id, issuer_id) because:
--   • Postgres can enforce referential integrity on concrete FKs.
--   • A polymorphic pair requires application-level integrity checks and
--     cannot be indexed as efficiently.
--   • There are exactly two link targets (transactions, issuers); the
--     "open for extension" argument that favours polymorphism does not apply.
--
-- Mutual-exclusion enforced by CHECK:
--   (transaction_id IS NOT NULL) XOR (issuer_id IS NOT NULL)
--   i.e. exactly one of the two must be non-NULL.
--
-- context_type / context_id: a typed reference to the source event table.
-- This IS polymorphic because there are four event tables and adding a
-- concrete FK column per table would produce a sparse row.  There is no
-- DB-level FK for (context_type, context_id); application code must keep
-- these consistent.  A trigger or periodic audit query can detect orphans.
--
-- Natural event identity
-- ─────────────────────
-- "One current active link of a given type between a context event and a
-- target (transaction or issuer)."  Enforced by two partial unique indexes:
--
--   uidx_cel_tx_current
--     UNIQUE (context_type, context_id, link_type, transaction_id)
--     WHERE is_current = TRUE AND transaction_id IS NOT NULL
--
--   uidx_cel_issuer_current
--     UNIQUE (context_type, context_id, link_type, issuer_id)
--     WHERE is_current = TRUE AND issuer_id IS NOT NULL
--
-- ON DELETE choices
-- ─────────────────
--   transaction_id → transactions : CASCADE
--     A link is derived analytical context; if the source transaction is
--     deleted, the link loses its reason to exist.
--   issuer_id → issuers : CASCADE
--     Same reasoning.
--   source_id → context_sources : RESTRICT
--     Provenance must not be silently lost.
--
-- Safe to re-run.  Uses CREATE TABLE IF NOT EXISTS + IF NOT EXISTS indexes.
-- Prerequisites: Migrations 001–018 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   DROP TABLE IF EXISTS context_event_links;
-- =============================================================================


-- ─── context_event_links ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS context_event_links (
    id              BIGSERIAL PRIMARY KEY,

    -- ── Context event (polymorphic reference to one of four event tables) ─────
    -- context_type identifies the source table.
    context_type    TEXT NOT NULL CHECK (context_type IN (
        'ownership',    -- ownership_events
        'governance',   -- governance_events
        'buyback',      -- buyback_events
        'corporate'     -- corporate_events
    )),
    -- context_id is the PK of the referenced row in context_type's table.
    -- No DB-level FK (polymorphic); application must maintain consistency.
    context_id      BIGINT NOT NULL,

    -- ── Link target: exactly one must be non-NULL ──────────────────────────────
    -- CASCADE: if the transaction or issuer is deleted, remove the link.
    transaction_id  BIGINT REFERENCES transactions(id) ON DELETE CASCADE,
    issuer_id       BIGINT REFERENCES issuers(id)      ON DELETE CASCADE,

    -- Mutual-exclusion: exactly one of transaction_id / issuer_id must be set.
    CONSTRAINT chk_cel_exactly_one_target
        CHECK (
            (transaction_id IS NOT NULL AND issuer_id IS NULL)
            OR
            (transaction_id IS NULL     AND issuer_id IS NOT NULL)
        ),

    -- ── Link classification ───────────────────────────────────────────────────
    link_type       TEXT NOT NULL CHECK (link_type IN (
        'same_issuer',      -- context event and target share the same issuer
        'same_person',      -- context event and target share the same person / entity
        'causal',           -- context event is believed to causally explain the target
        'concurrent',       -- context event occurred in close proximity to the target
        'related_party',    -- context event involves a related party of the target
        'background',       -- general background / informational context
        'other'
    )),

    -- Evidence quality.
    confidence      TEXT NOT NULL DEFAULT 'heuristic_suggestion'
                        CHECK (confidence IN (
                            'parsed_fact',
                            'heuristic_suggestion',
                            'reviewer_confirmed'
                        )),

    -- Source that substantiates this link assertion (may differ from the
    -- source of the context event itself).
    -- RESTRICT: provenance must not be silently lost.
    source_id       BIGINT NOT NULL
                        REFERENCES context_sources(id) ON DELETE RESTRICT,

    -- Operator annotation.
    analyst_note    TEXT,

    -- Versioning.
    is_current      BOOLEAN NOT NULL DEFAULT TRUE,
    version_number  INT     NOT NULL DEFAULT 1,
    superseded_by   BIGINT  REFERENCES context_event_links(id) ON DELETE SET NULL,
    superseded_at   TIMESTAMPTZ,

    -- Review workflow.
    review_status   TEXT NOT NULL DEFAULT 'pending_review'
                        CHECK (review_status IN (
                            'pending_review', 'confirmed', 'rejected'
                        )),
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ── Lookup and timeline indexes ───────────────────────────────────────────────

-- Find all context events linked to a specific transaction.
CREATE INDEX IF NOT EXISTS idx_cel_transaction_id
    ON context_event_links(transaction_id)
    WHERE transaction_id IS NOT NULL;

-- Find all context events linked to a specific issuer.
CREATE INDEX IF NOT EXISTS idx_cel_issuer_id
    ON context_event_links(issuer_id)
    WHERE issuer_id IS NOT NULL;

-- Find all links to a specific context event.
CREATE INDEX IF NOT EXISTS idx_cel_context
    ON context_event_links(context_type, context_id);

-- Review queue.
CREATE INDEX IF NOT EXISTS idx_cel_review
    ON context_event_links(review_status)
    WHERE review_status = 'pending_review';

-- Source provenance lookup.
CREATE INDEX IF NOT EXISTS idx_cel_source
    ON context_event_links(source_id);


-- ── Natural event identity: partial unique indexes ────────────────────────────
--
-- One current link of a given type between a context event and a transaction.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_cel_tx_current
    ON context_event_links(context_type, context_id, link_type, transaction_id)
    WHERE is_current = TRUE AND transaction_id IS NOT NULL;

-- One current link of a given type between a context event and an issuer.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_cel_issuer_current
    ON context_event_links(context_type, context_id, link_type, issuer_id)
    WHERE is_current = TRUE AND issuer_id IS NOT NULL;


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    (SELECT COUNT(*) FROM context_event_links)                      AS context_event_links_rows,
    (SELECT COUNT(*) FROM information_schema.table_constraints
     WHERE table_schema    = 'public'
       AND table_name      = 'context_event_links'
       AND constraint_name = 'chk_cel_exactly_one_target')          AS mutual_exclusion_check_exists,
    (SELECT COUNT(*) FROM pg_indexes
     WHERE schemaname = 'public'
       AND tablename  = 'context_event_links'
       AND indexname  IN (
           'uidx_cel_tx_current',
           'uidx_cel_issuer_current'
       ))                                                            AS natural_identity_indexes;
-- Expected:
--   mutual_exclusion_check_exists = 1
--   natural_identity_indexes      = 2
