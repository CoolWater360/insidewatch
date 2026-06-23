/**
 * GET /api/v1/transactions
 *
 * Query params:
 *   company_id       integer
 *   direction        buy | sell | unknown
 *   date_from        YYYY-MM-DD
 *   date_to          YYYY-MM-DD
 *   transaction_type buy | sell | grant | …  (any TransactionType)
 *   cash_only        true  — exclude grants and option_exercise (default: true)
 *   sort             transaction_date | filed_date | total_value | quantity (default: transaction_date)
 *   order            asc | desc (default: desc)
 *   page             integer ≥ 1 (default: 1)
 *   page_size        1–100 (default: 25)
 *   format           json | csv (default: json)
 */

import { NextRequest } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { apiGuard } from "@/lib/api-guard";
import { apiError, apiJson, apiCsv, parsePagination } from "@/lib/api-response";

const SORTABLE = new Set(["transaction_date", "filed_date", "total_value", "quantity"]);

// Explicit allowlist — excludes internal-only fields:
//   raw_hash, raw_document_sha256, identity_hash (forensic dedup keys)
//   review_notes (operator annotations, internal only)
//   raw_nature_text (verbatim PDF extract, voluminous and internal)
//   classification_rationale, classification_override, classification_overridden_by/at
//   needs_review, source_transaction_index, source_filing_id, parser_version
//   is_current, version_number, superseded_by/at (versioning internals)
const SELECT =
  "id, transaction_date, filed_date, company_id, insider_id, " +
  "direction, transaction_type, economic_intent, " +
  "instrument_type, isin, " +
  "quantity, unit_price, total_value, currency, " +
  "review_status, source_url, " +
  "extraction_confidence, classification_confidence, " +
  "companies(id, name, ticker, sector), insiders(full_name, role)";

export async function GET(request: NextRequest) {
  const db = getSupabaseServer();
  const guard = await apiGuard(request, db, "/api/v1/transactions");
  if (guard.error) return guard.error;

  const sp = new URL(request.url).searchParams;
  const { page, pageSize, offset } = parsePagination(sp);

  const sort = SORTABLE.has(sp.get("sort") ?? "") ? sp.get("sort")! : "transaction_date";
  const ascending = sp.get("order") === "asc";
  const cashOnly = sp.get("cash_only") !== "false";
  const format = sp.get("format") === "csv" ? "csv" : "json";

  let query = db
    .from("transactions")
    .select(SELECT, { count: "exact" });

  if (cashOnly) {
    query = query.or("transaction_type.is.null,transaction_type.not.in.(grant,option_exercise)");
  }

  const companyId = sp.get("company_id");
  if (companyId) query = query.eq("company_id", parseInt(companyId, 10));

  const direction = sp.get("direction");
  if (direction) query = query.eq("direction", direction);

  const txType = sp.get("transaction_type");
  if (txType) query = query.eq("transaction_type", txType);

  const dateFrom = sp.get("date_from");
  if (dateFrom) query = query.gte("transaction_date", dateFrom);

  const dateTo = sp.get("date_to");
  if (dateTo) query = query.lte("transaction_date", dateTo);

  query = query.order(sort, { ascending }).range(offset, offset + pageSize - 1);

  const { data, count, error } = await query;
  if (error) return apiError("Failed to fetch transactions.", 500);

  const total = count ?? 0;
  const rows = (data ?? []) as unknown as Record<string, unknown>[];

  if (format === "csv") {
    const flat = rows.map((r) => {
      const co = r.companies as Record<string, unknown> | null;
      const ins = r.insiders as Record<string, unknown> | null;
      return {
        id: r.id,
        transaction_date: r.transaction_date,
        filed_date: r.filed_date,
        company_id: r.company_id,
        company_name: co?.name ?? null,
        ticker: co?.ticker ?? null,
        sector: co?.sector ?? null,
        insider_name: ins?.full_name ?? null,
        insider_role: ins?.role ?? null,
        direction: r.direction,
        transaction_type: r.transaction_type,
        economic_intent: r.economic_intent,
        quantity: r.quantity,
        unit_price: r.unit_price,
        total_value: r.total_value,
        currency: r.currency,
        instrument_type: r.instrument_type,
        isin: r.isin,
        review_status: r.review_status,
        source_url: r.source_url,
      };
    });
    const ts = new Date().toISOString().slice(0, 10);
    return apiCsv(flat, `transactions_${ts}.csv`);
  }

  return apiJson(rows, {
    page,
    page_size: pageSize,
    total,
    total_pages: Math.ceil(total / pageSize),
  });
}
