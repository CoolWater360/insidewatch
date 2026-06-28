-- =============================================================================
-- Migration 018: Context Event Tables
-- Phase 17A of the Italy Alpha Roadmap
--
-- Creates four event tables that form the analytical context layer around
-- insider-transaction data:
--
--   ownership_events    — beneficial ownership threshold crossings,
--                         CONSOB-style §120 disclosures, and major-holding
--                         declarations
--   governance_events   — board appointment/resignation/re-election events,
--                         role changes, and committee memberships
--   buyback_events      — share buyback programme launches, authorisations,
--                         weekly execution reports, and closures
--   corporate_events    — capital actions, M&A announcements, dividend
--                         declarations, and other issuer-level events
--
-- All four tables share the same architectural patterns:
--   • issuer_id NOT NULL → RESTRICT: ownership/governance/buyback/corporate
--     history cannot be orphaned by an issuer deletion
--   • source_id NOT NULL → RESTRICT: provenance must not be silently lost
--   • entity_id / insider_id FKs → SET NULL: raw fallback name columns
--     preserve evidence even if the entity/insider row is deleted
--   • Versioning columns (is_current, version_number, superseded_by,
--     superseded_at) matching the pattern in transactions/transaction_versions
--   • Partial UNIQUE indexes for natural event identity where all key
--     columns are known (WHERE entity_id IS NOT NULL, etc.)
--
-- Natural event identity per table
-- ─────────────────────────────────
--   ownership_events  :
--     (issuer_id, entity_id, event_date, event_type) WHERE is_current=TRUE
--     AND entity_id IS NOT NULL
--     → one current record per resolved owner + issuer + date + type
--
--   governance_events :
--     (issuer_id, entity_id, event_type, effective_date) WHERE is_current=TRUE
--     AND entity_id IS NOT NULL AND effective_date IS NOT NULL
--     → one current record per resolved person + role event + date
--
--   buyback_events (two sub-identities):
--     Execution: (issuer_id, programme_id, execution_date) WHERE
--       event_type='execution' AND is_current=TRUE AND programme_id IS NOT NULL
--       AND execution_date IS NOT NULL
--     Lifecycle: (issuer_id, programme_id, event_type) WHERE
--       event_type <> 'execution' AND is_current=TRUE AND programme_id IS NOT NULL
--
--   corporate_events  :
--     (issuer_id, event_type, effective_date) WHERE is_current=TRUE
--     AND effective_date IS NOT NULL
--     Announcement-only events (effective_date NULL) cannot be DB-constrained;
--     documented — application layer prevents duplicates in that case.
--
-- ON DELETE summary (cross-cutting)
-- ──────────────────────────────────
--   → issuers         : RESTRICT on all four tables
--   → context_sources : RESTRICT on all four tables
--   → entities        : SET NULL  (raw_*_name fallback preserves evidence)
--   → insiders        : SET NULL  (governance_events only)
--   → self-reference (superseded_by) : SET NULL (see 017 header for rationale)
--
-- Safe to re-run.  Uses CREATE TABLE IF NOT EXISTS + IF NOT EXISTS indexes.
-- Prerequisites: Migrations 001–017 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   DROP TABLE IF EXISTS corporate_events;
--   DROP TABLE IF EXISTS buyback_events;
--   DROP TABLE IF EXISTS governance_events;
--   DROP TABLE IF EXISTS ownership_events;
-- =============================================================================


-- ─── ownership_events ─────────────────────────────────────────────────────────
--
-- Records threshold crossings, major-holding declarations, and other
-- beneficial ownership events for a given issuer.
--
-- Natural identity: (issuer_id, entity_id, event_date, event_type)
-- WHERE is_current=TRUE AND entity_id IS NOT NULL

