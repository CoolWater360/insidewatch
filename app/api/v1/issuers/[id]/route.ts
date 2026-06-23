/**
 * GET /api/v1/issuers/:id
 *
 * Returns a single issuer with its aliases.
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
  const guard = await apiGuard(request, db, "/api/v1/issuers/:id");
  if (guard.error) return guard.error;

  const { id } = await params;
  const issuerId = parseInt(id, 10);
  if (isNaN(issuerId)) return apiError("Invalid issuer id.", 400);

  const { data, error } = await db
    .from("issuers")
    .select(
      "id, canonical_name, short_name, lei, country, market, sector, status, created_at, updated_at, " +
      "issuer_aliases(id, alias, alias_type, is_primary, source)"
    )
    .eq("id", issuerId)
    .single();

  if (error || !data) return apiError("Issuer not found.", 404);

  return apiJson([data as unknown as Record<string, unknown>], {
    page: 1,
    page_size: 1,
    total: 1,
    total_pages: 1,
  });
}
