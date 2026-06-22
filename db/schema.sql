-- Phase 2: Internal dealing tracker database schema

CREATE TABLE IF NOT EXISTS companies (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  ticker TEXT UNIQUE,
  isin TEXT UNIQUE,
  sector TEXT,
  ir_internal_dealing_url TEXT,
  priority_tier INT DEFAULT 2,          -- 1=FTSE MIB, 2=Mid Cap, 3=rest
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Scraper run tracking (one row per tier, updated after each successful run)
CREATE TABLE IF NOT EXISTS scraper_runs (
  tier INT PRIMARY KEY,
  last_successful_run TIMESTAMP,
  companies_crawled INT DEFAULT 0,
  transactions_inserted INT DEFAULT 0,
  updated_at TIMESTAMP DEFAULT NOW()
);
INSERT INTO scraper_runs (tier) VALUES (1),(2),(3) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS insiders (
  id BIGSERIAL PRIMARY KEY,
  full_name TEXT NOT NULL,
  role TEXT,
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  insider_verified BOOLEAN DEFAULT TRUE,
  role_category TEXT DEFAULT 'other',
  created_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(full_name, company_id)
);

CREATE TABLE IF NOT EXISTS transactions (
  id BIGSERIAL PRIMARY KEY,
  insider_id BIGINT NOT NULL REFERENCES insiders(id) ON DELETE CASCADE,
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  transaction_date DATE NOT NULL,
  filed_date DATE,
  direction TEXT CHECK (direction IN ('buy', 'sell', 'unknown')),
  instrument_type TEXT,
  isin TEXT,
  quantity NUMERIC NOT NULL,
  unit_price NUMERIC NOT NULL,
  total_value NUMERIC NOT NULL,
  currency TEXT DEFAULT 'EUR',
  source_url TEXT,
  raw_hash TEXT NOT NULL UNIQUE,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_transactions_company_date ON transactions(company_id, transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_direction ON transactions(direction);
CREATE INDEX IF NOT EXISTS idx_transactions_insider ON transactions(insider_id);
CREATE INDEX IF NOT EXISTS idx_transactions_raw_hash ON transactions(raw_hash);
