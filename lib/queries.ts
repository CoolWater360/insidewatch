import { getSupabase } from "./supabase";
import { Company, Direction, TransactionWithRelations } from "./types";

export interface ClusterSignal {
  company_id: number;
  company_name: string;
  window_start: string; // earliest tx date in the cluster
  window_end: string;   // latest tx date in the cluster
  insiders: { name: string; role: string | null; date: string; quantity: number; total_value: number }[];
  total_value: number;
}

export interface TransactionFilters {
  companyId?: number;
  direction?: Direction;
  dateFrom?: string;
  dateTo?: string;
  sort?: string;
  order?: "asc" | "desc";
  page?: number;
  pageSize?: number;
}

export interface TransactionsResult {
  rows: TransactionWithRelations[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

// Whitelist of columns the UI is allowed to sort by (prevents arbitrary input).
const SORTABLE_COLUMNS = new Set([
  "transaction_date",
  "filed_date",
  "direction",
  "quantity",
  "unit_price",
  "total_value",
]);

const SELECT_WITH_RELATIONS =
  "*, companies(id, name, ticker, sector), insiders(full_name, role)";

export async function getTransactions(
  filters: TransactionFilters
): Promise<TransactionsResult> {
  const page = filters.page && filters.page > 0 ? filters.page : 1;
  const pageSize = filters.pageSize ?? 25;
  const supabase = getSupabase();

  const empty: TransactionsResult = { rows: [], total: 0, page, pageSize, totalPages: 0 };
  if (!supabase) return empty;

  const sortCol =
    filters.sort && SORTABLE_COLUMNS.has(filters.sort) ? filters.sort : "transaction_date";
  const ascending = filters.order === "asc";

  let query = supabase
    .from("transactions")
    .select(SELECT_WITH_RELATIONS, { count: "exact" });

  if (filters.companyId) query = query.eq("company_id", filters.companyId);
  if (filters.direction) query = query.eq("direction", filters.direction);
  if (filters.dateFrom) query = query.gte("transaction_date", filters.dateFrom);
  if (filters.dateTo) query = query.lte("transaction_date", filters.dateTo);

  const from = (page - 1) * pageSize;
  const to = from + pageSize - 1;
  query = query.order(sortCol, { ascending }).range(from, to);

  const { data, count, error } = await query;
  if (error) {
    console.error("getTransactions error:", error.message);
    return empty;
  }

  const total = count ?? 0;
  return {
    rows: (data ?? []) as unknown as TransactionWithRelations[],
    total,
    page,
    pageSize,
    totalPages: Math.ceil(total / pageSize),
  };
}

export async function getCompanies(): Promise<Company[]> {
  const supabase = getSupabase();
  if (!supabase) return [];
  const { data, error } = await supabase.from("companies").select("*").order("name");
  if (error) {
    console.error("getCompanies error:", error.message);
    return [];
  }
  return (data ?? []) as Company[];
}

export async function getCompanyById(id: number): Promise<Company | null> {
  const supabase = getSupabase();
  if (!supabase) return null;
  const { data, error } = await supabase
    .from("companies")
    .select("*")
    .eq("id", id)
    .single();
  if (error) return null;
  return data as Company;
}

export interface CompanyStats {
  total: number;
  buys: number;
  sells: number;
}

/** Lightweight buy/sell counts for a company's detail header. */
export async function getCompanyStats(companyId: number): Promise<CompanyStats> {
  const supabase = getSupabase();
  const zero: CompanyStats = { total: 0, buys: 0, sells: 0 };
  if (!supabase) return zero;

  const base = supabase
    .from("transactions")
    .select("id", { count: "exact", head: true })
    .eq("company_id", companyId);

  const [all, buys, sells] = await Promise.all([
    base,
    supabase
      .from("transactions")
      .select("id", { count: "exact", head: true })
      .eq("company_id", companyId)
      .eq("direction", "buy"),
    supabase
      .from("transactions")
      .select("id", { count: "exact", head: true })
      .eq("company_id", companyId)
      .eq("direction", "sell"),
  ]);

  return {
    total: all.count ?? 0,
    buys: buys.count ?? 0,
    sells: sells.count ?? 0,
  };
}

export interface DashboardStats {
  totalTransactions: number;
  weekCount: number;
  weekBuyValue: number;
  weekSellValue: number;
  companiesTracked: number;
  lastUpdatedAt: string | null;
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const supabase = getSupabase();
  const empty: DashboardStats = {
    totalTransactions: 0, weekCount: 0, weekBuyValue: 0,
    weekSellValue: 0, companiesTracked: 0, lastUpdatedAt: null,
  };
  if (!supabase) return empty;

  const weekAgo = new Date();
  weekAgo.setDate(weekAgo.getDate() - 7);
  const weekAgoStr = weekAgo.toISOString().slice(0, 10);

  const [total, companies, weekTx, lastTx] = await Promise.all([
    supabase.from("transactions").select("id", { count: "exact", head: true }),
    supabase.from("companies").select("id", { count: "exact", head: true }),
    supabase
      .from("transactions")
      .select("direction, total_value")
      .gte("transaction_date", weekAgoStr),
    supabase
      .from("transactions")
      .select("created_at")
      .order("created_at", { ascending: false })
      .limit(1),
  ]);

  const weekRows = weekTx.data ?? [];
  const weekBuyValue  = weekRows.filter(r => r.direction === "buy") .reduce((s, r) => s + (r.total_value || 0), 0);
  const weekSellValue = weekRows.filter(r => r.direction === "sell").reduce((s, r) => s + (r.total_value || 0), 0);

  return {
    totalTransactions: total.count ?? 0,
    weekCount:         weekRows.length,
    weekBuyValue,
    weekSellValue,
    companiesTracked:  companies.count ?? 0,
    lastUpdatedAt:     lastTx.data?.[0]?.created_at ?? null,
  };
}

/**
 * Cluster-buying signal: 2+ distinct insiders at the same company
 * with 'buy' transactions within a rolling 7-day window.
 * Looks back 90 days to keep the dataset manageable.
 */
export async function getClusterSignals(lookbackDays = 90): Promise<ClusterSignal[]> {
  const supabase = getSupabase();
  if (!supabase) return [];

  const since = new Date();
  since.setDate(since.getDate() - lookbackDays);
  const sinceStr = since.toISOString().slice(0, 10);

  const { data, error } = await supabase
    .from("transactions")
    .select("company_id, transaction_date, quantity, total_value, companies(id, name), insiders(full_name, role)")
    .eq("direction", "buy")
    .gte("transaction_date", sinceStr)
    .order("company_id")
    .order("transaction_date");

  if (error || !data) {
    console.error("getClusterSignals error:", error?.message);
    return [];
  }

  // Group by company
  const byCompany = new Map<number, typeof data>();
  for (const row of data) {
    const cid = row.company_id as number;
    if (!byCompany.has(cid)) byCompany.set(cid, []);
    byCompany.get(cid)!.push(row);
  }

  const signals: ClusterSignal[] = [];

  for (const [cid, rows] of byCompany) {
    const sorted = [...rows].sort(
      (a, b) => a.transaction_date.localeCompare(b.transaction_date)
    );
    const companyInfo = (sorted[0].companies as unknown as { id: number; name: string } | null);
    const companyName = companyInfo?.name ?? String(cid);

    // Sliding window: for each tx i, find all tx j where date_j - date_i <= 7 days
    // and count distinct insiders. Emit each unique cluster once (track by window_start).
    const emitted = new Set<string>();

    for (let i = 0; i < sorted.length; i++) {
      const startDate = sorted[i].transaction_date as string;
      const startMs = new Date(startDate).getTime();
      const windowTxs = [sorted[i]];

      for (let j = i + 1; j < sorted.length; j++) {
        const jMs = new Date(sorted[j].transaction_date as string).getTime();
        if (jMs - startMs <= 7 * 86400_000) {
          windowTxs.push(sorted[j]);
        } else {
          break;
        }
      }

      // Distinct insider names
      const distinctInsiders = new Set(
        windowTxs.map((t) => (t.insiders as unknown as { full_name: string } | null)?.full_name ?? "")
      );

      if (distinctInsiders.size >= 2) {
        const endDate = windowTxs[windowTxs.length - 1].transaction_date as string;
        const key = `${cid}:${startDate}`;
        if (!emitted.has(key)) {
          emitted.add(key);
          signals.push({
            company_id: cid,
            company_name: companyName,
            window_start: startDate,
            window_end: endDate,
            insiders: windowTxs.map((t) => ({
              name: (t.insiders as unknown as { full_name: string; role: string | null } | null)?.full_name ?? "Unknown",
              role: (t.insiders as unknown as { full_name: string; role: string | null } | null)?.role ?? null,
              date: t.transaction_date as string,
              quantity: t.quantity as number,
              total_value: t.total_value as number,
            })),
            total_value: windowTxs.reduce((s, t) => s + (t.total_value as number), 0),
          });
        }
      }
    }
  }

  // Most recent clusters first
  signals.sort((a, b) => b.window_end.localeCompare(a.window_end));
  return signals;
}
