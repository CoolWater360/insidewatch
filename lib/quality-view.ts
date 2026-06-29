/**
 * Phase 5 — server-side query helpers for the Data Quality workspace.
 *
 * All queries use getSupabaseServer() (service-role key) and are safe only
 * for internal server components. Never import from client components.
 *
 * Pure helper functions (classify*, label*, format*) are exported for testing.
 */

import { unstable_noStore as noStore } from "next/cache";
import { getSupabaseServer } from "./supabase-server";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface QualityReviewSummary {
  total: number;
  confirmed: number;
  corrected: number;
  rejected: number;
  fixture_eligible_pending: number; // fixture_eligible=true AND fixture_created=false
}

export interface CategoryBreakdownRow {
  category: string;
  count: number;
  label: string;
}

export interface ParserVersionQualityRow {
  parser_version: string;
  tx_count: number;
  review_count: number;
  corrected_count: number;
}

export interface TransactionQualityStats {
  unknown_direction_count: number;
  unknown_type_count: number;
  needs_review_count: number;
  total_transactions: number;
}

export interface RecentQualityReview {
  id: number;
  transaction_id: number;
  parser_version: string;
  review_category: string;
  outcome: string;
  fixture_eligible: boolean;
  reviewed_at: string;
  reviewed_by: string;
  correction_notes: string | null;
}

// ─── Pure helpers (exported for testing) ─────────────────────────────────────

export const REVIEW_OUTCOME_LABELS: Record<string, string> = {
  confirmed: "Confermato",
  corrected: "Corretto",
  rejected:  "Rifiutato",
};

export const REVIEW_CATEGORY_LABELS: Record<string, string> = {
  unknown_direction: "Direzione non determinata",
  unknown_type:      "Tipo sconosciuto",
  low_confidence:    "Bassa confidenza",
  corporate_action:  "Corporate action",
  vehicle_entity:    "Veicolo / fiduciaria",
  issuer_resolution: "Emittente non risolto",
  other:             "Altro",
};

export function qualityOutcomeLabel(outcome: string): string {
  return REVIEW_OUTCOME_LABELS[outcome] ?? outcome;
}

export function reviewCategoryLabel(category: string): string {
  return REVIEW_CATEGORY_LABELS[category] ?? category;
}

export function classifyReviewHealth(
  summary: QualityReviewSummary
): "ok" | "warn" | "none" {
  if (summary.total === 0) return "none";
  const correctionRate = summary.total > 0
    ? (summary.corrected + summary.rejected) / summary.total
    : 0;
  return correctionRate > 0.3 ? "warn" : "ok";
}

export function formatConfidencePct(value: number | null): string {
  if (value === null) return "—";
  return `${Math.round(value * 100)}%`;
}

// ─── Async queries (server-only) ──────────────────────────────────────────────

export async function getQualityReviewSummary(): Promise<QualityReviewSummary> {
  noStore();
  const db = getSupabaseServer();

  const [
    totalResult,
    confirmedResult,
    correctedResult,
    rejectedResult,
    fixtureResult,
  ] = await Promise.all([
    db.from("quality_reviews").select("id", { count: "exact", head: true }),
    db.from("quality_reviews").select("id", { count: "exact", head: true }).eq("outcome", "confirmed"),
    db.from("quality_reviews").select("id", { count: "exact", head: true }).eq("outcome", "corrected"),
    db.from("quality_reviews").select("id", { count: "exact", head: true }).eq("outcome", "rejected"),
    db.from("quality_reviews").select("id", { count: "exact", head: true })
      .eq("fixture_eligible", true).eq("fixture_created", false),
  ]);

  return {
    total:                    totalResult.count ?? 0,
    confirmed:                confirmedResult.count ?? 0,
    corrected:                correctedResult.count ?? 0,
    rejected:                 rejectedResult.count ?? 0,
    fixture_eligible_pending: fixtureResult.count ?? 0,
  };
}

export async function getReviewCategoryBreakdown(): Promise<CategoryBreakdownRow[]> {
  noStore();
  const db = getSupabaseServer();

  const categories = [
    "unknown_direction",
    "unknown_type",
    "low_confidence",
    "corporate_action",
    "vehicle_entity",
    "issuer_resolution",
    "other",
  ];

  const results = await Promise.all(
    categories.map((cat) =>
      db.from("quality_reviews")
        .select("id", { count: "exact", head: true })
        .eq("review_category", cat)
    )
  );

  return categories.map((cat, i) => ({
    category: cat,
    label:    reviewCategoryLabel(cat),
    count:    results[i].count ?? 0,
  })).filter((r) => r.count > 0);
}

export async function getTransactionQualityStats(): Promise<TransactionQualityStats> {
  noStore();
  const db = getSupabaseServer();

  const [unknownDir, unknownType, needsReview, total] = await Promise.all([
    db.from("transactions").select("id", { count: "exact", head: true }).eq("direction", "unknown"),
    db.from("transactions").select("id", { count: "exact", head: true }).eq("transaction_type", "unknown"),
    db.from("transactions").select("id", { count: "exact", head: true }).eq("needs_review", true),
    db.from("transactions").select("id", { count: "exact", head: true }),
  ]);

  return {
    unknown_direction_count: unknownDir.count ?? 0,
    unknown_type_count:      unknownType.count ?? 0,
    needs_review_count:      needsReview.count ?? 0,
    total_transactions:      total.count ?? 0,
  };
}

export async function getParserVersionQuality(): Promise<ParserVersionQualityRow[]> {
  noStore();
  const db = getSupabaseServer();

  // Fetch distinct parser versions from transactions
  const { data: txVersions } = await db
    .from("transactions")
    .select("parser_version")
    .not("parser_version", "is", null)
    .order("parser_version");

  if (!txVersions) return [];

  // Deduplicate
  const versions = [...new Set((txVersions as { parser_version: string }[])
    .map((r) => r.parser_version).filter(Boolean))];

  if (versions.length === 0) return [];

  const rows = await Promise.all(versions.map(async (ver) => {
    const [txCount, reviewCount, correctedCount] = await Promise.all([
      db.from("transactions").select("id", { count: "exact", head: true }).eq("parser_version", ver),
      db.from("quality_reviews").select("id", { count: "exact", head: true }).eq("parser_version", ver),
      db.from("quality_reviews").select("id", { count: "exact", head: true })
        .eq("parser_version", ver).eq("outcome", "corrected"),
    ]);
    return {
      parser_version:  ver,
      tx_count:        txCount.count ?? 0,
      review_count:    reviewCount.count ?? 0,
      corrected_count: correctedCount.count ?? 0,
    };
  }));

  return rows.sort((a, b) => b.tx_count - a.tx_count);
}

export async function getRecentQualityReviews(limit = 20): Promise<RecentQualityReview[]> {
  noStore();
  const db = getSupabaseServer();

  const { data, error } = await db
    .from("quality_reviews")
    .select(
      "id, transaction_id, parser_version, review_category, outcome," +
      " fixture_eligible, reviewed_at, reviewed_by, correction_notes"
    )
    .order("reviewed_at", { ascending: false })
    .limit(limit);

  if (error || !data) {
    console.error("getRecentQualityReviews error:", error?.message);
    return [];
  }

  return data as unknown as RecentQualityReview[];
}
