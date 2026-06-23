-- =============================================================================
-- Migration 013: Public views + per-view RLS grants (definitions only)
-- ─────────────────────────────────────────────────────────────────────────────
-- This file DEFINES public views and grants SELECT on those views to the
-- anon/authenticated roles.  It does NOT enable RLS on any base table.
-- Enabling RLS is a documented manual step at the bottom of this file.
--
-- Design rationale
-- ────────────────
-- Granting SELECT on raw base tables (transactions, companies, insiders) to
-- anon would expose internal columns: raw_hash, identity_hash, raw_document_
-- sha256, raw_nature_text, review_notes, classification_rationale,
-- classification_override/by/at, source_filing_id, source_transaction_index,
-- needs_review, parser_version, is_current, version_number, superseded_by/at,
-- ir_internal_dealing_url, priority_tier, issuer_id, insider_verified, etc.
--
-- Instead, narrow views project only the fields the public dashboard consumes.
-- anon/authenticated receive SELECT on the views, NOT on the base tables.
-- The base tables remain inaccessible to anon even after RLS is enabled,
-- because default-deny applies to any table without an explicit USING(true)
-- policy for the requesting role.
--
-- Tables permanently inaccessible to anon/authenticated:
--   filings, unmatched_issuers, internal_audit_log, api_audit_log,
--   issuers, issuer_aliases, scraper_runs, companies (base), insiders (base),
--   transactions (base)
--
-- Applying this file:
--   psql $DATABASE_URL -f db/migrations/013_rls_policies.sql
-- or Supabase Dashboard → SQL Editor.
-- Safe to re-run (CREATE OR REPLACE + DROP POLICY IF EXISTS).
-- =============================================================================


-- ─── public_transactions ─────────────────────────────────────────────────────
--
-- Exposes only the fields the public dashboard and /api/v1/transactions use.
-- Filters to is_current = TRUE so superseded rows are invisible to the public.
--
-- Excluded columns (all internal):
--   raw_hash, raw_document_sha256, identity_hash  — dedup / forensic keys
--   source_filing_id, source_transaction_index     — pipeline internals
--   raw_nature_text                                — verbatim PDF extract
--   needs_review, review_reason, review_notes      — operator queue fields
--   classification_rationale                       — internal rule annotation
--   classification_override, _overridden_by/at     — operator override metadata
--   parser_version                                 — internal scraper versioning
--   is_current, version_number, superseded_by/at   — versioning internals (filtered, not exposed)
--   created_at, updated_at                         — internal timestamps

CREATE OR REPLACE VIEW public_transactions AS
SELECT
    id,
    transaction_date,
    filed_date,
    company_id,
    insider_id,
    direction,
    transaction_type,
    economic_intent,
    instrument_type,
    isin,
    quantity,
    unit_price,
    total_value,
    currency,
    review_status,
    source_url,
    extraction_confidence,
    classification_confidence
FROM transactions
WHERE is_current = TRUE;


-- ─── public_companies ────────────────────────────────────────────────────────
--
-- Excluded columns:
--   ir_internal_dealing_url  — internal scraper URL used only by the crawler
--   priority_tier            — internal crawl priority
--   issuer_id                — internal FK to issuers master table
--   created_at, updated_at   — internal timestamps

CREATE OR REPLACE VIEW public_companies AS
SELECT
    id,
    name,
    ticker,
    isin,
    sector
FROM companies;


-- ─── public_insiders ─────────────────────────────────────────────────────────
--
-- Excluded columns:
--   company_id       — insider-company link is via transactions, not exposed here
--   insider_verified — internal quality flag
--   created_at       — internal timestamp

CREATE OR REPLACE VIEW public_insiders AS
SELECT
    id,
    full_name,
    role,
    role_category
FROM insiders;


-- ─── Grant SELECT on views only ──────────────────────────────────────────────
--
-- anon and authenticated receive SELECT on the views, not on base tables.
-- After RLS is enabled, the base tables are default-deny for these roles.

GRANT SELECT ON public_transactions TO anon, authenticated;
GRANT SELECT ON public_companies    TO anon, authenticated;
GRANT SELECT ON public_insiders     TO anon, authenticated;

-- Explicitly revoke any pre-existing direct grants on the base tables
-- (safe no-op if they were never granted).
REVOKE ALL ON transactions FROM anon, authenticated;
REVOKE ALL ON companies    FROM anon, authenticated;
REVOKE ALL ON insiders     FROM anon, authenticated;


-- ─── RLS policies (on base tables — enforced once RLS is enabled) ─────────────
--
-- These policies are defined now but have no effect until RLS is enabled.
-- Because anon/authenticated have no grants on the base tables (only on views),
-- the policies are a defence-in-depth measure: even if a grant were accidentally
-- added to a base table, the policy would still restrict which rows are visible.
--
-- No INSERT / UPDATE / DELETE policies: default-deny for non-service roles.

DROP POLICY IF EXISTS "anon_read_transactions" ON transactions;
CREATE POLICY "anon_read_transactions"
    ON transactions FOR SELECT TO anon, authenticated
    USING (is_current = TRUE);

DROP POLICY IF EXISTS "anon_read_companies" ON companies;
CREATE POLICY "anon_read_companies"
    ON companies FOR SELECT TO anon, authenticated
    USING (true);

DROP POLICY IF EXISTS "anon_read_insiders" ON insiders;
CREATE POLICY "anon_read_insiders"
    ON insiders FOR SELECT TO anon, authenticated
    USING (true);


-- =============================================================================
-- MANUAL ENABLE STEP
-- ─────────────────────────────────────────────────────────────────────────────
-- Run these commands MANUALLY from the Supabase SQL editor or psql AFTER:
--   1. Verifying the views above return the expected columns and row count.
--   2. Confirming that service-role queries (scraper, Next.js server) still
--      work (service_role bypasses RLS unconditionally).
--   3. Confirming that anon queries against base tables return 0 rows
--      (policy default-deny) while anon queries against the views succeed.
--
-- DO NOT include these commands in an automated migration.
--
--   ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
--   ALTER TABLE companies    ENABLE ROW LEVEL SECURITY;
--   ALTER TABLE insiders     ENABLE ROW LEVEL SECURITY;
--
-- Verification after enabling:
--   SELECT tablename, rowsecurity
--   FROM   pg_tables
--   WHERE  schemaname = 'public'
--     AND  tablename  IN ('transactions', 'companies', 'insiders');
--   -- Expected: rowsecurity = true for all three rows.
--
--   -- Test anon access (run as anon role):
--   SET ROLE anon;
--   SELECT COUNT(*) FROM public_transactions;   -- should return rows
--   SELECT COUNT(*) FROM transactions;          -- should return 0 (policy blocks)
--   RESET ROLE;
-- =============================================================================
