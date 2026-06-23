-- Migration 010: Latency measurement timestamps for the filing pipeline.
-- Phase 9 of the Italy Alpha Roadmap.
--
-- Adds six timestamp columns to filings that capture when each stage of the
-- ingestion pipeline occurred.  Together they allow precise latency reporting:
--
--   source_published_utc  → estimated time the source first published the filing
--   discovered_utc        → when the listener first saw the URL
--   downloaded_utc        → when the PDF was successfully downloaded
--   parsed_utc            → when the parser returned transaction records
--   validated_utc         → when all transactions passed validation
--   delivered_utc         → when transactions were inserted and alerts sent
--
-- Key latency metrics:
--   end-to-end:    source_published_utc  →  delivered_utc
--   scraper:       discovered_utc        →  delivered_utc
--   download:      discovered_utc        →  downloaded_utc
--   parse:         downloaded_utc        →  parsed_utc
--   insert:        parsed_utc            →  delivered_utc
--
-- Safe to re-run: all statements use IF NOT EXISTS or WHERE guards.
--
-- Apply via Supabase Dashboard → SQL Editor, or:
--   psql $DATABASE_URL -f db/migrations/010_latency_timestamps.sql

ALTER TABLE filings
  ADD COLUMN IF NOT EXISTS source_published_utc  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS discovered_utc         TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS downloaded_utc         TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS parsed_utc             TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS validated_utc          TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS delivered_utc          TIMESTAMPTZ;

-- ─── Backfill from existing columns ──────────────────────────────────────────

-- discovered_utc: use first_seen_at (set at registration time in Phase 2)
UPDATE filings
SET    discovered_utc = first_seen_at
WHERE  discovered_utc IS NULL
  AND  first_seen_at IS NOT NULL;

-- delivered_utc: use completed_at (set when all transactions are inserted)
UPDATE filings
SET    delivered_utc = completed_at
WHERE  delivered_utc IS NULL
  AND  completed_at IS NOT NULL
  AND  status = 'completed';

-- source_published_utc: estimated from filing_date at 09:00 UTC.
-- Borsa Italiana typically publishes between 09:00–12:00 CET (08:00–11:00 UTC).
-- 09:00 UTC is a reasonable conservative estimate for a daily batch measure.
UPDATE filings
SET    source_published_utc = (filing_date::date + INTERVAL '9 hours') AT TIME ZONE 'UTC'
WHERE  source_published_utc IS NULL
  AND  filing_date IS NOT NULL;

-- ─── Index for latency queries ────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_filings_delivered_utc
  ON filings(delivered_utc DESC)
  WHERE delivered_utc IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_filings_discovered_utc
  ON filings(discovered_utc DESC)
  WHERE discovered_utc IS NOT NULL;
