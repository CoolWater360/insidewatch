-- =============================================================================
-- Migration 005: Issuer Master and Security Mapping
-- Phase 6 of the Italy Alpha Roadmap
--
-- Problem with the old companies table:
--   Company rows are created on-the-fly from the PDF Avviso header string.
--   The same issuer appears under multiple name variants ("Eni S.p.A.",
--   "ENI SPA", "Eni"), creating duplicate company rows and fragmenting
--   transaction history across them.  The primary lookup uses ilike with
--   wildcards, which gives inconsistent results as the corpus grows.
--
-- Solution — Issuer Master:
--   issuers         Canonical legal entity.  One row per real-world company.
--   issuer_aliases  All known name variants, ISINs, tickers that map to an
--                   issuer.  Exact case-insensitive lookup replaces ilike.
--   securities      Individual tradeable instruments (ISIN → issuer).
--   unmatched_issuers
--                   Review queue populated when the resolver finds no match.
--                   Operator resolves or rejects each entry via CLI.
--
--   companies.issuer_id links the legacy companies table to the new master.
--   Existing transaction company_id FKs are unchanged in this phase.
--
-- Safe to re-run: all statements are guarded with IF NOT EXISTS or DO/EXCEPTION.
--
-- Prerequisites:
--   Migrations 001, 002, 003, 004 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   ALTER TABLE companies DROP COLUMN IF EXISTS issuer_id;
--   DROP TABLE IF EXISTS unmatched_issuers;
--   DROP TABLE IF EXISTS securities;
--   DROP TABLE IF EXISTS issuer_aliases;
--   DROP TABLE IF EXISTS issuers;
-- =============================================================================


-- ─── issuers ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS issuers (
    id              BIGSERIAL PRIMARY KEY,

    -- Official/canonical company name used for display.
    canonical_name  TEXT NOT NULL UNIQUE,

    -- Abbreviated display name (e.g. "Eni" for "Eni S.p.A.").
    short_name      TEXT,

    -- Legal Entity Identifier (GLEIF global standard, 20 chars).
    lei             TEXT UNIQUE,

    -- ISO 3166-1 alpha-2 country of incorporation.
    country         TEXT NOT NULL DEFAULT 'IT',

    -- Trading venue / market segment.
    -- e.g. 'MTA', 'AIM Italy', 'EXM' (Euronext Milan), 'MIV'
    market          TEXT,

    -- Broad sector classification (free text; not a strict taxonomy yet).
    sector          TEXT,

    -- Lifecycle status of this entity.
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'delisted', 'suspended', 'pending_review')),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_issuers_status  ON issuers(status);
CREATE INDEX IF NOT EXISTS idx_issuers_country ON issuers(country);


-- ─── issuer_aliases ───────────────────────────────────────────────────────────
--
-- Every known string that identifies an issuer.
--
-- alias_type values:
--   'name'         — a recognised name variant (Avviso header, press-room spelling)
--   'isin'         — ISIN code (supplement to securities table for name-only lookup)
--   'lei'          — LEI code (supplement to issuers.lei)
--   'ticker'       — exchange ticker symbol
--   'avviso_header'— the exact "Mittente del comunicato" string from a PDF

CREATE TABLE IF NOT EXISTS issuer_aliases (
    id          BIGSERIAL PRIMARY KEY,
    issuer_id   BIGINT NOT NULL REFERENCES issuers(id) ON DELETE CASCADE,

    alias       TEXT NOT NULL,
    alias_type  TEXT NOT NULL
                    CHECK (alias_type IN ('name', 'isin', 'lei', 'ticker', 'avviso_header')),

    -- Where this alias came from (audit trail, not enforced).
    source      TEXT NOT NULL DEFAULT 'manual',

    -- True for the primary/preferred variant (used in display).
    is_primary  BOOLEAN NOT NULL DEFAULT FALSE,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Each (alias, alias_type) pair belongs to exactly one issuer.
    UNIQUE (alias, alias_type)
);

CREATE INDEX IF NOT EXISTS idx_issuer_aliases_issuer_id ON issuer_aliases(issuer_id);
-- Case-insensitive lookup index.
CREATE INDEX IF NOT EXISTS idx_issuer_aliases_alias_lower
    ON issuer_aliases(LOWER(alias));


-- ─── securities ───────────────────────────────────────────────────────────────
--
-- Individual tradeable instruments.
-- The primary ISIN → issuer mapping used by the resolver.

CREATE TABLE IF NOT EXISTS securities (
    id              BIGSERIAL PRIMARY KEY,
    issuer_id       BIGINT NOT NULL REFERENCES issuers(id) ON DELETE CASCADE,

    isin            TEXT NOT NULL UNIQUE,

    -- High-level instrument class.
    instrument_type TEXT NOT NULL DEFAULT 'equity'
                        CHECK (instrument_type IN (
                            'equity', 'bond', 'warrant', 'option',
                            'convertible', 'etf', 'other'
                        )),

    -- Human-readable description from the PDF (e.g. "Azioni Ordinarie").
    instrument_name TEXT,

    currency        TEXT NOT NULL DEFAULT 'EUR',

    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'delisted', 'suspended')),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_securities_issuer_id ON securities(issuer_id);


-- ─── unmatched_issuers ────────────────────────────────────────────────────────
--
-- Review queue.  Populated automatically when the resolver finds no match for
-- a raw company name / ISIN pair extracted from a filing.
--
-- One row per distinct raw_name; status cycles pending → resolved | rejected.

CREATE TABLE IF NOT EXISTS unmatched_issuers (
    id                  BIGSERIAL PRIMARY KEY,

    -- Raw company name as it appeared in the filing.
    raw_name            TEXT NOT NULL,

    -- ISIN extracted alongside the name (if any).
    raw_isin            TEXT,

    -- Filing that first surfaced this unmatched name (for triage context).
    filing_id           BIGINT REFERENCES filings(id) ON DELETE SET NULL,

    -- ilike suggestion produced by the resolver (may be NULL if no close match).
    suggestion_issuer_id BIGINT REFERENCES issuers(id) ON DELETE SET NULL,

    discovered_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- pending → waiting for operator action
    -- resolved → mapped to an issuer via --resolve or --create-and-resolve
    -- rejected → confirmed as not an issuer (test data, parse error, etc.)
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'resolved', 'rejected')),

    resolved_to         BIGINT REFERENCES issuers(id) ON DELETE SET NULL,
    resolved_at         TIMESTAMPTZ,
    resolved_by         TEXT,

    -- One review queue entry per distinct raw company name.
    UNIQUE (raw_name)
);

CREATE INDEX IF NOT EXISTS idx_unmatched_issuers_status
    ON unmatched_issuers(status)
    WHERE status = 'pending';


-- ─── Link legacy companies table to issuer master ─────────────────────────────
--
-- companies.issuer_id is NULL until the backfill script or scraper links the row.
-- The company_id FK on transactions is unchanged in this phase.

ALTER TABLE companies ADD COLUMN IF NOT EXISTS issuer_id BIGINT REFERENCES issuers(id);

CREATE INDEX IF NOT EXISTS idx_companies_issuer_id
    ON companies(issuer_id)
    WHERE issuer_id IS NOT NULL;


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    (SELECT COUNT(*) FROM issuers)           AS issuers,
    (SELECT COUNT(*) FROM issuer_aliases)    AS aliases,
    (SELECT COUNT(*) FROM securities)        AS securities,
    (SELECT COUNT(*) FROM unmatched_issuers) AS unmatched,
    (SELECT COUNT(*) FROM companies WHERE issuer_id IS NOT NULL) AS companies_linked;
