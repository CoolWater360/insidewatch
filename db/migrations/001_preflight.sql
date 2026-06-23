-- =============================================================================
-- Preflight validation for migration 001_schema_integrity.sql
-- Phase 1 of the Italy Alpha Roadmap
--
-- Run this file BEFORE 001_schema_integrity.sql and review all output.
-- Proceed only if every status column shows 'ok' or 'column not yet present'.
--
-- Run in: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Schema compatibility:
--   · Always-present columns (transaction_type, direction, needs_review) are
--     queried directly.
--   · Columns added by this migration (economic_intent, review_status,
--     role_category, priority_tier, parser_version, extraction_confidence,
--     classification_confidence) are checked via information_schema first.
--     If absent the check is skipped with a clear status message — no errors.
--
-- Output: a single result set from _preflight ordered by check number.
-- =============================================================================


-- ─── Result collector ─────────────────────────────────────────────────────────

DROP TABLE IF EXISTS _preflight;

CREATE TEMP TABLE _preflight (
    n      smallint,
    path   text,
    value  text,
    cnt    bigint,
    status text
);


-- ─── Check 1: transactions.transaction_type (always present) ──────────────────
-- Valid after Section 1 backfill: buy|sell|grant|option_exercise|sell_to_cover|other
-- Finds non-NULL values already in DB that the CHECK constraint would reject.

INSERT INTO _preflight
SELECT 1, 'transactions.transaction_type', transaction_type, COUNT(*), 'FAIL — invalid value'
FROM transactions
WHERE transaction_type IS NOT NULL
  AND transaction_type NOT IN ('buy','sell','grant','option_exercise','sell_to_cover','other')
GROUP BY transaction_type

UNION ALL

SELECT 1, 'transactions.transaction_type', null, 0, 'ok — no invalid values'
WHERE NOT EXISTS (
    SELECT 1 FROM transactions
    WHERE transaction_type IS NOT NULL
      AND transaction_type NOT IN ('buy','sell','grant','option_exercise','sell_to_cover','other')
);


-- ─── Check 2: transactions.direction (always present) ─────────────────────────
-- Valid: buy | sell | unknown

INSERT INTO _preflight
SELECT 2, 'transactions.direction', direction, COUNT(*), 'FAIL — invalid value'
FROM transactions
WHERE direction NOT IN ('buy','sell','unknown')
GROUP BY direction

UNION ALL

SELECT 2, 'transactions.direction', null, 0, 'ok — no invalid values'
WHERE NOT EXISTS (
    SELECT 1 FROM transactions
    WHERE direction NOT IN ('buy','sell','unknown')
);


-- ─── Check 3: transactions.economic_intent (added by this migration) ──────────
-- Valid: discretionary | mechanical | unclear
-- Skipped safely if the column does not yet exist.

DO $$
DECLARE v_exists bool; v_n int;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'transactions'
          AND column_name  = 'economic_intent'
    ) INTO v_exists;

    IF v_exists THEN
        EXECUTE $q$
            INSERT INTO _preflight
            SELECT 3, 'transactions.economic_intent', economic_intent, COUNT(*), 'FAIL — invalid value'
            FROM transactions
            WHERE economic_intent IS NOT NULL
              AND economic_intent NOT IN ('discretionary','mechanical','unclear')
            GROUP BY economic_intent
        $q$;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        IF v_n = 0 THEN
            INSERT INTO _preflight VALUES (3,'transactions.economic_intent',null,0,'ok — no invalid values');
        END IF;
    ELSE
        INSERT INTO _preflight VALUES (3,'transactions.economic_intent',null,null,'column not yet present — skipped');
    END IF;
END $$;


-- ─── Check 4: transactions.review_status (added by this migration) ────────────
-- Valid: pending_review | under_review | confirmed | rejected | corrected | NULL

DO $$
DECLARE v_exists bool; v_n int;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'transactions'
          AND column_name  = 'review_status'
    ) INTO v_exists;

    IF v_exists THEN
        EXECUTE $q$
            INSERT INTO _preflight
            SELECT 4, 'transactions.review_status', review_status, COUNT(*), 'FAIL — invalid value'
            FROM transactions
            WHERE review_status IS NOT NULL
              AND review_status NOT IN (
                  'pending_review','under_review','confirmed','rejected','corrected'
              )
            GROUP BY review_status
        $q$;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        IF v_n = 0 THEN
            INSERT INTO _preflight VALUES (4,'transactions.review_status',null,0,'ok — no invalid values');
        END IF;
    ELSE
        INSERT INTO _preflight VALUES (4,'transactions.review_status',null,null,'column not yet present — skipped');
    END IF;
END $$;


-- ─── Check 5: insiders.role_category (added by this migration) ────────────────
-- Valid: executive | board | major_shareholder | related_person | other

DO $$
DECLARE v_exists bool; v_n int;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'insiders'
          AND column_name  = 'role_category'
    ) INTO v_exists;

    IF v_exists THEN
        EXECUTE $q$
            INSERT INTO _preflight
            SELECT 5, 'insiders.role_category', role_category, COUNT(*), 'FAIL — invalid value'
            FROM insiders
            WHERE role_category IS NOT NULL
              AND role_category NOT IN (
                  'executive','board','major_shareholder','related_person','other'
              )
            GROUP BY role_category
        $q$;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        IF v_n = 0 THEN
            INSERT INTO _preflight VALUES (5,'insiders.role_category',null,0,'ok — no invalid values');
        END IF;
    ELSE
        INSERT INTO _preflight VALUES (5,'insiders.role_category',null,null,'column not yet present — skipped');
    END IF;
END $$;


