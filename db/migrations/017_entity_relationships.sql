-- =============================================================================
-- Migration 017: Entity Relationships
-- Phase 17A of the Italy Alpha Roadmap
--
-- entity_relationships records a source-backed, versioned relationship
-- between two entities or between an entity and an issuer: control,
-- beneficial ownership, shareholding, related-party, concert-party,
-- nominee, trustee, beneficiary, and family ties where publicly disclosed
-- and legally relevant under MAR / TUF.
--
-- Natural event identity
-- ─────────────────────
-- "One current active relationship between a given subject and object of
-- a given type."  Enforced with two partial UNIQUE indexes, separated
-- because the object can be either an entity or an issuer, not both:
--
--   uidx_entity_rel_entity_current
--     UNIQUE (subject_entity_id, object_entity_id, relationship_type)
--     WHERE is_current = TRUE AND object_entity_id IS NOT NULL
--
--   uidx_entity_rel_issuer_current
--     UNIQUE (subject_entity_id, object_issuer_id, relationship_type)
--     WHERE is_current = TRUE AND object_issuer_id IS NOT NULL
--
-- When the object is not yet resolved (both FK columns NULL), the DB
-- cannot enforce uniqueness; the application layer uses the review queue
-- to prevent duplicates in that case.
--
-- ON DELETE choices (all NEW FKs — no existing FKs modified)
-- ──────────────────────────────────────────────────────────
--   subject_entity_id → entities : RESTRICT
--     Cannot delete an entity that is the subject of a relationship; the
--     relationship must first be deleted or re-assigned explicitly.
--   object_entity_id  → entities : RESTRICT
--     Same reasoning for the object side; SET NULL would break the
--     "at least one object" CHECK if object_issuer_id is also NULL.
--   object_issuer_id  → issuers  : RESTRICT
--     Prevents silently losing the issuer reference.
--   source_id         → context_sources : RESTRICT
--     Provenance must not be silently lost — every relationship must
--     remain traceable to its primary source.
--   superseded_by (self-ref) → entity_relationships : SET NULL
--     If the replacing row is deleted, the old row retains is_current=FALSE
--     and loses its superseded_by pointer.  This is a detectable data-
--     quality inconsistency (is_current=FALSE with superseded_by=NULL)
--     and survivable; RESTRICT here would block any deletion of a row
--     that has ever been a "new" version.
--
-- Safe to re-run.  Uses CREATE TABLE IF NOT EXISTS + IF NOT EXISTS indexes.
-- Prerequisites: Migrations 001–016 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   DROP TABLE IF EXISTS entity_relationships;
-- =============================================================================


