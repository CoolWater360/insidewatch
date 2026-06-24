import { getSupabaseServer } from "./supabase-server";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ReviewQueueCounts {
  pendingTransactions: number;
  failedFilings: number;
  pendingIssuers: number;
}

export interface ReviewTransaction {
  id: number;
  transaction_date: string;
  direction: string;
  transaction_type: string | null;
  economic_intent: string | null;
  quantity: number;
  unit_price: number;
  total_value: number;
  currency: string;
  needs_review: boolean;
  review_status: string | null;
  review_reason: string | null;
  review_notes?: string | null;
  extraction_confidence: number | null;
  classification_rationale: string | null;
  raw_nature_text: string | null;
  classification_override: boolean;
  isin: string | null;
  companies: { id: number; name: string } | null;
  insiders: { full_name: string; role: string | null } | null;
}

export interface ReviewFiling {
  id: number;
  pdf_url: string;
  filing_date: string | null;
  company_name: string | null;
  status: string;
  attempt_count: number;
  last_attempted_at: string | null;
  last_error: string | null;
  scraper_version: string | null;
  transactions_inserted: number;
}

export interface UnmatchedIssuer {
  id: number;
  raw_name: string;
  raw_isin: string | null;
  status: string;
  suggestion_issuer_id: number | null;
  created_at: string;
}

export interface PaginatedResult<T> {
  rows: T[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

// ─── Queue counts ─────────────────────────────────────────────────────────────

export async function getReviewQueueCounts(): Promise<ReviewQueueCounts> {
  const db = getSupabaseServer();

  const [txResult, filingResult, issuerResult] = await Promise.all([
    db
      .from("transactions")
      .select("id", { count: "exact", head: true })
      .eq("needs_review", true)
      .or("review_status.is.null,review_status.in.(pending_review,under_review)"),
    db
      .from("filings")
      .select("id", { count: "exact", head: true })
      .in("status", ["failed", "skipped"]),
    db
      .from("unmatched_issuers")
      .select("id", { count: "exact", head: true })
      .eq("status", "pending"),
  ]);

  return {
    pendingTransactions: txResult.count ?? 0,
    failedFilings: filingResult.count ?? 0,
    pendingIssuers: issuerResult.count ?? 0,
  };
}

// ─── Transaction review queue ─────────────────────────────────────────────────

export async function getTransactionsForReview(
  page = 1,
  pageSize = 25
): Promise<PaginatedResult<ReviewTransaction>> {
  const db = getSupabaseServer();
  const from = (page - 1) * pageSize;
  const to = from + pageSize - 1;

  const { data, count, error } = await db
    .from("transactions")
    .select(
      "id, transaction_date, direction, transaction_type, economic_intent," +
      " quantity, unit_price, total_value, currency, needs_review, review_status," +
      " review_reason, extraction_confidence, classification_rationale," +
      " raw_nature_text, classification_override, isin," +
      " companies(id, name), insiders(full_name, role)",
      { count: "exact" }
    )
    .eq("needs_review", true)
    .or("review_status.is.null,review_status.in.(pending_review,under_review)")
    .order("extraction_confidence", { ascending: true, nullsFirst: true })
    .order("created_at", { ascending: false })
    .range(from, to);

  if (error) {
    console.error("getTransactionsForReview error:", error.message);
    return { rows: [], total: 0, page, pageSize, totalPages: 0 };
  }

  const total = count ?? 0;
  return {
    rows: (data ?? []) as unknown as ReviewTransaction[],
    total,
    page,
    pageSize,
    totalPages: Math.ceil(total / pageSize),
  };
}

// ─── Failed filings queue ─────────────────────────────────────────────────────

export async function getFailedFilings(
  page = 1,
  pageSize = 25
): Promise<PaginatedResult<ReviewFiling>> {
  const db = getSupabaseServer();
  const from = (page - 1) * pageSize;
  const to = from + pageSize - 1;

  const { data, count, error } = await db
    .from("filings")
    .select(
      "id, pdf_url, filing_date, company_name, status, attempt_count," +
      " last_attempted_at, last_error, scraper_version, transactions_inserted",
      { count: "exact" }
    )
    .in("status", ["failed", "skipped"])
    .order("last_attempted_at", { ascending: false, nullsFirst: true })
    .range(from, to);

  if (error) {
    console.error("getFailedFilings error:", error.message);
    return { rows: [], total: 0, page, pageSize, totalPages: 0 };
  }

  const total = count ?? 0;
  return {
    rows: (data ?? []) as unknown as ReviewFiling[],
    total,
    page,
    pageSize,
    totalPages: Math.ceil(total / pageSize),
  };
}

// ─── Unmatched issuers queue ──────────────────────────────────────────────────

export async function getUnmatchedIssuers(
  page = 1,
  pageSize = 25
): Promise<PaginatedResult<UnmatchedIssuer>> {
  const db = getSupabaseServer();
  const from = (page - 1) * pageSize;
  const to = from + pageSize - 1;

  const { data, count, error } = await db
    .from("unmatched_issuers")
    .select(
      "id, raw_name, raw_isin, status, suggestion_issuer_id, created_at",
      { count: "exact" }
    )
    .eq("status", "pending")
    .order("created_at", { ascending: false })
    .range(from, to);

  if (error) {
    console.error("getUnmatchedIssuers error:", error.message);
    return { rows: [], total: 0, page, pageSize, totalPages: 0 };
  }

  const total = count ?? 0;
  return {
    rows: (data ?? []) as unknown as UnmatchedIssuer[],
    total,
    page,
    pageSize,
    totalPages: Math.ceil(total / pageSize),
  };
}
