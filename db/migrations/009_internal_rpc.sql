-- Migration 009: Postgres RPC functions for atomic internal-console operations.
--
-- Each function performs a business UPDATE and an internal_audit_log INSERT in
-- a single implicit PL/pgSQL transaction.  If either statement fails the whole
-- function rolls back — the business state and the audit record are always
-- consistent.  Route handlers call these via db.rpc('function_name', params)
-- and surface any error as HTTP 500; no partial state is ever committed.
--
-- Apply via Supabase Dashboard → SQL Editor, or:
--   psql $DATABASE_URL -f db/migrations/009_internal_rpc.sql


-- ─── 1. confirm transaction ────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION internal_confirm_transaction(
  p_transaction_id BIGINT,
  p_actor          TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_review_status TEXT;
BEGIN
  SELECT review_status
  INTO   v_review_status
  FROM   transactions
  WHERE  id = p_transaction_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'transaction % not found', p_transaction_id;
  END IF;

  UPDATE transactions
  SET    review_status = 'confirmed',
         updated_at    = NOW()
  WHERE  id = p_transaction_id;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    'confirm', 'transaction', p_transaction_id, p_actor,
    jsonb_build_object('review_status', v_review_status),
    jsonb_build_object('review_status', 'confirmed')
  );
END;
$$;


-- ─── 2. reject transaction ─────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION internal_reject_transaction(
  p_transaction_id BIGINT,
  p_actor          TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_review_status TEXT;
BEGIN
  SELECT review_status
  INTO   v_review_status
  FROM   transactions
  WHERE  id = p_transaction_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'transaction % not found', p_transaction_id;
  END IF;

  UPDATE transactions
  SET    review_status = 'rejected',
         updated_at    = NOW()
  WHERE  id = p_transaction_id;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    'reject', 'transaction', p_transaction_id, p_actor,
    jsonb_build_object('review_status', v_review_status),
    jsonb_build_object('review_status', 'rejected')
  );
END;
$$;


-- ─── 3. reclassify transaction ────────────────────────────────────────────────
--
-- Also snapshots the full row to transaction_versions before modifying it.
-- The INTENT_MAP mirrors the TypeScript constant in review/route.ts.

CREATE OR REPLACE FUNCTION internal_reclassify_transaction(
  p_transaction_id   BIGINT,
  p_transaction_type TEXT,
  p_rationale        TEXT,
  p_actor            TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_row          transactions%ROWTYPE;
  v_version_num  INT;
  v_new_intent   TEXT;
  v_now          TIMESTAMPTZ := NOW();
BEGIN
  SELECT *
  INTO   v_row
  FROM   transactions
  WHERE  id = p_transaction_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'transaction % not found', p_transaction_id;
  END IF;

  v_version_num := COALESCE(v_row.version_number, 1);

  v_new_intent := CASE p_transaction_type
    WHEN 'buy'                    THEN 'discretionary'
    WHEN 'sell'                   THEN 'discretionary'
    WHEN 'subscription'           THEN 'discretionary'
    WHEN 'grant'                  THEN 'mechanical'
    WHEN 'option_exercise'        THEN 'mechanical'
    WHEN 'sell_to_cover'          THEN 'mechanical'
    WHEN 'conversion'             THEN 'mechanical'
    WHEN 'inheritance'            THEN 'mechanical'
    WHEN 'gift_in'                THEN 'mechanical'
    WHEN 'gift_out'               THEN 'mechanical'
    WHEN 'transfer_in'            THEN 'mechanical'
    WHEN 'transfer_out'           THEN 'mechanical'
    WHEN 'pledge_or_security'     THEN 'mechanical'
    WHEN 'derivative_transaction' THEN 'mechanical'
    ELSE 'unclear'
  END;

  -- Full-row snapshot before modification
  INSERT INTO transaction_versions (transaction_id, version_number, snapshot, changed_by, change_reason)
  VALUES (
    p_transaction_id,
    v_version_num,
    to_jsonb(v_row),
    p_actor,
    'classification_override: ' || p_rationale
  );

  UPDATE transactions
  SET    transaction_type              = p_transaction_type,
         economic_intent               = v_new_intent,
         classification_rationale      = 'operator_correction: ' || p_rationale,
         classification_override       = TRUE,
         classification_overridden_by  = p_actor,
         classification_overridden_at  = v_now,
         version_number                = v_version_num + 1,
         review_status                 = 'corrected',
         updated_at                    = v_now
  WHERE  id = p_transaction_id;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    'reclassify', 'transaction', p_transaction_id, p_actor,
    jsonb_build_object(
      'transaction_type',         v_row.transaction_type,
      'economic_intent',          v_row.economic_intent,
      'classification_rationale', v_row.classification_rationale
    ),
    jsonb_build_object(
      'transaction_type',         p_transaction_type,
      'economic_intent',          v_new_intent,
      'classification_rationale', 'operator_correction: ' || p_rationale
    )
  );
