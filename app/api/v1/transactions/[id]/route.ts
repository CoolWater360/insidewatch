/**
 * GET /api/v1/transactions/:id
 *
 * Returns a single transaction with company and insider relations.
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
  const guard = await apiGuard(request, db, "/api/v1/transactions/:id");
  if (guard.error) return guard.error;

  const { id } = await params;
  const txId = parseInt(id, 10);
  if (isNaN(txId)) return apiError("Invalid transaction id.", 400);

  const { data, error } = await db
    .from("transactions")
    .select("*, companies(id, name, ticker, sector), insiders(full_name, role)")
    .eq("id", txId)
    .single();

  if (error || !data) return apiError("Transaction not found.", 404);

  return apiJson([data as Record<string, unknown>], {
    page: 1,
    page_size: 1,
    total: 1,
    total_pages: 1,
  });
}
