-- =============================================================================
-- Migration 013: Row-Level Security policies (definitions only)
-- ─────────────────────────────────────────────────────────────────────────────
-- This file DEFINES the policies but does NOT enable RLS on any table.
-- Enabling RLS is a separate manual step (see bottom of this file).
--
-- Rationale for the split:
--   · Enabling RLS without policies = all existing queries (internal console,
--     scraper service role) break immediately.
--   · Defining policies first lets you review and test them before cutting over.
--   · Service-role key bypasses RLS unconditionally — scraper and server-side
--     Next.js code are unaffected by RLS once enabled.
--
-- Policy model:
--   anon / authenticated roles   → read-only SELECT on public-safe tables
--   service_role                 → bypasses RLS (Postgres built-in, no policy needed)
--
-- Tables exposed to anon:
--   transactions  — SELECT only, no internal-only columns (enforced at API layer)
--   companies     — SELECT only
--   insiders      — SELECT only
--
-- Tables NOT exposed to anon (no SELECT policy):
--   filings, unmatched_issuers, internal_audit_log, api_audit_log,
--   issuers, issuer_aliases
-- =============================================================================


-- ─── transactions ─────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS "anon_read_transactions" ON transactions;
CREATE POLICY "anon_read_transactions"
  ON transactions
  FOR SELECT
  TO anon, authenticated
  USING (true);

-- No INSERT / UPDATE / DELETE policies for anon — default-deny covers it once
-- RLS is enabled.


-- ─── companies ────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS "anon_read_companies" ON companies;
CREATE POLICY "anon_read_companies"
  ON companies
  FOR SELECT
  TO anon, authenticated
  USING (true);


-- ─── insiders ─────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS "anon_read_insiders" ON insiders;
CREATE POLICY "anon_read_insiders"
  ON insiders
  FOR SELECT
  TO anon, authenticated
  USING (true);


-- =============================================================================
-- MANUAL ENABLE STEP — run this AFTER verifying the policies above work
-- correctly in your staging environment.  Do NOT include this in an automated
-- migration; run it manually from the Supabase SQL editor or psql.
--
--   ALTER TABLE transactions  ENABLE ROW LEVEL SECURITY;
--   ALTER TABLE companies     ENABLE ROW LEVEL SECURITY;
--   ALTER TABLE insiders      ENABLE ROW LEVEL SECURITY;
--
-- Once enabled, verify with:
--   SELECT tablename, rowsecurity
--   FROM   pg_tables
--   WHERE  schemaname = 'public'
--     AND  tablename IN ('transactions', 'companies', 'insiders');
-- =============================================================================
