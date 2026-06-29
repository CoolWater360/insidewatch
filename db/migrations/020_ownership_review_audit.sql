-- =============================================================================
-- Migration 020: Ownership Review Audit Hardening
-- Phase 17B.7
--
-- Closes the documented review-audit gap for the ownership pilot before it
-- expands. Three additive, backward-compatible changes:
--
--   1. entities.reviewed_by / reviewed_at — explicit reviewer attribution
--      columns, mirroring ownership_events and entity_relationships
--      (migrations 018 / 017). Previously entities recorded only review_status.
--
--   2. internal_audit_log.entity_type CHECK — extended to accept the three
--      ownership entity kinds so ownership review actions can write audit rows.
--      All existing values remain valid; existing rows are unaffected.
--
--   3. Atomic review RPCs — UPDATE + internal_audit_log INSERT in one PL/pgSQL
--      transaction, mirroring db/migrations/009_internal_rpc.sql. Route handlers
--      call these via db.rpc(); any error rolls back both the business change
--      and the audit row — no partial state.
--
-- NO existing audit-log checks, FKs, RPCs, or review workflows are modified.
-- NO data is backfilled: earlier pilot entity corrections (entities 3 and 5,
-- approved in Phase 17B.5) remain without retroactive reviewer attribution; this
-- is intentional and documented in docs/ownership-review-ui.md.
--
-- Safe to re-run (IF NOT EXISTS / DROP CONSTRAINT IF EXISTS / OR REPLACE).
-- Prerequisites: Migrations 001–019 applied.
--
-- Apply in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Rollback:
--   DROP FUNCTION IF EXISTS internal_review_ownership_entity(BIGINT, TEXT, TEXT);
--   DROP FUNCTION IF EXISTS internal_set_ownership_entity_type(BIGINT, TEXT, TEXT);
--   DROP FUNCTION IF EXISTS internal_review_ownership_event(BIGINT, TEXT, TEXT);
--   DROP FUNCTION IF EXISTS internal_review_ownership_relationship(BIGINT, TEXT, TEXT);
--   ALTER TABLE internal_audit_log DROP CONSTRAINT IF EXISTS internal_audit_log_entity_type_check;
--   ALTER TABLE internal_audit_log ADD CONSTRAINT internal_audit_log_entity_type_check
--       CHECK (entity_type IN ('transaction', 'filing', 'unmatched_issuer'));
--   ALTER TABLE entities DROP COLUMN IF EXISTS reviewed_by;
--   ALTER TABLE entities DROP COLUMN IF EXISTS reviewed_at;
-- =============================================================================


-- ─── 1. entities reviewer attribution columns ─────────────────────────────────

ALTER TABLE entities ADD COLUMN IF NOT EXISTS reviewed_by TEXT;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;


-- ─── 2. extend internal_audit_log.entity_type CHECK ───────────────────────────
-- The original inline CHECK (migration 008) is auto-named
-- internal_audit_log_entity_type_check. Drop and re-add (named) with the three
-- ownership kinds appended. Existing values are preserved.

ALTER TABLE internal_audit_log
    DROP CONSTRAINT IF EXISTS internal_audit_log_entity_type_check;

ALTER TABLE internal_audit_log
    ADD CONSTRAINT internal_audit_log_entity_type_check
    CHECK (entity_type IN (
        'transaction', 'filing', 'unmatched_issuer',
        'ownership_entity', 'ownership_event', 'ownership_relationship'
    ));


-- ─── 3. Atomic ownership review RPCs ───────────────────────────────────────────

-- 3a. Approve / reject an entity (no type change).
CREATE OR REPLACE FUNCTION internal_review_ownership_entity(
  p_entity_id BIGINT,
  p_decision  TEXT,   -- 'approve' | 'reject'
  p_actor     TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_old_status TEXT;
  v_new_status TEXT;
  v_now        TIMESTAMPTZ := NOW();
BEGIN
  IF p_decision NOT IN ('approve', 'reject') THEN
    RAISE EXCEPTION 'invalid decision %, expected approve|reject', p_decision;
  END IF;
  v_new_status := CASE p_decision WHEN 'approve' THEN 'confirmed' ELSE 'rejected' END;

  SELECT review_status INTO v_old_status
  FROM entities WHERE id = p_entity_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'entity % not found', p_entity_id;
  END IF;

  UPDATE entities
  SET    review_status = v_new_status,
         reviewed_by   = p_actor,
         reviewed_at   = v_now,
         updated_at    = v_now
  WHERE  id = p_entity_id;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    p_decision, 'ownership_entity', p_entity_id, p_actor,
    jsonb_build_object('review_status', v_old_status),
    jsonb_build_object('review_status', v_new_status)
  );