-- ─── entity_relationships ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS entity_relationships (
    id                 BIGSERIAL PRIMARY KEY,

    -- ── Subject ───────────────────────────────────────────────────────────────
    -- The entity that holds, controls, or is the acting party in the
    -- relationship.  RESTRICT: cannot delete an entity while it is the
    -- subject of at least one relationship row.
    subject_entity_id  BIGINT NOT NULL
                           REFERENCES entities(id) ON DELETE RESTRICT,

    -- ── Object (entity OR issuer — exactly one must be non-NULL) ──────────────

    -- Object is an entity (e.g. holding vehicle, person, trust).
    -- RESTRICT: cannot delete an entity while it is the object of a
    -- relationship; SET NULL would break the CHECK below if object_issuer_id
    -- is also NULL.
    object_entity_id   BIGINT REFERENCES entities(id) ON DELETE RESTRICT,

    -- Object is a listed issuer (e.g. entity is a shareholder of co.).
    -- RESTRICT: prevents silently losing the issuer context.
    object_issuer_id   BIGINT REFERENCES issuers(id) ON DELETE RESTRICT,

    -- Exactly one of object_entity_id / object_issuer_id must be non-NULL.
    CONSTRAINT chk_entity_rel_has_object
        CHECK (object_entity_id IS NOT NULL OR object_issuer_id IS NOT NULL),

    -- ── Relationship type ─────────────────────────────────────────────────────
    relationship_type  TEXT NOT NULL CHECK (relationship_type IN (
        'controls',           -- subject directly or indirectly controls object
        'beneficially_owns',  -- subject is the beneficial owner of object
        'shareholder',        -- subject holds a stake in object (typically an issuer)
        'related_party',      -- subject is a related party of the issuer
        'concert_party',      -- subject and object act in concert (explicitly disclosed)
        'nominee_for',        -- subject acts as nominee for a third party
        'trustee_of',         -- subject is trustee of object (trust / foundation)
        'beneficiary_of',     -- subject is a beneficiary of object
        'family_relation',    -- legally relevant family tie (spouse, child, etc.)
        'other'
    )),

    -- ── Evidence quality ──────────────────────────────────────────────────────
    -- see docs/context-data-model.md §7.2 for definitions
    confidence         TEXT NOT NULL DEFAULT 'parsed_fact'
                           CHECK (confidence IN (
                               'parsed_fact',
                               'heuristic_suggestion',
                               'reviewer_confirmed'
                           )),

    -- ── Quantitative attributes (optional) ───────────────────────────────────
    ownership_pct      NUMERIC CHECK (ownership_pct BETWEEN 0 AND 100),
    voting_pct         NUMERIC CHECK (voting_pct    BETWEEN 0 AND 100),
    direct_or_indirect TEXT CHECK (direct_or_indirect IN (
        'direct', 'indirect', 'both', 'unknown'
    )),

    -- ── Temporal scope ────────────────────────────────────────────────────────
    -- effective_to NULL = relationship is believed to still be active.
    effective_from     DATE,
    effective_to       DATE,

    -- ── Source provenance ─────────────────────────────────────────────────────
    -- RESTRICT: source record must not be silently deleted while any
    -- relationship references it.
    source_id          BIGINT NOT NULL
                           REFERENCES context_sources(id) ON DELETE RESTRICT,

    -- Verbatim key text from the source document substantiating this
    -- relationship (e.g. a quoted passage from the annual report).
    evidence_text      TEXT,

    -- ── Versioning (mirrors pattern in transactions / transaction_versions) ────
    is_current         BOOLEAN NOT NULL DEFAULT TRUE,
    version_number     INT     NOT NULL DEFAULT 1,

    -- SET NULL: see ON DELETE rationale in file header.
    superseded_by      BIGINT  REFERENCES entity_relationships(id) ON DELETE SET NULL,
    superseded_at      TIMESTAMPTZ,

    -- ── Review workflow ───────────────────────────────────────────────────────
    review_status      TEXT NOT NULL DEFAULT 'pending_review'
                           CHECK (review_status IN (
                               'pending_review', 'confirmed', 'rejected'
                           )),
    reviewed_by        TEXT,
    reviewed_at        TIMESTAMPTZ,

    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ── Lookup and timeline indexes ───────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_entity_rel_subject
    ON entity_relationships(subject_entity_id);

CREATE INDEX IF NOT EXISTS idx_entity_rel_obj_entity
    ON entity_relationships(object_entity_id)
    WHERE object_entity_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_entity_rel_obj_issuer
    ON entity_relationships(object_issuer_id)
    WHERE object_issuer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_entity_rel_type
    ON entity_relationships(relationship_type);

CREATE INDEX IF NOT EXISTS idx_entity_rel_source
    ON entity_relationships(source_id);

-- Superseded rows: locate all non-current versions for a subject entity.
CREATE INDEX IF NOT EXISTS idx_entity_rel_not_current
    ON entity_relationships(subject_entity_id)
    WHERE is_current = FALSE;

-- Review queue.
CREATE INDEX IF NOT EXISTS idx_entity_rel_review
    ON entity_relationships(review_status)
    WHERE review_status = 'pending_review';


-- ── Natural event identity: partial unique indexes ────────────────────────────
--
-- "One current active relationship between a given subject and object pair
-- of a given type."
--
-- Unresolved objects (both FK columns still NULL after initial parse — rare)
-- cannot be constrained at the DB level; the application prevents duplicates
-- in that edge case.

-- Subject → object entity current
CREATE UNIQUE INDEX IF NOT EXISTS uidx_entity_rel_entity_current
    ON entity_relationships(subject_entity_id, object_entity_id, relationship_type)
    WHERE is_current = TRUE AND object_entity_id IS NOT NULL;

-- Subject → object issuer current
CREATE UNIQUE INDEX IF NOT EXISTS uidx_entity_rel_issuer_current
    ON entity_relationships(subject_entity_id, object_issuer_id, relationship_type)
    WHERE is_current = TRUE AND object_issuer_id IS NOT NULL;


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    (SELECT COUNT(*) FROM entity_relationships)                      AS entity_relationships_rows,
    (SELECT COUNT(*) FROM information_schema.table_constraints
     WHERE table_schema    = 'public'
       AND table_name      = 'entity_relationships'
       AND constraint_name = 'chk_entity_rel_has_object')            AS check_has_object_exists,
    (SELECT COUNT(*) FROM pg_indexes
     WHERE schemaname = 'public'
       AND tablename  = 'entity_relationships'
       AND indexname  IN (
           'uidx_entity_rel_entity_current',
           'uidx_entity_rel_issuer_current'
       ))                                                             AS natural_identity_indexes;
-- Expected:
--   check_has_object_exists  = 1
--   natural_identity_indexes = 2
