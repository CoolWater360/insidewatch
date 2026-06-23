-- Migration 011: API audit log for /api/v1/* endpoints.
-- Phase 10 of the Italy Alpha Roadmap.
--
-- Every authenticated request to /api/v1/* is logged here. The table
-- serves two purposes simultaneously:
--
--   1. Rate limiting — count rows WHERE api_key_hash = $hash AND
--      created_at >= NOW() - INTERVAL '1 minute' before allowing a request.
--
--   2. Audit trail — answers "who called what endpoint, when, and with
--      what result" for institutional clients.
--
-- api_key_hash stores SHA-256(raw_key) so the real token is never at rest.
--
-- Safe to re-run: CREATE TABLE / INDEX use IF NOT EXISTS.
--
-- Apply via Supabase Dashboard → SQL Editor, or:
--   psql $DATABASE_URL -f db/migrations/011_api_audit_log.sql

CREATE TABLE IF NOT EXISTS api_audit_log (
    id            BIGSERIAL PRIMARY KEY,
    api_key_hash  TEXT        NOT NULL,   -- SHA-256 hex of the bearer token
    endpoint      TEXT        NOT NULL,   -- e.g. "/api/v1/transactions"
    method        TEXT        NOT NULL DEFAULT 'GET',
    query_params  TEXT,                   -- raw query string (no auth tokens)
    status_code   INT,
    response_ms   INT,                    -- wall-clock milliseconds
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Rate-limiting: fast lookup by key + recency.
CREATE INDEX IF NOT EXISTS idx_api_audit_log_key_time
  ON api_audit_log (api_key_hash, created_at DESC);

-- Monitoring: all requests by endpoint.
CREATE INDEX IF NOT EXISTS idx_api_audit_log_endpoint_time
  ON api_audit_log (endpoint, created_at DESC);

-- Purge policy (informational comment — apply manually if desired):
-- DELETE FROM api_audit_log WHERE created_at < NOW() - INTERVAL '90 days';
