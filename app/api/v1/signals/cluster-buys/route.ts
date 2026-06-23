/**
 * GET /api/v1/signals/cluster-buys
 *
 * Returns cluster-buy signals: 2+ distinct insiders at the same company
 * purchasing within a 7-day window.  Non-cash transactions (grants,
 * option exercises) are excluded.
 *
 * Query params:
 *   lookback_days   integer 7–365 (default: 90)
 *   format          json | csv (default: json)
 */

import { NextRequest } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { apiGuard } from "@/lib/api-guard";
import { apiError, apiJson, apiCsv } from "@/lib/api-response";
import { getClusterSignals } from "@/lib/queries";

export async function GET(request: NextRequest) {
  const db = getSupabaseServer();
  const guard = await apiGuard(request, db, "/api/v1/signals/cluster-buys");
  if (guard.error) return guard.error;

  const sp = new URL(request.url).searchParams;

  const rawDays = parseInt(sp.get("lookback_days") ?? "90", 10);
  const lookbackDays = isNaN(rawDays) ? 90 : Math.min(365, Math.max(7, rawDays));
  const format = sp.get("format") === "csv" ? "csv" : "json";

  const signals = await getClusterSignals(lookbackDays);

  if (format === "csv") {
    const flat = signals.flatMap((s) =>
      s.insiders.map((ins) => ({
        company_id: s.company_id,
        company_name: s.company_name,
        window_start: s.window_start,
        window_end: s.window_end,
        cluster_total_value: s.total_value,
        cluster_cash_value: s.cash_value,
        insider_name: ins.name,
        insider_role: ins.role,
        insider_date: ins.date,
        insider_quantity: ins.quantity,
        insider_total_value: ins.total_value,
        transaction_type: ins.transaction_type,
      }))
    );
    const ts = new Date().toISOString().slice(0, 10);
    return apiCsv(flat, `cluster_buys_${ts}.csv`);
  }

  return apiJson(signals as unknown as Record<string, unknown>[], {
    page: 1,
    page_size: signals.length,
    total: signals.length,
    total_pages: 1,
  });
}
