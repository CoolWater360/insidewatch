/**
 * GET /api/v1/filings/:id
 *
 * Returns a single filing record including latency timestamps.
 */

import { NextRequest } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { apiGuard } from "@/lib/api-guard";
import { apiError, apiJson } from "@/lib/api-response";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const db = getSupabaseServer();
  const guard = await apiGuard(request, db, "/api/v1/filings/:id");
  if (guard.error) return guard.error;

  const { id } = await params;
  const filingId = parseInt(id, 10);
  if (isNaN(filingId)) return apiError("Invalid filing id.", 400);

  const { data, error } = await db
    .from("filings")
    .select(
      "id, pdf_url, filing_date, company_name, status, attempt_count, " +
      "max_attempts, last_attempted_at, next_attempt_after, " +
      "pdf_sha256, error_message, first_seen_at, completed_at, " +
      "source_published_utc, discovered_utc, downloaded_utc, " +
      "parsed_utc, validated_utc, delivered_utc"
    )
    .eq("id", filingId)
    .single();

  if (error || !data) return apiError("Filing not found.", 404);

  return apiJson([data as unknown as Record<string, unknown>], {
    page: 1,
    page_size: 1,
    total: 1,
    total_pages: 1,
  });
}
