-- =============================================================================
-- Migration 008: Internal Audit Log
-- Phase 8 security-and-audit patch
--
-- Creates internal_audit_log — an append-only record of every manual action
-- taken via the /internal operations console.
--
-- Design:
--   · Rows are never updated or deleted (immutable audit trail).
--   · before_values / after_values store JSONB snapshots of relevant fields.
--   · actor records INTERNAL_ACTOR_LABEL (shared-admin model — not individual
--     user attribution; see lib/internal-audit.ts for the full caveat).
--   · No FK to transactions/filings/unmatched_issuers — audit rows must survive
--     even if the referenced entity is later deleted.
--
-- Safe to re-run.
-- Prerequisites: Migrations 001–007 applied.
--
-- Rollback:
--   DROP TABLE IF EXISTS internal_audit_log;
-- =============================================================================


CREATE TABLE IF NOT EXISTS internal_audit_log (
    id            BIGSERIAL PRIMARY KEY,

    -- What happened.
    -- Known values: confirm | reject | reclassify | retry_filing |
    --               resolve_issuer | reject_issuer | add_review_note
    action_type   TEXT NOT NULL,

    -- Which table the entity lives in.
    entity_type   TEXT NOT NULL
        CHECK (entity_type IN ('transaction', 'filing', 'unmatched_issuer')),

    -- Primary key of the affected row (no FK — rows must outlive the entity).
    entity_id     BIGINT NOT NULL,

    -- Who performed the action.  Populated from INTERNAL_ACTOR_LABEL env var.
    -- "shared-admin" signals the temporary shared-credential model.
    actor         TEXT NOT NULL,

    -- Relevant field values before and after the action.
    before_values JSONB,
    after_values  JSONB,

    -- Wall-clock time of the action (server UTC).  Immutable once written.
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- No UPDATE or DELETE policies — audit rows are immutable.
-- RLS: only the service-role key may insert; anon key has no access.

-- Efficient lookup: all actions on a given entity (for audit trail display).
CREATE INDEX IF NOT EXISTS idx_audit_log_entity
    ON internal_audit_log(entity_type, entity_id, created_at DESC);

-- Efficient lookup: recent actions by actor or action type.
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
    ON internal_audit_log(created_at DESC);


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    COUNT(*)                                                              AS total_rows,
    COUNT(*) FILTER (WHERE action_type = 'confirm')                       AS confirms,
    COUNT(*) FILTER (WHERE action_type = 'reject')                        AS rejects,
    COUNT(*) FILTER (WHERE action_type = 'reclassify')                    AS reclassifies,
    COUNT(*) FILTER (WHERE action_type = 'retry_filing')                  AS retries,
    COUNT(*) FILTER (WHERE action_type IN ('resolve_issuer','reject_issuer')) AS issuer_actions,
    COUNT(*) FILTER (WHERE action_type = 'add_review_note')               AS notes
FROM internal_audit_log;