-- ─── Check 6: companies.priority_tier (added by this migration) ───────────────
-- Valid: integer BETWEEN 1 AND 3

DO $$
DECLARE v_exists bool; v_n int;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'companies'
          AND column_name  = 'priority_tier'
    ) INTO v_exists;

    IF v_exists THEN
        EXECUTE $q$
            INSERT INTO _preflight
            SELECT 6, 'companies.priority_tier', priority_tier::text, COUNT(*), 'FAIL — invalid value'
            FROM companies
            WHERE priority_tier IS NOT NULL
              AND priority_tier NOT BETWEEN 1 AND 3
            GROUP BY priority_tier
        $q$;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        IF v_n = 0 THEN
            INSERT INTO _preflight VALUES (6,'companies.priority_tier',null,0,'ok — no invalid values');
        END IF;
    ELSE
        INSERT INTO _preflight VALUES (6,'companies.priority_tier',null,null,'column not yet present — skipped');
    END IF;
END $$;


-- ─── Check 7: NULL counts — rows each backfill UPDATE will touch ───────────────
-- These are the expected UPDATE row counts from Sections 1 and 5 of the migration.
-- A count of 0 means the backfill step has nothing to do (already clean or
-- already applied); a large count is normal on a fresh DB.

DO $$
DECLARE
    v_tx_type bigint := 0;
    v_nr      bigint := 0;
    v_intent  bigint;
    v_rs      bigint;
    v_pv      bigint;
    v_ec      bigint;
    v_cc      bigint;
    c_intent  bool;
    c_rs      bool;
    c_pv      bool;
    c_ec      bool;
    c_cc      bool;
BEGIN
    -- Always-present columns
    SELECT COUNT(*) INTO v_tx_type FROM transactions WHERE transaction_type IS NULL;
    SELECT COUNT(*) INTO v_nr      FROM transactions WHERE needs_review IS NULL;

    -- Columns added by this migration — check existence first
    SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='transactions' AND column_name='economic_intent')          INTO c_intent;
    SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='transactions' AND column_name='review_status')             INTO c_rs;
    SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='transactions' AND column_name='parser_version')            INTO c_pv;
    SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='transactions' AND column_name='extraction_confidence')     INTO c_ec;
    SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='transactions' AND column_name='classification_confidence') INTO c_cc;

    IF c_intent THEN EXECUTE 'SELECT COUNT(*) FROM transactions WHERE economic_intent IS NULL'          INTO v_intent; END IF;
    IF c_rs     THEN EXECUTE 'SELECT COUNT(*) FROM transactions WHERE review_status IS NULL'            INTO v_rs;     END IF;
    IF c_pv     THEN EXECUTE 'SELECT COUNT(*) FROM transactions WHERE parser_version IS NULL'           INTO v_pv;     END IF;
    IF c_ec     THEN EXECUTE 'SELECT COUNT(*) FROM transactions WHERE extraction_confidence IS NULL'    INTO v_ec;     END IF;
    IF c_cc     THEN EXECUTE 'SELECT COUNT(*) FROM transactions WHERE classification_confidence IS NULL' INTO v_cc;    END IF;

    INSERT INTO _preflight VALUES
        (7,'transactions.transaction_type (NULL→backfill)', null, v_tx_type,
            'rows Section 1a will set to buy/sell/grant/other'),
        (7,'transactions.needs_review (NULL→FALSE)',         null, v_nr,
            'rows Section 1b will set to FALSE'),
        (7,'transactions.economic_intent (NULL→derived)',    null, v_intent,
            CASE WHEN c_intent THEN 'rows Section 5a will derive from transaction_type'
                               ELSE 'column not yet present — will be 0 after migration adds it' END),
        (7,'transactions.review_status (NULL→derived)',      null, v_rs,
            CASE WHEN c_rs THEN 'rows Section 5b will derive from needs_review'
                           ELSE 'column not yet present — will be 0 after migration adds it' END),
        (7,'transactions.parser_version (NULL→0.0.0)',       null, v_pv,
            CASE WHEN c_pv THEN 'rows Section 5c will set to sentinel 0.0.0'
                           ELSE 'column not yet present — will be 0 after migration adds it' END),
        (7,'transactions.extraction_confidence (NULL→coarse)',null, v_ec,
            CASE WHEN c_ec THEN 'rows Section 5d will set coarse score (0.4 or 0.8)'
                           ELSE 'column not yet present — will be 0 after migration adds it' END),
        (7,'transactions.classification_confidence (NULL→coarse)',null, v_cc,
            CASE WHEN c_cc THEN 'rows Section 5e will set coarse score (0.3, 0.6, or 0.9)'
                           ELSE 'column not yet present — will be 0 after migration adds it' END);
END $$;


-- ─── Check 8: Baseline row counts ─────────────────────────────────────────────

INSERT INTO _preflight
SELECT 8, 'total rows — ' || tbl, null, cnt, 'baseline before migration'
FROM (
    SELECT 'transactions' AS tbl, COUNT(*) AS cnt FROM transactions
    UNION ALL
    SELECT 'insiders',  COUNT(*) FROM insiders
    UNION ALL
    SELECT 'companies', COUNT(*) FROM companies
) sub;


-- ─── Report ───────────────────────────────────────────────────────────────────
-- All checks 1–6 should show 'ok' or 'column not yet present — skipped'.
-- Any 'FAIL' row identifies rows that must be cleaned up before running
-- 001_schema_integrity.sql, otherwise CHECK constraint additions will fail.

SELECT
    n     AS "#",
    path  AS column_path,
    value AS invalid_value,
    cnt   AS count,
    status
FROM _preflight
ORDER BY n, path, value NULLS LAST;