END;
$$;


-- ─── 4. add review note ───────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION internal_add_review_note(
  p_transaction_id BIGINT,
  p_note           TEXT,
  p_actor          TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_old_note TEXT;
BEGIN
  SELECT review_notes
  INTO   v_old_note
  FROM   transactions
  WHERE  id = p_transaction_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'transaction % not found', p_transaction_id;
  END IF;

  UPDATE transactions
  SET    review_notes = p_note,
         updated_at   = NOW()
  WHERE  id = p_transaction_id;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    'add_review_note', 'transaction', p_transaction_id, p_actor,
    jsonb_build_object('review_notes', v_old_note),
    jsonb_build_object('review_notes', p_note)
  );
END;
$$;


-- ─── 5. retry filing ──────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION internal_retry_filing(
  p_filing_id BIGINT,
  p_actor     TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_status        TEXT;
  v_attempt_count INT;
BEGIN
  SELECT status, attempt_count
  INTO   v_status, v_attempt_count
  FROM   filings
  WHERE  id = p_filing_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'filing % not found', p_filing_id;
  END IF;

  UPDATE filings
  SET    status             = 'pending',
         claim_token        = NULL,
         next_attempt_after = NULL,
         last_error         = NULL
  WHERE  id = p_filing_id;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    'retry_filing', 'filing', p_filing_id, p_actor,
    jsonb_build_object('status', v_status, 'attempt_count', v_attempt_count),
    jsonb_build_object('status', 'pending')
  );
END;
$$;


-- ─── 6. resolve unmatched issuer ──────────────────────────────────────────────

CREATE OR REPLACE FUNCTION internal_resolve_issuer(
  p_unmatched_id BIGINT,
  p_issuer_id    BIGINT,
  p_actor        TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_raw_name TEXT;
  v_isin     TEXT;
  v_status   TEXT;
BEGIN
  SELECT raw_name, isin, status
  INTO   v_raw_name, v_isin, v_status
  FROM   unmatched_issuers
  WHERE  id = p_unmatched_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'unmatched_issuer % not found', p_unmatched_id;
  END IF;

  UPDATE unmatched_issuers
  SET    status             = 'resolved',
         resolved_issuer_id = p_issuer_id
  WHERE  id = p_unmatched_id;

  -- Backfill any companies sharing this raw name (case-insensitive)
  UPDATE companies
  SET    issuer_id = p_issuer_id
  WHERE  LOWER(name) = LOWER(v_raw_name)
    AND  issuer_id IS NULL;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    'resolve_issuer', 'unmatched_issuer', p_unmatched_id, p_actor,
    jsonb_build_object('status', v_status, 'raw_name', v_raw_name, 'isin', v_isin),
    jsonb_build_object('status', 'resolved', 'issuer_id', p_issuer_id)
  );
END;
$$;


-- ─── 7. reject unmatched issuer ───────────────────────────────────────────────

CREATE OR REPLACE FUNCTION internal_reject_issuer(
  p_unmatched_id BIGINT,
  p_actor        TEXT
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_raw_name TEXT;
  v_status   TEXT;
BEGIN
  SELECT raw_name, status
  INTO   v_raw_name, v_status
  FROM   unmatched_issuers
  WHERE  id = p_unmatched_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'unmatched_issuer % not found', p_unmatched_id;
  END IF;

  UPDATE unmatched_issuers
  SET    status = 'rejected'
  WHERE  id = p_unmatched_id;

  INSERT INTO internal_audit_log (action_type, entity_type, entity_id, actor, before_values, after_values)
  VALUES (
    'reject_issuer', 'unmatched_issuer', p_unmatched_id, p_actor,
    jsonb_build_object('status', v_status, 'raw_name', v_raw_name),
    jsonb_build_object('status', 'rejected')
  );
END;
$$;
