import { getSupabaseServer } from "./supabase-server";
import type { PaginatedResult } from "./review";

// ─── Status ───────────────────────────────────────────────────────────────────

export type FilingStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "failed"
  | "skipped"
  | "retry_requested"
  | "superseded";

export const FILING_STATUS_LABELS: Record<string, string> = {
  pending:          "In attesa",
  in_progress:      "In corso",
  completed:        "Completato",
  failed:           "Fallito",
  skipped:          "Saltato",
  retry_requested:  "Riesecuzione richiesta",
  superseded:       "Superato",
};

// ─── Filings list ─────────────────────────────────────────────────────────────

export interface FilingRow {
  id: number;
  pdf_url: string;
  filing_date: string | null;
  company_name: string | null;
  status: string;
  attempt_count: number;
  max_attempts: number;
  last_attempted_at: string | null;
  next_attempt_after: string | null;
  completed_at: string | null;
  transactions_inserted: number;
  transactions_skipped_dedup: number;
  last_error: string | null;
  pdf_sha256: string | null;
  scraper_version: string | null;
  file_size_bytes: number | null;
  first_seen_at: string | null;
  has_stored_document: boolean;
  source_published_utc: string | null;
  discovered_utc: string | null;
}

export interface FilingFilters {
  status?: string;
  q?: string;
  only_failed_skipped?: boolean;
  no_storage?: boolean;
}

/** Returns the IN-list for the status filter. Exported for tests. */
export function buildStatusFilter(filters: FilingFilters): string[] | undefined {
  if (filters.only_failed_skipped) return ["failed", "skipped"];
  if (filters.status) return [filters.status];
  return undefined;
}

/** Builds an OR ilike filter string for text search. Exported for tests. */
export function buildSearchFilter(q: string): string {
  return `company_name.ilike.%${q}%,pdf_url.ilike.%${q}%`;
}

export async function getFilings(
  page: number,
  pageSize: number,
  filters: FilingFilters
): Promise<PaginatedResult<FilingRow>> {
  const db = getSupabaseServer();
  const from = (page - 1) * pageSize;
  const to = from + pageSize - 1;

  // eslint-disable-next-line prefer-const
  let query = db
    .from("filings")
    .select(
      "id, pdf_url, filing_date, company_name, status, attempt_count," +
      " max_attempts, last_attempted_at, next_attempt_after, completed_at," +
      " transactions_inserted, transactions_skipped_dedup, last_error," +
      " pdf_sha256, scraper_version, file_size_bytes, first_seen_at," +
      " storage_path, source_published_utc, discovered_utc",
      { count: "exact" }
    );

  const statusList = buildStatusFilter(filters);
  if (statusList) query = query.in("status", statusList);

  if (filters.q) query = query.or(buildSearchFilter(filters.q));

  // Filter server-side on storage_path without exposing the value
  if (filters.no_storage) query = query.is("storage_path", null);

  query = query.order("id", { ascending: false }).range(from, to);

  const { data, count, error } = await query;

  if (error) {
    console.error("getFilings error:", error.message);
    return { rows: [], total: 0, page, pageSize, totalPages: 0, queryError: error.message };
  }

  const rows: FilingRow[] = ((data ?? []) as unknown as Record<string, unknown>[]).map((raw) => ({
    id: raw.id as number,
    pdf_url: raw.pdf_url as string,
    filing_date: (raw.filing_date as string | null) ?? null,
    company_name: (raw.company_name as string | null) ?? null,
    status: raw.status as string,
    attempt_count: raw.attempt_count as number,
    max_attempts: raw.max_attempts as number,
    last_attempted_at: (raw.last_attempted_at as string | null) ?? null,
    next_attempt_after: (raw.next_attempt_after as string | null) ?? null,
    completed_at: (raw.completed_at as string | null) ?? null,
    transactions_inserted: (raw.transactions_inserted as number) ?? 0,
    transactions_skipped_dedup: (raw.transactions_skipped_dedup as number) ?? 0,
    last_error: (raw.last_error as string | null) ?? null,
    pdf_sha256: (raw.pdf_sha256 as string | null) ?? null,
    scraper_version: (raw.scraper_version as string | null) ?? null,
    file_size_bytes: (raw.file_size_bytes as number | null) ?? null,
    first_seen_at: (raw.first_seen_at as string | null) ?? null,
    has_stored_document: raw.storage_path != null,
    source_published_utc: (raw.source_published_utc as string | null) ?? null,
    discovered_utc: (raw.discovered_utc as string | null) ?? null,
  }));

  const total = count ?? 0;
  return { rows, total, page, pageSize, totalPages: Math.ceil(total / pageSize) };
}

// ─── Filing detail ─────────────────────────────────────────────────────────────

export interface FilingProcessingRun {
  id: number;
  run_at: string;
  parser_version: string | null;
  transactions_found: number;
  transactions_inserted: number;
  transactions_versioned: number;
  transactions_unchanged: number;
}

