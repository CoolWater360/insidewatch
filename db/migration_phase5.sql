-- Phase 5 migration: priority tiers + scraper run tracking
-- Run once in the Supabase SQL editor.

ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS priority_tier INT DEFAULT 2;

-- Scraper run tracking
CREATE TABLE IF NOT EXISTS scraper_runs (
  tier INT PRIMARY KEY,
  last_successful_run TIMESTAMP,
  companies_crawled INT DEFAULT 0,
  transactions_inserted INT DEFAULT 0,
  updated_at TIMESTAMP DEFAULT NOW()
);
INSERT INTO scraper_runs (tier) VALUES (1),(2),(3) ON CONFLICT DO NOTHING;

-- Seed Tier 1 — FTSE MIB constituents present in our database.
-- Update this list when the index rebalances (quarterly).
UPDATE companies SET priority_tier = 1 WHERE name ILIKE ANY(ARRAY[
  'Assicurazioni Generali%',
  'UniCredit%',
  'Eni%',
  'Intesa Sanpaolo%',
  'Enel%',
  'Ferrari%',
  'Moncler%',
  'Davide Campari%',
  'Leonardo%',
  'Nexi%',
  'Prysmian%',
  'Mediobanca%',
  'Pirelli%',
  'Inwit%',
  'Poste Italiane%',
  'Recordati%',
  'Snam%',
  'Terna%',
  'Telecom Italia%',
  'A2A%',
  'Buzzi%',
  'Diasorin%',
  'DiaSorin%',
  'Italgas%',
  'Unipol%',
  'BPER Banca%',
  'Banco BPM%',
  'Banco Bpm%',
  'CNH Industrial%',
  'Iveco Group%',
  'STMicroelectronics%',
  'Amplifon%',
  'Lottomatica%',
  'Saipem%',
  'Tenaris%',
  'Banca Mediolanum%',
  'Stellantis%',
  'Brunello Cucinelli%',
  'Interpump%',
  'Webuild%'
]);

-- Tier 3 — very small / illiquid names (companies with no recent filings in our data)
-- Leave everything else at default Tier 2.
