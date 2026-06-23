-- =============================================================================
-- InsideWatch — Canonical Schema
-- Italy Alpha, Phase 1
--
-- This file is the single source of truth for the database structure.
-- Apply it once on a fresh Supabase project, then apply numbered migrations
-- in db/migrations/ for subsequent changes.
--
-- All migration SQL has been consolidated here so a fresh install is complete.
-- Existing live databases should apply db/migrations/001_schema_integrity.sql
-- instead of re-running this file.
--
-- Apply order for fresh install:
--   1. This file (schema.sql)
--   2. db/migrations/001_schema_integrity.sql  (backfill + constraints)
--   3. Seed: python3 -m scraper.run_phase2 --limit 0  (loads seed_companies.csv)
-- =============================================================================


-- ─── companies ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS companies (
    id                      BIGSERIAL PRIMARY KEY,
    name                    TEXT NOT NULL UNIQUE,
    ticker                  TEXT UNIQUE,
    isin                    TEXT UNIQUE,
    sector                  TEXT,
    ir_internal_dealing_url TEXT,
    -- 1 = FTSE MIB (~40 co.), 2 = Mid Cap (~60 co.), 3 = rest
    priority_tier           INT  NOT NULL DEFAULT 2
                                CHECK (priority_tier BETWEEN 1 AND 3),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_companies_priority_tier ON companies(priority_tier);
CREATE INDEX IF NOT EXISTS idx_companies_isin          ON companies(isin) WHERE isin IS NOT NULL;


-- ─── insiders ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS insiders (
    id                BIGSERIAL PRIMARY KEY,
    full_name         TEXT    NOT NULL,
    role              TEXT,
    company_id        BIGINT  NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    -- FALSE when the name contains a legal-entity marker (not a natural person)
    insider_verified  BOOLEAN NOT NULL DEFAULT TRUE,
    -- executive | board | major_shareholder | related_person | other
    role_category     TEXT    NOT NULL DEFAULT 'other'
                          CHECK (role_category IN (
                              'executive', 'board', 'major_shareholder',
                              'related_person', 'other'
                          )),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(full_name, company_id)
);

CREATE INDEX IF NOT EXISTS idx_insiders_company    ON insiders(company_id);
CREATE INDEX IF NOT EXISTS idx_insiders_verified   ON insiders(insider_verified);


-- ─── transactions ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS transactions (
    id              BIGSERIAL PRIMARY KEY,
    insider_id      BIGINT  NOT NULL REFERENCES insiders(id)  ON DELETE CASCADE,
    company_id      BIGINT  NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- Core event fields
    transaction_date  DATE    NOT NULL,
    filed_date        DATE,
    direction         TEXT    NOT NULL DEFAULT 'unknown'
                          CHECK (direction IN ('buy', 'sell', 'unknown')),

    -- Transaction classification
    -- Current values: buy | sell | grant | option_exercise | sell_to_cover | other
    -- Phase 7 will extend to the full taxonomy and migrate existing values.
    transaction_type  TEXT    NOT NULL DEFAULT 'buy'
                          CHECK (transaction_type IN (
                              'buy', 'sell', 'grant', 'option_exercise',
                              'sell_to_cover', 'other'
                          )),

    -- Discretionary intent (Phase 7 rules engine will set this precisely)
    economic_intent   TEXT    NOT NULL DEFAULT 'unclear'
                          CHECK (economic_intent IN (
                              'discretionary', 'mechanical', 'unclear'
                          )),

    -- Instrument
    instrument_type TEXT,
    isin            TEXT,

    -- Financial values — stored as NUMERIC for precision.
    -- Note: Python scraper currently computes these as float; Phase 4 will
    -- migrate computation to Decimal.
    quantity    NUMERIC NOT NULL,
    unit_price  NUMERIC NOT NULL,
    total_value NUMERIC NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'EUR',

    -- Source provenance
    source_url              TEXT,
    -- SHA-256 of PDF bytes at ingestion — enables integrity checks and re-parse
    raw_document_sha256     TEXT,
    -- Position within the source PDF (0-indexed); NULL = unknown (legacy records)
    source_transaction_index INT,
    -- FK to filings table — added in Phase 2 migration (002_filings_ledger.sql)
    source_filing_id        BIGINT,

    -- Deduplication hash.
    -- Current: SHA-256(insider|company|date|qty|price) — see DATA_MODEL.md §7
    -- for known collision risks. Redesigned in Phase 4.
    raw_hash TEXT NOT NULL UNIQUE,

    -- Data quality
    needs_review            BOOLEAN NOT NULL DEFAULT FALSE,
    -- Fraction of expected fields successfully extracted (0.0–1.0).
    -- NULL = not yet calculated (legacy records set a coarse estimate in migration 001).
    extraction_confidence   REAL    CHECK (extraction_confidence IS NULL
                                       OR (extraction_confidence >= 0.0
                                       AND extraction_confidence <= 1.0)),
    -- Confidence in direction + transaction_type classification (0.0–1.0).
    classification_confidence REAL  CHECK (classification_confidence IS NULL
                                       OR (classification_confidence >= 0.0
                                       AND classification_confidence <= 1.0)),

    -- Internal review workflow
    -- NULL = not yet assigned (new records get a value immediately from the scraper)
    review_status TEXT       CHECK (review_status IS NULL OR review_status IN (
                                 'pending_review', 'under_review',
                                 'confirmed', 'rejected', 'corrected'
                             )),
    -- Structured tag(s) explaining why review is required.
    -- e.g. 'ambiguous_direction', 'truncated_name', 'missing_issuer'
    review_reason TEXT,

    -- Parser provenance
    -- '0.0.0' = legacy record (pre-Phase 1); '1.x.x' = Phase 1+ parser
    parser_version TEXT,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Primary query patterns
CREATE INDEX IF NOT EXISTS idx_transactions_company_date
    ON transactions(company_id, transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_direction
    ON transactions(direction);
CREATE INDEX IF NOT EXISTS idx_transactions_insider
    ON transactions(insider_id);
CREATE INDEX IF NOT EXISTS idx_transactions_raw_hash
    ON transactions(raw_hash);
CREATE INDEX IF NOT EXISTS idx_transactions_filed_date
    ON transactions(filed_date DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_review_status
    ON transactions(review_status);
CREATE INDEX IF NOT EXISTS idx_transactions_economic_intent
    ON transactions(economic_intent);
CREATE INDEX IF NOT EXISTS idx_transactions_needs_review
    ON transactions(needs_review) WHERE needs_review = TRUE;
CREATE INDEX IF NOT EXISTS idx_transactions_source_filing
    ON transactions(source_filing_id) WHERE source_filing_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_transactions_transaction_type
    ON transactions(transaction_type);


-- ─── scraper_runs ─────────────────────────────────────────────────────────────
-- Coarse per-tier run tracking. Replaced by the filings ledger in Phase 2
-- but retained for backwards compatibility with existing monitoring queries.

CREATE TABLE IF NOT EXISTS scraper_runs (
    tier                  INT PRIMARY KEY,  -- 1 | 2 | 3
    last_successful_run   TIMESTAMPTZ,
    companies_crawled     INT  NOT NULL DEFAULT 0,
    transactions_inserted INT  NOT NULL DEFAULT 0,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO scraper_runs (tier) VALUES (1),(2),(3) ON CONFLICT DO NOTHING;


-- ─── RLS placeholder (Phase 1 manual step) ────────────────────────────────────
-- Row Level Security must be enabled manually in the Supabase dashboard:
--   Dashboard → Authentication → Policies → Enable RLS on each table.
--
-- After enabling RLS, apply these policies:
--
-- Public read-only (anon key can SELECT, nothing else):
--   CREATE POLICY "public_read_companies"   ON companies   FOR SELECT USING (true);
--   CREATE POLICY "public_read_insiders"    ON insiders    FOR SELECT USING (true);
--   CREATE POLICY "public_read_transactions" ON transactions FOR SELECT USING (true);
--
-- Backend write (service_role key bypasses RLS by default — no additional policy needed).
--
-- IMPORTANT: Do NOT enable RLS until SUPABASE_SERVICE_ROLE_KEY is set as a
-- GitHub Secret and confirmed working in scraper/db.py. Enabling RLS with
-- only the anon key in the scraper will silently break all ingestion.
