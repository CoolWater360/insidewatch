-- Phase 1: transaction type classification + data quality flags
-- Run once in the Supabase SQL editor.

ALTER TABLE transactions
  ADD COLUMN IF NOT EXISTS transaction_type TEXT DEFAULT 'buy',
  ADD COLUMN IF NOT EXISTS needs_review BOOLEAN DEFAULT FALSE;

-- Zero-price rows are non-cash grants/awards
UPDATE transactions
  SET transaction_type = 'grant',
      needs_review     = (direction = 'unknown')
  WHERE unit_price = 0 AND total_value = 0 AND quantity > 0;

-- Normal sells (non-zero value rows where direction = sell)
UPDATE transactions
  SET transaction_type = 'sell'
  WHERE direction = 'sell' AND total_value > 0;

-- Unknown direction on non-zero rows = parsing failure → flag for review
UPDATE transactions
  SET transaction_type = 'other',
      needs_review     = TRUE
  WHERE direction = 'unknown' AND total_value > 0;

-- Report
SELECT
  transaction_type,
  needs_review,
  COUNT(*) AS cnt
FROM transactions
GROUP BY transaction_type, needs_review
ORDER BY cnt DESC;
