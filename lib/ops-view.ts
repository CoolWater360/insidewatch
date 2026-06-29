/**
 * Phase 5 — server-side query helpers for the Operations workspace.
 *
 * All queries use getSupabaseServer() (service-role key) and are safe only
 * for internal server components. Never import from client components.
 *
 * Pure helper functions (classify*, format*) are exported for testing.
 */

import { unstable_noStore as noStore } from "next/cache";
import { getSupabaseServer } from "./supabase-server";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface FilingStatusCounts {
  pending:        number;
  in_progress:    number;
  completed:      number;
  failed:         number;
  skipped:        number;
  retry_eligible: number; // failed AND attempt_count < max_attempts
  total:          number;
}

export interface ProcessingRunRow {
  id:                      number;
  filing_id:               number;
  parser_version:          string | null;
  run_at:                  string;
  transactions_found:      number;
  transactions_inserted:   number;
  transactions_versioned:  number;
  transactions_unchanged:  number;
  company_name:            string | null;
}

export interface FailedFilingRow {
  id:                  number;
  company_name:        string | null;
  filing_date:         string | null;
  status:              string;
  attempt_count:       number;
  max_attempts:        number;
  last_attempted_at:   string | null;
  last_error:          string | null;
  scraper_version:     string | null;
  next_attempt_after:  string | null;
  pdf_url:             string;
}

export interface PipelineLatencyStats {
  median_hours: number | null;
  p95_hours:    number | null;
  sample_count: number;
  note:         string;
}

export interface LastActivityRow {
  last_run_at:             string | null;
  last_run_parser_version: string | null;
  last_run_filing_id:      number | null;
}

// ─── Pure helpers (exported for testing) ─────────────────────────────────────

export const FILING_STATUS_LABELS: Record<string, string> = {
  pending:     "In attesa",
  in_progress: "In elaborazione",
  completed:   "Completato",
  failed:      "Fallito",
  skipped:     "Saltato",
};

export function filingStatusLabel(status: string): string {
  return FILING_STATUS_LABELS[status] ?? status;
}

export function classifyFilingHealth(
  counts: Pick<FilingStatusCounts, "failed" | "skipped" | "pending">
): "ok" | "warn" | "error" {
  if (counts.failed > 0) return "error";
  if (counts.skipped > 0 || counts.pending > 0) return "warn";
  return "ok";
}

export function isRetryEligible(
  status: string,
  attemptCount: number,
  maxAttempts: number
): boolean {
  return status === "failed" && attemptCount < maxAttempts;
}