CREATE TABLE IF NOT EXISTS ownership_events (
    id                        BIGSERIAL PRIMARY KEY,

    -- Issuer the ownership event relates to.
    -- RESTRICT: history must not be silently orphaned.
    issuer_id                 BIGINT NOT NULL REFERENCES issuers(id) ON DELETE RESTRICT,

    -- Resolved beneficial owner entity (NULL when still unresolved).
    -- SET NULL: raw_entity_name provides the fallback documentary evidence.
    entity_id                 BIGINT REFERENCES entities(id) ON DELETE SET NULL,

    -- Raw name as it appears in the source — preserved regardless of entity resolution.
    raw_entity_name           TEXT,

    -- Optional holding-vehicle entity (intermediate SPV, trust, etc.).
    holding_vehicle_entity_id BIGINT REFERENCES entities(id) ON DELETE SET NULL,
    raw_vehicle_name          TEXT,

    -- Event classification.
    event_type                TEXT NOT NULL CHECK (event_type IN (
        'threshold_crossing_up',    -- holding crossed upward (e.g. 5 % → 10 %)
        'threshold_crossing_down',  -- holding fell below threshold
        'initial_disclosure',       -- first disclosure when above threshold
        'cancellation',             -- previous disclosure cancelled/corrected
        'change_in_nature',         -- change in nature of holding (direct ↔ indirect)
        'pledge',                   -- shares pledged as collateral
        'pledge_release',           -- pledge released
        'other'
    )),

    -- Date the economic event occurred (not the filing date).
    event_date                DATE NOT NULL,

    -- Stake before and after this event (%).
    stake_pct_before          NUMERIC CHECK (stake_pct_before BETWEEN 0 AND 100),
    stake_pct_after           NUMERIC CHECK (stake_pct_after  BETWEEN 0 AND 100),

    -- Voting rights % (NULL if same as economic stake or not separately disclosed).
    voting_pct_before         NUMERIC CHECK (voting_pct_before BETWEEN 0 AND 100),
    voting_pct_after          NUMERIC CHECK (voting_pct_after  BETWEEN 0 AND 100),

    direct_or_indirect        TEXT CHECK (direct_or_indirect IN (
        'direct', 'indirect', 'both', 'unknown'
    )),

    -- Source provenance.
    -- RESTRICT: provenance must not be silently lost.
    source_id                 BIGINT NOT NULL
                                  REFERENCES context_sources(id) ON DELETE RESTRICT,

    -- Verbatim passage from the source document.
    evidence_text             TEXT,

    -- Evidence quality.
    confidence                TEXT NOT NULL DEFAULT 'parsed_fact'
                                  CHECK (confidence IN (
                                      'parsed_fact',
                                      'heuristic_suggestion',
                                      'reviewer_confirmed'
                                  )),

    -- Versioning.
    is_current                BOOLEAN NOT NULL DEFAULT TRUE,
    version_number            INT     NOT NULL DEFAULT 1,
    superseded_by             BIGINT  REFERENCES ownership_events(id) ON DELETE SET NULL,
    superseded_at             TIMESTAMPTZ,

    -- Review workflow.
    review_status             TEXT NOT NULL DEFAULT 'pending_review'
                                  CHECK (review_status IN (
                                      'pending_review', 'confirmed', 'rejected'
                                  )),
    reviewed_by               TEXT,
    reviewed_at               TIMESTAMPTZ,

    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── ownership_events indexes ──────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_ownership_issuer_id
    ON ownership_events(issuer_id);

CREATE INDEX IF NOT EXISTS idx_ownership_entity_id
    ON ownership_events(entity_id)
    WHERE entity_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ownership_event_date
    ON ownership_events(event_date DESC);

CREATE INDEX IF NOT EXISTS idx_ownership_event_type
    ON ownership_events(event_type);

CREATE INDEX IF NOT EXISTS idx_ownership_source
    ON ownership_events(source_id);

CREATE INDEX IF NOT EXISTS idx_ownership_not_current
    ON ownership_events(issuer_id)
    WHERE is_current = FALSE;

CREATE INDEX IF NOT EXISTS idx_ownership_review
    ON ownership_events(review_status)
    WHERE review_status = 'pending_review';

-- Natural event identity: resolved owner + issuer + date + type, current only.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_ownership_resolved_current
    ON ownership_events(issuer_id, entity_id, event_date, event_type)
    WHERE is_current = TRUE AND entity_id IS NOT NULL;


-- ─── governance_events ────────────────────────────────────────────────────────
--
-- Board appointments, resignations, re-elections, and committee memberships.
--
-- Natural identity: (issuer_id, entity_id, event_type, effective_date)
-- WHERE is_current=TRUE AND entity_id IS NOT NULL AND effective_date IS NOT NULL

CREATE TABLE IF NOT EXISTS governance_events (
    id                    BIGSERIAL PRIMARY KEY,

    -- Issuer the governance event relates to.
    -- RESTRICT: history must not be silently orphaned.
    issuer_id             BIGINT NOT NULL REFERENCES issuers(id) ON DELETE RESTRICT,

    -- Resolved person entity (NULL when still unresolved).
    -- SET NULL: raw_entity_name fallback preserved.
    entity_id             BIGINT REFERENCES entities(id) ON DELETE SET NULL,

    -- Raw name as it appears in the source.
    raw_entity_name       TEXT,

    -- Optional link to a known MAR declarant.
    -- SET NULL: governance record survives if the insider row is deleted.
    insider_id            BIGINT REFERENCES insiders(id) ON DELETE SET NULL,

    -- Event classification.
    event_type            TEXT NOT NULL CHECK (event_type IN (
        'appointment',         -- new appointment to a board / committee
        'resignation',         -- voluntary resignation
        'removal',             -- involuntary removal
        'reelection',          -- reconfirmed at AGM
        'term_expiry',         -- mandate expired at AGM
        'role_change',         -- change in role / responsibility
        'committee_join',      -- joined a board committee
        'committee_leave',     -- left a board committee
        'other'
    )),

    -- Board role (optional, controlled vocabulary — extend by migration).
    role_type             TEXT CHECK (role_type IN (
        'executive_director',
        'non_executive_director',
        'independent_director',
        'chairman',
        'ceo',
        'cfo',
        'coo',
        'statutory_auditor',
        'audit_committee',
        'remuneration_committee',
        'other'
    )),

    -- The date the appointment/resignation takes legal effect.
    -- NULL only for announcement-only records where the effective date is
    -- not yet known (e.g. a board press release pending shareholder vote).
    effective_date        DATE,

    -- Announcement date (always present when known).
    announcement_date     DATE,

    -- For succession events: the entity stepping down.
    predecessor_entity_id BIGINT REFERENCES entities(id) ON DELETE SET NULL,
    raw_predecessor_name  TEXT,

    -- For succession events: the entity stepping in.
    successor_entity_id   BIGINT REFERENCES entities(id) ON DELETE SET NULL,
    raw_successor_name    TEXT,

    -- Source provenance.
    -- RESTRICT: provenance must not be silently lost.
    source_id             BIGINT NOT NULL
                              REFERENCES context_sources(id) ON DELETE RESTRICT,

    evidence_text         TEXT,

    confidence            TEXT NOT NULL DEFAULT 'parsed_fact'
                              CHECK (confidence IN (
                                  'parsed_fact',
                                  'heuristic_suggestion',
                                  'reviewer_confirmed'
                              )),

    -- Versioning.
    is_current            BOOLEAN NOT NULL DEFAULT TRUE,
    version_number        INT     NOT NULL DEFAULT 1,
    superseded_by         BIGINT  REFERENCES governance_events(id) ON DELETE SET NULL,
    superseded_at         TIMESTAMPTZ,

    -- Review workflow.
    review_status         TEXT NOT NULL DEFAULT 'pending_review'
                              CHECK (review_status IN (
                                  'pending_review', 'confirmed', 'rejected'
                              )),
    reviewed_by           TEXT,
    reviewed_at           TIMESTAMPTZ,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── governance_events indexes ─────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_governance_issuer_id
    ON governance_events(issuer_id);

CREATE INDEX IF NOT EXISTS idx_governance_entity_id
    ON governance_events(entity_id)
    WHERE entity_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_governance_insider_id
    ON governance_events(insider_id)
    WHERE insider_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_governance_event_type
    ON governance_events(event_type);

CREATE INDEX IF NOT EXISTS idx_governance_effective_date
    ON governance_events(effective_date DESC)
    WHERE effective_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_governance_source
    ON governance_events(source_id);

CREATE INDEX IF NOT EXISTS idx_governance_not_current
    ON governance_events(issuer_id)
    WHERE is_current = FALSE;

CREATE INDEX IF NOT EXISTS idx_governance_review
    ON governance_events(review_status)
    WHERE review_status = 'pending_review';

-- Natural event identity: resolved person + issuer + event type + effective date, current.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_governance_resolved_current
    ON governance_events(issuer_id, entity_id, event_type, effective_date)
    WHERE is_current = TRUE
      AND entity_id IS NOT NULL
      AND effective_date IS NOT NULL;


-- ─── buyback_events ───────────────────────────────────────────────────────────
--
-- Share buyback programme lifecycle and weekly execution records.
--
-- Natural identity (two sub-types):
--   Execution records:
--     (issuer_id, programme_id, execution_date) WHERE event_type='execution'
--     AND is_current=TRUE AND programme_id IS NOT NULL AND execution_date IS NOT NULL
--   Lifecycle records:
--     (issuer_id, programme_id, event_type) WHERE event_type<>'execution'
--     AND is_current=TRUE AND programme_id IS NOT NULL

CREATE TABLE IF NOT EXISTS buyback_events (
    id                        BIGSERIAL PRIMARY KEY,

    -- Issuer conducting the buyback.
    -- RESTRICT: history must not be silently orphaned.
    issuer_id                 BIGINT NOT NULL REFERENCES issuers(id) ON DELETE RESTRICT,

    -- Opaque identifier grouping all events for a single buyback programme
    -- (e.g. "2024-BGM-001").  NULL for one-off disclosures not tied to a
    -- named programme (uncommon).
    programme_id              TEXT,

    -- Event classification.
    event_type                TEXT NOT NULL CHECK (event_type IN (
        'authorisation',    -- shareholder / board authorisation of a new programme
        'launch',           -- management announces programme is now active
        'execution',        -- weekly/daily execution report (Borsa Italiana avviso)
        'suspension',       -- programme temporarily suspended
        'resumption',       -- programme resumed after suspension
        'amendment',        -- material change to programme terms
        'closure',          -- programme complete / shares cancelled / treasury-stock cap
        'other'
    )),

    -- ── For execution records (event_type = 'execution') ─────────────────────
    -- Date of the execution period (last day of the weekly report window).
    execution_date            DATE,
    shares_bought             BIGINT CHECK (shares_bought >= 0),
    avg_price                 NUMERIC CHECK (avg_price >= 0),
    currency                  TEXT,
    total_consideration       NUMERIC CHECK (total_consideration >= 0),
    cumulative_shares_bought  BIGINT CHECK (cumulative_shares_bought >= 0),

    -- ── For lifecycle records ─────────────────────────────────────────────────
    -- Announced max shares / budget for the programme.
    max_shares_authorised     BIGINT CHECK (max_shares_authorised >= 0),
    max_budget_authorised     NUMERIC CHECK (max_budget_authorised >= 0),
    authorisation_date        DATE,
    expiry_date               DATE,

    -- Source provenance.
    -- RESTRICT: provenance must not be silently lost.
    source_id                 BIGINT NOT NULL
                                  REFERENCES context_sources(id) ON DELETE RESTRICT,

    evidence_text             TEXT,

    confidence                TEXT NOT NULL DEFAULT 'parsed_fact'
                                  CHECK (confidence IN (
                                      'parsed_fact',
                                      'heuristic_suggestion',
                                      'reviewer_confirmed'
                                  )),

    -- Versioning.
    is_current                BOOLEAN NOT NULL DEFAULT TRUE,
    version_number            INT     NOT NULL DEFAULT 1,
    superseded_by             BIGINT  REFERENCES buyback_events(id) ON DELETE SET NULL,
    superseded_at             TIMESTAMPTZ,

    -- Review workflow.
    review_status             TEXT NOT NULL DEFAULT 'pending_review'
                                  CHECK (review_status IN (
                                      'pending_review', 'confirmed', 'rejected'
                                  )),
    reviewed_by               TEXT,
    reviewed_at               TIMESTAMPTZ,

    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── buyback_events indexes ────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_buyback_issuer_id
    ON buyback_events(issuer_id);

CREATE INDEX IF NOT EXISTS idx_buyback_programme_id
    ON buyback_events(programme_id)
    WHERE programme_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_buyback_event_type
    ON buyback_events(event_type);

CREATE INDEX IF NOT EXISTS idx_buyback_execution_date
    ON buyback_events(execution_date DESC)
    WHERE execution_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_buyback_source
    ON buyback_events(source_id);

CREATE INDEX IF NOT EXISTS idx_buyback_not_current
    ON buyback_events(issuer_id)
    WHERE is_current = FALSE;

CREATE INDEX IF NOT EXISTS idx_buyback_review
    ON buyback_events(review_status)
    WHERE review_status = 'pending_review';

-- Natural identity — execution sub-type: one current report per programme + date.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_buyback_execution_current
    ON buyback_events(issuer_id, programme_id, execution_date)
    WHERE event_type = 'execution'
      AND is_current = TRUE
      AND programme_id IS NOT NULL
      AND execution_date IS NOT NULL;

-- Natural identity — lifecycle sub-type: one current record per event type per programme.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_buyback_lifecycle_current
    ON buyback_events(issuer_id, programme_id, event_type)
    WHERE event_type <> 'execution'
      AND is_current = TRUE
      AND programme_id IS NOT NULL;


-- ─── corporate_events ─────────────────────────────────────────────────────────
--
-- Capital actions, M&A, dividends, and other issuer-level corporate events.
--
-- Natural identity: (issuer_id, event_type, effective_date)
-- WHERE is_current=TRUE AND effective_date IS NOT NULL
--
-- Events where only an announcement_date is known (effective_date NULL)
-- cannot be DB-constrained; the application layer prevents duplicates
-- in that case.

CREATE TABLE IF NOT EXISTS corporate_events (
    id                    BIGSERIAL PRIMARY KEY,

    -- Issuer the event relates to.
    -- RESTRICT: history must not be silently orphaned.
    issuer_id             BIGINT NOT NULL REFERENCES issuers(id) ON DELETE RESTRICT,

    -- Event classification.
    event_type            TEXT NOT NULL CHECK (event_type IN (
        'rights_issue',          -- rights / capital increase
        'share_split',           -- stock split
        'reverse_split',         -- reverse stock split (consolidation)
        'spin_off',              -- new entity spun off
        'merger_acquisition',    -- issuer acquires or is acquired
        'delisting',             -- shares delisted from the exchange
        'listing',               -- new listing / IPO
        'dividend_ordinary',     -- regular cash dividend
        'dividend_special',      -- special / extraordinary dividend
        'dividend_scrip',        -- scrip / stock dividend
        'share_cancellation',    -- treasury share cancellation
        'name_change',           -- company name change
        'registered_office',     -- change of registered address
        'other'
    )),

    -- When the event takes legal or economic effect.
    -- NULL only when a formal announcement has been made but the exact
    -- effective date is pending regulatory/shareholder approval.
    effective_date        DATE,

    -- When the event was publicly announced.
    announcement_date     DATE,

    -- Free-text description (e.g. "1:10 split, ex-date 2025-03-15").
    description           TEXT,

    -- Ratio applicable to splits, rights issues, etc. (e.g. 0.1 for 1:10).
    ratio                 NUMERIC CHECK (ratio > 0),

    -- For M&A: the counterparty company name.
    counterparty_name     TEXT,

    -- For dividends: per-share amount and currency.
    dividend_per_share    NUMERIC CHECK (dividend_per_share >= 0),
    currency              TEXT,

    -- Source provenance.
    -- RESTRICT: provenance must not be silently lost.
    source_id             BIGINT NOT NULL
                              REFERENCES context_sources(id) ON DELETE RESTRICT,

    evidence_text         TEXT,

    confidence            TEXT NOT NULL DEFAULT 'parsed_fact'
                              CHECK (confidence IN (
                                  'parsed_fact',
                                  'heuristic_suggestion',
                                  'reviewer_confirmed'
                              )),

    -- Versioning.
    is_current            BOOLEAN NOT NULL DEFAULT TRUE,
    version_number        INT     NOT NULL DEFAULT 1,
    superseded_by         BIGINT  REFERENCES corporate_events(id) ON DELETE SET NULL,
    superseded_at         TIMESTAMPTZ,

    -- Review workflow.
    review_status         TEXT NOT NULL DEFAULT 'pending_review'
                              CHECK (review_status IN (
                                  'pending_review', 'confirmed', 'rejected'
                              )),
    reviewed_by           TEXT,
    reviewed_at           TIMESTAMPTZ,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── corporate_events indexes ──────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_corporate_issuer_id
    ON corporate_events(issuer_id);

CREATE INDEX IF NOT EXISTS idx_corporate_event_type
    ON corporate_events(event_type);

CREATE INDEX IF NOT EXISTS idx_corporate_effective_date
    ON corporate_events(effective_date DESC)
    WHERE effective_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_corporate_announcement_date
    ON corporate_events(announcement_date DESC)
    WHERE announcement_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_corporate_source
    ON corporate_events(source_id);

CREATE INDEX IF NOT EXISTS idx_corporate_not_current
    ON corporate_events(issuer_id)
    WHERE is_current = FALSE;

CREATE INDEX IF NOT EXISTS idx_corporate_review
    ON corporate_events(review_status)
    WHERE review_status = 'pending_review';

-- Natural event identity: one current record per issuer + event type + effective date.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_corporate_event_current
    ON corporate_events(issuer_id, event_type, effective_date)
    WHERE is_current = TRUE
      AND effective_date IS NOT NULL;


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    (SELECT COUNT(*) FROM ownership_events)                         AS ownership_events_rows,
    (SELECT COUNT(*) FROM governance_events)                        AS governance_events_rows,
    (SELECT COUNT(*) FROM buyback_events)                           AS buyback_events_rows,
    (SELECT COUNT(*) FROM corporate_events)                         AS corporate_events_rows,
    (SELECT COUNT(*) FROM pg_indexes
     WHERE schemaname = 'public'
       AND tablename  IN (
           'ownership_events', 'governance_events',
           'buyback_events',   'corporate_events'
       )
       AND indexname  LIKE 'uidx_%')                                AS natural_identity_indexes;
-- Expected:
--   natural_identity_indexes = 5
--     (1 on ownership, 1 on governance, 2 on buyback, 1 on corporate)
