export type Direction = "buy" | "sell" | "unknown";

export type TransactionType =
  | "buy"
  | "sell"
  | "grant"
  | "option_exercise"
  | "sell_to_cover"
  | "other";

// Phase 7 will expand this to the full taxonomy (open_market_buy, vesting, etc.)
// and migrate existing values. For now these three cover all live records.
export type EconomicIntent = "discretionary" | "mechanical" | "unclear";

export type ReviewStatus =
  | "pending_review"
  | "under_review"
  | "confirmed"
  | "rejected"
  | "corrected";

export interface Company {
  id: number;
  name: string;
  ticker: string | null;
  isin: string | null;
  sector: string | null;
  ir_internal_dealing_url: string | null;
  priority_tier: number | null;
}

export interface Insider {
  id: number;
  full_name: string;
  role: string | null;
  company_id: number;
  insider_verified: boolean;
  role_category: string;
}

export interface Transaction {
  id: number;
  insider_id: number;
  company_id: number;

  // Core event
  transaction_date: string; // YYYY-MM-DD
  filed_date: string | null;
  direction: Direction;

  // Classification
  transaction_type: TransactionType | null; // null = legacy record
  economic_intent: EconomicIntent | null;

  // Instrument
  instrument_type: string | null;
  isin: string | null;

  // Financials
  quantity: number;
  unit_price: number;
  total_value: number;
  currency: string;

  // Source provenance
  source_url: string | null;
  raw_document_sha256: string | null;
  source_transaction_index: number | null;
  source_filing_id: number | null;
  raw_hash: string;

  // Data quality
  needs_review: boolean | null;
  extraction_confidence: number | null;    // 0.0–1.0
  classification_confidence: number | null; // 0.0–1.0

  // Review workflow
  review_status: ReviewStatus | null;
  review_reason: string | null;

  // Parser provenance
  parser_version: string | null; // '0.0.0' = legacy, '1.x.x' = Phase 1+
}

/** Transaction joined with its company and insider, as returned by list queries. */
export interface TransactionWithRelations extends Transaction {
  companies: Pick<Company, "id" | "name" | "ticker" | "sector"> | null;
  insiders: Pick<Insider, "full_name" | "role"> | null;
}