export function formatLatencyHours(hours: number | null): string {
  if (hours === null) return "—";
  if (hours < 1)  return `${Math.round(hours * 60)} min`;
  if (hours < 24) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} gg`;
}

/**
 * Compute median and p95 from a sorted array of numbers.
 * Returns null when the array is empty.
 */
export function computePercentiles(
  sorted: number[]
): { median: number | null; p95: number | null } {
  if (sorted.length === 0) return { median: null, p95: null };
  const mid = Math.floor(sorted.length / 2);
  const median =
    sorted.length % 2 === 0
      ? (sorted[mid - 1] + sorted[mid]) / 2
      : sorted[mid];
  const p95 = sorted[Math.floor(sorted.length * 0.95)];
  return { median, p95: p95 ?? sorted[sorted.length - 1] };
}

// ─── Async queries (server-only) ──────────────────────────────────────────────

export async function getFilingStatusCounts(): Promise<FilingStatusCounts> {
  noStore();
  const db = getSupabaseServer();

  const [pending, inProgress, completed, failed, skipped, total] =
    await Promise.all([
      db.from("filings").select("id", { count: "exact", head: true }).eq("status", "pending"),
      db.from("filings").select("id", { count: "exact", head: true }).eq("status", "in_progress"),
      db.from("filings").select("id", { count: "exact", head: true }).eq("status", "completed"),
      db.from("filings").select("id", { count: "exact", head: true }).eq("status", "failed"),
      db.from("filings").select("id", { count: "exact", head: true }).eq("status", "skipped"),
      db.from("filings").select("id", { count: "exact", head: true }),
    ]);

  // retry_eligible: status=failed AND attempt_count < max_attempts
  // Supabase doesn't support cross-column comparisons in a filter easily,
  // so we fetch failed filings and count in JS.
  const { data: failedRows } = await db
    .from("filings")
    .select("attempt_count, max_attempts")
    .eq("status", "failed");

  const retryCount = (failedRows ?? []).filter(
    (r: { attempt_count: number; max_attempts: number }) =>
      r.attempt_count < r.max_attempts
  ).length;

  return {
    pending:        pending.count ?? 0,
    in_progress:    inProgress.count ?? 0,
    completed:      completed.count ?? 0,
    failed:         failed.count ?? 0,
    skipped:        skipped.count ?? 0,
    retry_eligible: retryCount,
    total:          total.count ?? 0,
  };
}

export async function getRecentProcessingRuns(limit = 25): Promise<ProcessingRunRow[]> {
  noStore();
  const db = getSupabaseServer();

  const { data, error } = await db
    .from("filing_processing_runs")
    .select(
      "id, filing_id, parser_version, run_at," +
      " transactions_found, transactions_inserted, transactions_versioned, transactions_unchanged," +
      " filings(company_name)"
    )
    .order("run_at", { ascending: false })
    .limit(limit);

  if (error || !data) {
    console.error("getRecentProcessingRuns error:", error?.message);
    return [];
  }

  return (data as unknown as Record<string, unknown>[]).map((row) => ({
    id:                     row.id as number,
    filing_id:              row.filing_id as number,
    parser_version:         (row.parser_version as string | null) ?? null,
    run_at:                 row.run_at as string,
    transactions_found:     (row.transactions_found as number) ?? 0,
    transactions_inserted:  (row.transactions_inserted as number) ?? 0,
    transactions_versioned: (row.transactions_versioned as number) ?? 0,
    transactions_unchanged: (row.transactions_unchanged as number) ?? 0,
    company_name:           ((row.filings as { company_name: string | null } | null)?.company_name) ?? null,
  }));
}

export async function getFailedFilingsSample(limit = 20): Promise<FailedFilingRow[]> {
  noStore();
  const db = getSupabaseServer();

  const { data, error } = await db
    .from("filings")
    .select(
      "id, company_name, filing_date, status, attempt_count, max_attempts," +
      " last_attempted_at, last_error, scraper_version, next_attempt_after, pdf_url"
    )
    .in("status", ["failed", "skipped"])
    .order("last_attempted_at", { ascending: false, nullsFirst: true })
    .limit(limit);

  if (error || !data) {
    console.error("getFailedFilingsSample error:", error?.message);
    return [];
  }

  return data as unknown as FailedFilingRow[];
}

export async function getPipelineLatencyStats(): Promise<PipelineLatencyStats> {
  noStore();
  const db = getSupabaseServer();

  // Only completed filings with both timestamps are valid for latency measurement.
  const { data, error } = await db
    .from("filings")
    .select("first_seen_at, completed_at")
    .eq("status", "completed")
    .not("completed_at", "is", null)
    .not("first_seen_at", "is", null)
    .order("completed_at", { ascending: false })
    .limit(200);

  if (error || !data || data.length === 0) {
    return { median_hours: null, p95_hours: null, sample_count: 0, note: "Dato non disponibile" };
  }

  const hours = (data as { first_seen_at: string; completed_at: string }[])
    .map((r) => (new Date(r.completed_at).getTime() - new Date(r.first_seen_at).getTime()) / 3_600_000)
    .filter((h) => h >= 0)
    .sort((a, b) => a - b);

  const { median, p95 } = computePercentiles(hours);

  return {
    median_hours: median !== null ? Math.round(median * 10) / 10 : null,
    p95_hours:    p95 !== null    ? Math.round(p95    * 10) / 10 : null,
    sample_count: hours.length,
    note: "scoperta → completamento (fasi interne non separate)",
  };
}

export async function getLastActivity(): Promise<LastActivityRow> {
  noStore();
  const db = getSupabaseServer();

  const { data } = await db
    .from("filing_processing_runs")
    .select("run_at, parser_version, filing_id")
    .order("run_at", { ascending: false })
    .limit(1);

  if (!data || data.length === 0) {
    return { last_run_at: null, last_run_parser_version: null, last_run_filing_id: null };
  }

  const row = data[0] as { run_at: string; parser_version: string | null; filing_id: number };
  return {
    last_run_at:             row.run_at,
    last_run_parser_version: row.parser_version,
    last_run_filing_id:      row.filing_id,
  };
}

export async function getVersionedTransactionCount(): Promise<number> {
  noStore();
  const db = getSupabaseServer();
  const { count } = await db
    .from("transaction_versions")
    .select("id", { count: "exact", head: true });
  return count ?? 0;
}
