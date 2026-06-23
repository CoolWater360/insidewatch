/**
 * GET /api/v1/issuers
 *
 * Returns issuers from the issuer master table.
 *
 * Query params:
 *   status      active | delisted | suspended | pending_review (default: active)
 *   country     ISO 3166-1 alpha-2 (default: IT)
 *   page        integer ≥ 1 (default: 1)
 *   page_size   1–100 (default: 50)
 */

import { NextRequest } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { apiGuard } from "@/lib/api-guard";
import { apiError, apiJson, parsePagination } from "@/lib/api-response";

const VALID_STATUSES = new Set(["active", "delisted", "suspended", "pending_review"]);

export async function GET(request: NextRequest) {
  const db = getSupabaseServer();
  const guard = await apiGuard(request, db, "/api/v1/issuers");
  if (guard.error) return guard.error;

  const sp = new URL(request.url).searchParams;
  const { page, pageSize, offset } = parsePagination(sp);

  const status = sp.get("status") ?? "active";
  const country = sp.get("country") ?? null;

  if (!VALID_STATUSES.has(status)) {
    return apiError(`Invalid status. Must be one of: ${[...VALID_STATUSES].join(", ")}.`, 400);
  }

  let query = db
    .from("issuers")
    .select("id, canonical_name, short_name, lei, country, market, sector, status, created_at, updated_at", {
      count: "exact",
    })
    .eq("status", status)
    .order("canonical_name");

  if (country) query = query.eq("country", country.toUpperCase());
  query = query.range(offset, offset + pageSize - 1);

  const { data, count, error } = await query;
  if (error) return apiError("Failed to fetch issuers.", 500);

  const total = count ?? 0;
  return apiJson((data ?? []) as Record<string, unknown>[], {
    page,
    page_size: pageSize,
    total,
    total_pages: Math.ceil(total / pageSize),
  });
}