END;
$$;


-- 3b. Apply an operator-approved entity_type correction (marks confirmed).
CREATE OR REPLACE FUNCTION internal_set_ownership_entity_type(
  p_entity_id   BIGINT,
  p_entity_type TEXT,
  p_actor       TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_old_type   TEXT;
  v_old_status TEXT;
  v_now        TIMESTAMPTZ := NOW();
BEGIN
  SELECT entity_type, review_status INTO v_old_type, v_old_status
  FROM entities WHERE id = p_entity_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'entity % not found', p_entity_id;
  END IF;

  -- entity_type CHECK on entities enforces the valid vocabulary; an invalid
  -- value raises here and rolls back.
  UPDATE entities
  SET    entity_type   = p_entity_type,
         review_status = 'confirmed',
         reviewed_by   = p_actor,
         reviewed_at   = v_now,
         updated_at    = v_now
  WHERE  id = p_entity_id;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    'reclassify', 'ownership_entity', p_entity_id, p_actor,
    jsonb_build_object('entity_type', v_old_type, 'review_status', v_old_status),
    jsonb_build_object('entity_type', p_entity_type, 'review_status', 'confirmed')
  );
END;
$$;


-- 3c. Approve / reject an ownership event (current version only).
CREATE OR REPLACE FUNCTION internal_review_ownership_event(
  p_event_id BIGINT,
  p_decision TEXT,
  p_actor    TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_old_status TEXT;
  v_new_status TEXT;
  v_now        TIMESTAMPTZ := NOW();
BEGIN
  IF p_decision NOT IN ('approve', 'reject') THEN
    RAISE EXCEPTION 'invalid decision %, expected approve|reject', p_decision;
  END IF;
  v_new_status := CASE p_decision WHEN 'approve' THEN 'confirmed' ELSE 'rejected' END;

  SELECT review_status INTO v_old_status
  FROM ownership_events WHERE id = p_event_id AND is_current = TRUE FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'current ownership_event % not found', p_event_id;
  END IF;

  UPDATE ownership_events
  SET    review_status = v_new_status,
         reviewed_by   = p_actor,
         reviewed_at   = v_now,
         updated_at    = v_now
  WHERE  id = p_event_id AND is_current = TRUE;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    p_decision, 'ownership_event', p_event_id, p_actor,
    jsonb_build_object('review_status', v_old_status),
    jsonb_build_object('review_status', v_new_status)
  );
END;
$$;


-- 3d. Approve / reject an entity relationship (current version only).
CREATE OR REPLACE FUNCTION internal_review_ownership_relationship(
  p_relationship_id BIGINT,
  p_decision        TEXT,
  p_actor           TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_old_status TEXT;
  v_new_status TEXT;
  v_now        TIMESTAMPTZ := NOW();
BEGIN
  IF p_decision NOT IN ('approve', 'reject') THEN
    RAISE EXCEPTION 'invalid decision %, expected approve|reject', p_decision;
  END IF;
  v_new_status := CASE p_decision WHEN 'approve' THEN 'confirmed' ELSE 'rejected' END;

  SELECT review_status INTO v_old_status
  FROM entity_relationships WHERE id = p_relationship_id AND is_current = TRUE FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'current entity_relationship % not found', p_relationship_id;
  END IF;

  UPDATE entity_relationships
  SET    review_status = v_new_status,
         reviewed_by   = p_actor,
         reviewed_at   = v_now,
         updated_at    = v_now
  WHERE  id = p_relationship_id AND is_current = TRUE;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    p_decision, 'ownership_relationship', p_relationship_id, p_actor,
    jsonb_build_object('review_status', v_old_status),
    jsonb_build_object('review_status', v_new_status)
  );
END;
$$;


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    (SELECT COUNT(*) FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'entities'
       AND column_name IN ('reviewed_by', 'reviewed_at'))               AS entity_reviewer_cols,  -- expect 2
    (SELECT COUNT(*) FROM information_schema.routines
     WHERE routine_schema = 'public'
       AND routine_name IN (
         'internal_review_ownership_entity',
         'internal_set_ownership_entity_type',
         'internal_review_ownership_event',
         'internal_review_ownership_relationship'))                     AS ownership_rpcs;        -- expect 4
-- Quick CHECK acceptance probe (should NOT raise):
--   INSERT ... entity_type='ownership_event' ... then ROLLBACK in a transaction.
