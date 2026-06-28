-- =============================================================================
-- Migration 016: Context Sources and Entities
-- Phase 17A of the Italy Alpha Roadmap
--
-- Creates the two foundational tables for the Phase 17 context data layer:
--
--   context_sources — primary-source provenance registry.  Every context
--     event (ownership, governance, buyback, corporate) must reference a
--     context_sources row via NOT NULL FK.  Stores canonical URL, type,
--     publisher, publication + discovery timestamps, optional document hash,
--     and an internal storage_path / raw_text pair.
--
--     The storage_path and raw_text columns are INTERNAL ONLY.  A public
--     view (context_sources_public) exposes all other columns and must be
--     used by any API endpoint or external tool.
--
--   entities — legal entities in the ownership and governance structure.
--     Distinct from insiders (MAR Art 19 declarants linked to a specific
--     company) and from issuers (listed companies / securities issuers).
--     Covers natural persons, companies, holding vehicles, trusts, nominees,
--     foundations, and funds.
--
-- Natural event identity
-- ─────────────────────
--   context_sources : source_url (UNIQUE).  One row per canonical URL.
--   entities        : lei (UNIQUE where NOT NULL).  No compound name +
--     jurisdiction unique key: Italian company names are not globally
--     unique, so entity deduplication requires human review.
--
-- ON DELETE choices (all NEW FKs — no existing FKs modified)
-- ──────────────────────────────────────────────────────────
--   context_sources.issuer_id → issuers  : SET NULL
--     A source record survives if its issuer link is later cleared; the
--     source_url is the primary identity, not the issuer FK.
--   entities.issuer_id → issuers         : SET NULL
--     Entity record survives; the FK is advisory (not the entity's identity).
--   entities.insider_id → insiders       : SET NULL
--     Same reasoning.
--
-- Safe to re-run.  All DDL uses CREATE TABLE IF NOT EXISTS.
-- Prerequisites: Migrations 001–015 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   DROP VIEW  IF EXISTS context_sources_public;
--   DROP TABLE IF EXISTS entities;
--   DROP TABLE IF EXISTS context_sources;
-- =============================================================================


-- ─── context_sources ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS context_sources (
    id                    BIGSERIAL PRIMARY KEY,

    -- Canonical URL of the source document.  Natural identity / dedup key.
    source_url            TEXT NOT NULL,

    -- Controlled vocabulary for source classification.
    source_type           TEXT NOT NULL CHECK (source_type IN (
        'regulatory_filing',  -- CONSOB / Borsa Italiana mandatory disclosure
        'exchange_notice',    -- Borsa Italiana avviso (incl. buyback weekly reports)
        'annual_report',      -- annual or interim report filed by issuer
        'press_release',      -- company investor-relations press release
        'prospectus',         -- IPO, rights issue, or capital-increase prospectus
        'governance_notice',  -- board appointment / resignation announcement
        'official_gazette',   -- Gazzetta Ufficiale della Repubblica Italiana
        'operator_entry',     -- manually entered by an InsideWatch operator
        'other'
    )),

    -- Publisher identity (e.g. 'Borsa Italiana', 'CONSOB', issuer canonical name).
    publisher             TEXT,

    -- Document title as it appears on the source site.
    document_title        TEXT,

    -- When the source was originally published.
    -- NULL is acceptable for historical records where the date is unknown;
    -- populate from source metadata wherever possible.
    publication_timestamp TIMESTAMPTZ,

    -- When InsideWatch first discovered and recorded this source.
    -- Set on INSERT and never updated thereafter.
    discovered_timestamp  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- SHA-256 hex of the downloaded bytes.
    -- NULL if the document was not preserved locally.
    document_hash         TEXT,

    -- ── INTERNAL ONLY — excluded from context_sources_public view ─────────────
    --
    -- storage_path: path in the internal storage backend
    --   (same convention as filings.storage_path, e.g.
    --   context/<year>/<month>/<sha256>.pdf).
    --   Never expose in public-facing API responses.
    storage_path          TEXT,
    --
    -- raw_text: full extracted text from the source document.
    --   Stored for re-parse, audit, and source-evidence lookup.
    --   May be very large; TOAST-compressed automatically by Postgres.
    --   Never expose in public-facing API responses.
    raw_text              TEXT,
    -- ── END INTERNAL ──────────────────────────────────────────────────────────

    -- Optional FK to the issuer this source primarily concerns.
    -- SET NULL: source record survives if the issuer link is later cleared.
    issuer_id             BIGINT REFERENCES issuers(id) ON DELETE SET NULL,

    -- Identifier for the ingestion batch / run that first recorded this source.
    ingestion_run_id      TEXT,

    -- Operator review state for the source record itself.
    review_status         TEXT NOT NULL DEFAULT 'unreviewed'
                              CHECK (review_status IN (
                                  'unreviewed', 'reviewed', 'disputed'
                              )),

    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Natural identity constraint: one row per canonical source URL.
    UNIQUE (source_url)
);


-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_context_sources_issuer_id
    ON context_sources(issuer_id)
    WHERE issuer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_context_sources_source_type
    ON context_sources(source_type);

-- Timeline query: most recent publications first.
CREATE INDEX IF NOT EXISTS idx_context_sources_publication
    ON context_sources(publication_timestamp DESC)
    WHERE publication_timestamp IS NOT NULL;

-- Document integrity / dedup by hash.
CREATE INDEX IF NOT EXISTS idx_context_sources_hash
    ON context_sources(document_hash)
    WHERE document_hash IS NOT NULL;

-- Review queue: sources awaiting or disputed operator review.
CREATE INDEX IF NOT EXISTS idx_context_sources_review
    ON context_sources(review_status)
    WHERE review_status != 'reviewed';


-- ─── context_sources_public VIEW ─────────────────────────────────────────────
--
-- Public-facing projection of context_sources.
--
-- Deliberately omits storage_path and raw_text — these are internal
-- operational columns that must never be served through any API endpoint
-- or export that a client or researcher can access.
--
-- All API endpoints must query this view (or select specific safe columns
-- from the table).  Internal scripts may query the table directly using
-- the service-role key.

CREATE OR REPLACE VIEW context_sources_public AS
    SELECT
        id,
        source_url,
        source_type,
        publisher,
        document_title,
        publication_timestamp,
        discovered_timestamp,
        document_hash,
        -- storage_path intentionally excluded
        -- raw_text intentionally excluded
        issuer_id,
        ingestion_run_id,
        review_status,
        created_at,
        updated_at
    FROM context_sources;


-- ─── entities ────────────────────────────────────────────────────────────────
--
-- Legal entities in the ownership and governance structure.
--
-- Intentionally distinct from:
--   insiders — MAR Art 19 declarants (each linked to a specific company row)
--   issuers  — listed securities issuers
--
-- An entity may optionally be cross-linked to both.  The FK columns
-- (issuer_id, insider_id) are advisory; they are not the entity's identity.
--
-- Natural identity: lei (UNIQUE where NOT NULL).
-- No compound (legal_name, jurisdiction) UNIQUE constraint because
-- Italian company names are not globally unique; entity deduplication
-- requires human review (review_status = 'pending_review').

CREATE TABLE IF NOT EXISTS entities (
    id            BIGSERIAL PRIMARY KEY,

    -- Legal/documentary name exactly as it appears in the primary source.
    legal_name    TEXT NOT NULL,

    -- Abbreviated display name (optional).
    short_name    TEXT,

    -- Category of legal vehicle.
    entity_type   TEXT NOT NULL CHECK (entity_type IN (
        'natural_person',
        'company',
        'holding_company',
        'trust',
        'fiduciary',
        'foundation',
        'fund',
        'nominee',
        'other'
    )),

    -- ISO 3166-1 alpha-2 jurisdiction of incorporation (or citizenship for
    -- natural persons).
    jurisdiction  TEXT NOT NULL DEFAULT 'IT',

    -- Legal Entity Identifier (20-char GLEIF code).  Preferred globally-unique
    -- key.  UNIQUE enforced: one entity record per LEI.
    lei           TEXT UNIQUE,

    -- Optional link to the issuer master when this entity is also a listed issuer.
    -- SET NULL on issuer deletion: entity record survives.
    issuer_id     BIGINT REFERENCES issuers(id) ON DELETE SET NULL,

    -- Optional link to a known MAR declarant (advisory — not the entity's identity).
    -- SET NULL on insider deletion: entity record survives.
    insider_id    BIGINT REFERENCES insiders(id) ON DELETE SET NULL,

    -- Internal analyst notes.  Not published externally.
    notes         TEXT,

    -- Review state for this entity record.
    -- New entities start as 'pending_review' until an operator confirms the
    -- legal name is correct and the entity is not a duplicate.
    review_status TEXT NOT NULL DEFAULT 'pending_review'
                      CHECK (review_status IN (
                          'pending_review', 'confirmed', 'rejected'
                      )),

    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_entities_entity_type
    ON entities(entity_type);

CREATE INDEX IF NOT EXISTS idx_entities_issuer_id
    ON entities(issuer_id)
    WHERE issuer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_entities_insider_id
    ON entities(insider_id)
    WHERE insider_id IS NOT NULL;

-- Review queue.
CREATE INDEX IF NOT EXISTS idx_entities_review
    ON entities(review_status)
    WHERE review_status = 'pending_review';

-- Case-insensitive name lookup — primary entry point for entity resolution.
CREATE INDEX IF NOT EXISTS idx_entities_legal_name_lower
    ON entities(LOWER(legal_name));


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    (SELECT COUNT(*) FROM context_sources)                         AS context_sources_rows,
    (SELECT COUNT(*) FROM entities)                                AS entities_rows,
    (SELECT COUNT(*) FROM information_schema.views
     WHERE table_schema = 'public'
       AND table_name   = 'context_sources_public')                AS public_view_created,
    (SELECT COUNT(*) FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'context_sources'
       AND column_name  IN ('storage_path', 'raw_text'))           AS internal_cols_on_table,
    (SELECT COUNT(*) FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'context_sources_public'
       AND column_name  IN ('storage_path', 'raw_text'))           AS internal_cols_on_view;
-- Expected:
--   public_view_created    = 1  (view exists)
--   internal_cols_on_table = 2  (storage_path + raw_text are on the table)
--   internal_cols_on_view  = 0  (neither appears in the public view)
