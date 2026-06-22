export type Direction = "buy" | "sell" | "unknown";

export interface Company {
  id: number;
  name: string;
  ticker: string | null;
  isin: string | null;
  sector: string | null;
  ir_internal_dealing_url: string | null;
}

export interface Insider {
  id: number;
  full_name: string;
  role: string | null;
  company_id: number;
}

export type TransactionType =
  | "buy"
  | "sell"
  | "grant"
  | "option_exercise"
  | "sell_to_cover"
  | "other";

export interface Transaction {
  id: number;
  insider_id: number;
  company_id: number;
  transaction_date: string; // YYYY-MM-DD
  filed_date: string | null;
  direction: Direction;
  transaction_type: TransactionType | null;
  needs_review: boolean | null;
  instrument_type: string | null;
  isin: string | null;
  quantity: number;
  unit_price: number;
  total_value: number;
  currency: string;
  source_url: string | null;
  raw_hash: string;
}

/** Transaction joined with its company and insider, as returned by list queries. */
export interface TransactionWithRelations extends Transaction {
  companies: Pick<Company, "id" | "name" | "ticker" | "sector"> | null;
  insiders: Pick<Insider, "full_name" | "role"> | null;
}
