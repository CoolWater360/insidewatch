-- =============================================================================
-- Migration 012: Patch — drop orphaned constraint + fix resolve_issuer RPC
-- Deployment blocker patch — apply immediately after migrations 001–011.
--
-- Two problems fixed here:
--
-- 1. ORPHANED CONSTRAINT (blocker for new transaction types):
--    Migration 001 added a named constraint `chk_transaction_type` limiting
--    transaction_type to 6 values.  Migration 006 correctly expanded the limit
--    to 16 values but dropped only `transactions_transaction_type_check` (the
--    Postgres auto-named inline constraint from schema.sql).  It never dropped
--    `chk_transaction_type`, leaving both constraints active.  Any INSERT or
--    UPDATE with a new type (subscription, inheritance, gift_in, etc.) is
--    silently rejected by `chk_transaction_type`.
--
-- 2. WRONG COLUMN NAMES IN internal_resolve_issuer (blocker for issuer resolution):
--    The original function selects `isin` (column is `raw_isin`) and sets
--    `resolved_issuer_id` (column is `resolved_to`).  Every call raised a
--    Postgres error; operator issuer resolution was completely broken.
--
-- Safe to re-run.
-- Apply via Supabase Dashboard → SQL Editor, or:
--   psql $DATABASE_URL -f db/migrations/012_patch_constraint_and_rpc.sql
-- =============================================================================


-- ─── 1. Drop the 6-value orphaned constraint ──────────────────────────────────

DO $$ BEGIN
    ALTER TABLE transactions DROP CONSTRAINT chk_transaction_type;
EXCEPTION WHEN undefined_object THEN
    RAISE NOTICE 'chk_transaction_type does not exist — already clean.';
END $$;

-- Verify the 16-value constraint from migration 006 is in place.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema = 'public'
          AND table_name   = 'transactions'
          AND constraint_name = 'transactions_transaction_type_check'
    ) THEN
        -- Migration 006 was not yet applied — add the 16-value constraint now.
        ALTER TABLE transactions ADD CONSTRAINT transactions_transaction_type_check
            CHECK (transaction_type IN (
                'buy', 'sell', 'grant', 'option_exercise', 'sell_to_cover',
                'subscription', 'conversion', 'inheritance',
                'gift_in', 'gift_out', 'transfer_in', 'transfer_out',
                'pledge_or_security', 'derivative_transaction',
                'other', 'unknown'
            ));
        RAISE NOTICE 'transactions_transaction_type_check added (migration 006 guard).';
    END IF;
END $$;


-- ─── 2. Fix internal_resolve_issuer — correct column names ────────────────────
--
-- Old:  SELECT raw_name, isin, status   → isin does not exist; column is raw_isin
--       SET resolved_issuer_id           → column is resolved_to
-- New:  SELECT raw_name, raw_isin, status
--       SET resolved_to

CREATE OR REPLACE FUNCTION internal_resolve_issuer(
  p_unmatched_id BIGINT,
  p_issuer_id    BIGINT,
  p_actor        TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_raw_name TEXT;
  v_raw_isin TEXT;
  v_status   TEXT;
BEGIN
  SELECT raw_name, raw_isin, status
  INTO   v_raw_name, v_raw_isin, v_status
  FROM   unmatched_issuers
  WHERE  id = p_unmatched_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'unmatched_issuer % not found', p_unmatched_id;
  END IF;

  UPDATE unmatched_issuers
  SET    status      = 'resolved',
         resolved_to = p_issuer_id
  WHERE  id = p_unmatched_id;

  -- Backfill any companies sharing this raw name (case-insensitive)
  UPDATE companies
  SET    issuer_id = p_issuer_id
  WHERE  LOWER(name) = LOWER(v_raw_name)
    AND  issuer_id IS NULL;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    'resolve_issuer', 'unmatched_issuer', p_unmatched_id, p_actor,
    jsonb_build_object('status', v_status, 'raw_name', v_raw_name, 'raw_isin', v_raw_isin),
    jsonb_build_object('status', 'resolved', 'issuer_id', p_issuer_id)
  );
END;
$$;


-- ─── Verification ─────────────────────────────────────────────────────────────

SELECT
    constraint_name,
    check_clause
FROM information_schema.check_constraints
WHERE constraint_schema = 'public'
  AND constraint_name IN (
      'chk_transaction_type',
      'transactions_transaction_type_check'
  );
-- Expected: only transactions_transaction_type_check, listing 16 values.
-- chk_transaction_type must NOT appear.