export interface FilingDetailTx {
  id: number;
  transaction_date: string;
  direction: string;
  transaction_type: string | null;
  economic_intent: string | null;
  total_value: number;
  currency: string;
  review_status: string | null;
  parser_version: string | null;
  insiders: { full_name: string } | null;
}

export interface FilingDetailFull {
  id: number;
  pdf_url: string;
  filing_date: string | null;
  company_name: string | null;
  status: string;
  attempt_count: number;
  max_attempts: number;
  last_attempted_at: string | null;
  next_attempt_after: string | null;
  completed_at: string | null;
  transactions_inserted: number;
  transactions_skipped_dedup: number;
  last_error: string | null;
  pdf_sha256: string | null;
  scraper_version: string | null;
  file_size_bytes: number | null;
  first_seen_at: string | null;
  has_stored_document: boolean;
  has_stale_claim: boolean;
  source_published_utc: string | null;
  discovered_utc: string | null;
  downloaded_utc: string | null;
  stored_utc: string | null;
  parsed_utc: string | null;
  validated_utc: string | null;
  delivered_utc: string | null;
  extracted_text_excerpt: string | null;
  transactions: FilingDetailTx[];
  processing_runs: FilingProcessingRun[];
}

const EXTRACTED_TEXT_LIMIT = 2000;

export async function getFilingDetail(id: number): Promise<FilingDetailFull | null> {
  const db = getSupabaseServer();

  const [filingResult, txResult, runsResult] = await Promise.all([
    db
      .from("filings")
      .select(
        "id, pdf_url, filing_date, company_name, status, attempt_count," +
        " max_attempts, last_attempted_at, next_attempt_after, completed_at," +
        " transactions_inserted, transactions_skipped_dedup, last_error," +
        " pdf_sha256, scraper_version, file_size_bytes, first_seen_at," +
        " storage_path, claim_token," +
        " source_published_utc, discovered_utc, downloaded_utc, stored_utc," +
        " parsed_utc, validated_utc, delivered_utc, raw_extracted_text"
      )
      .eq("id", id)
      .single(),
    db
      .from("transactions")
      .select(
        "id, transaction_date, direction, transaction_type, economic_intent," +
        " total_value, currency, review_status, parser_version," +
        " insiders(full_name)"
      )
      .eq("source_filing_id", id)
      .order("transaction_date", { ascending: false }),
    db
      .from("filing_processing_runs")
      .select(
        "id, run_at, parser_version, transactions_found, transactions_inserted," +
        " transactions_versioned, transactions_unchanged"
      )
      .eq("filing_id", id)
      .order("run_at", { ascending: false }),
  ]);

  if (filingResult.error || !filingResult.data) {
    console.error("getFilingDetail error:", filingResult.error?.message);
    return null;
  }

  const raw = filingResult.data as unknown as Record<string, unknown>;
  const rawText = raw.raw_extracted_text as string | null;

  return {
    id: raw.id as number,
    pdf_url: raw.pdf_url as string,
    filing_date: (raw.filing_date as string | null) ?? null,
    company_name: (raw.company_name as string | null) ?? null,
    status: raw.status as string,
    attempt_count: raw.attempt_count as number,
    max_attempts: raw.max_attempts as number,
    last_attempted_at: (raw.last_attempted_at as string | null) ?? null,
    next_attempt_after: (raw.next_attempt_after as string | null) ?? null,
    completed_at: (raw.completed_at as string | null) ?? null,
    transactions_inserted: (raw.transactions_inserted as number) ?? 0,
    transactions_skipped_dedup: (raw.transactions_skipped_dedup as number) ?? 0,
    last_error: (raw.last_error as string | null) ?? null,
    pdf_sha256: (raw.pdf_sha256 as string | null) ?? null,
    scraper_version: (raw.scraper_version as string | null) ?? null,
    file_size_bytes: (raw.file_size_bytes as number | null) ?? null,
    first_seen_at: (raw.first_seen_at as string | null) ?? null,
    has_stored_document: raw.storage_path != null,
    has_stale_claim: raw.claim_token != null && raw.status !== "in_progress",
    source_published_utc: (raw.source_published_utc as string | null) ?? null,
    discovered_utc: (raw.discovered_utc as string | null) ?? null,
    downloaded_utc: (raw.downloaded_utc as string | null) ?? null,
    stored_utc: (raw.stored_utc as string | null) ?? null,
    parsed_utc: (raw.parsed_utc as string | null) ?? null,
    validated_utc: (raw.validated_utc as string | null) ?? null,
    delivered_utc: (raw.delivered_utc as string | null) ?? null,
    extracted_text_excerpt: rawText
      ? rawText.length > EXTRACTED_TEXT_LIMIT
        ? rawText.slice(0, EXTRACTED_TEXT_LIMIT) + "…"
        : rawText
      : null,
    transactions: (txResult.data ?? []) as unknown as FilingDetailTx[],
    processing_runs: (runsResult.data ?? []) as unknown as FilingProcessingRun[],
  };
}
