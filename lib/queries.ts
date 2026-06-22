import { getSupabase } from "./supabase";
import { Company, Direction, TransactionWithRelations } from "./types";

export interface ClusterInsider {
  name: string;
  role: string | null;
  date: string;
  quantity: number;
  total_value: number;
  transaction_type: string | null;
}

export interface ClusterSignal {
  company_id: number;
  company_name: string;
  window_start: string;
  window_end: string;
  insiders: ClusterInsider[];
  total_value: number;
  /** Sum of cash (non-grant, non-option) transactions only */
  cash_value: number;
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

const NON_CASH_TYPES = new Set(["grant", "option_exercise"]);

/**
 * Cluster-buying signal: 2+ distinct insiders at the same company
 * with 'buy' (or grant/option_exercise) transactions within a rolling
 * 7-day window. Looks back `lookbackDays` days.
 *
 * Algorithm: greedy left-to-right scan per company. Once a qualifying
 * window is found, we advance past its last transaction before looking
 * for the next cluster. This prevents the same event appearing as
 * multiple overlapping windows.
 *
 * Within each window, insiders are deduplicated by lower-cased name so
 * the same person (with slightly different capitalisation) never inflates
 * the distinct-insider count.
 */
export async function getClusterSignals(lookbackDays = 90): Promise<ClusterSignal[]> {
  const supabase = getSupabase();
  if (!supabase) return [];

  const since = new Date();
  since.setDate(since.getDate() - lookbackDays);
  const sinceStr = since.toISOString().slice(0, 10);

  const { data, error } = await supabase
    .from("transactions")
    .select(
      "company_id, transaction_date, quantity, total_value, transaction_type," +
      " companies(id, name), insiders(full_name, role)"
    )
    .eq("direction", "buy")
    .gte("transaction_date", sinceStr)
    .order("company_id")
    .order("transaction_date");

  if (error || !data) {
    console.error("getClusterSignals error:", error?.message);
    return [];
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type Row = Record<string, any>;
  // Exclude non-cash transactions — grants and option exercises are corporate
  // decisions, not discretionary purchases, so they don't belong in the signal feed.
  const rows = (data as Row[]).filter(
    (r) => !NON_CASH_TYPES.has((r.transaction_type as string | null) ?? "")
  );

  // Group rows by company
  const byCompany = new Map<number, Row[]>();
  for (const row of rows) {
    const cid = row.company_id as number;
    if (!byCompany.has(cid)) byCompany.set(cid, []);
    byCompany.get(cid)!.push(row);
  }

  const signals: ClusterSignal[] = [];

  for (const [cid, companyRows] of byCompany) {
    const sorted = [...companyRows].sort(
      (a, b) => (a.transaction_date as string).localeCompare(b.transaction_date as string)
    );
    const companyInfo = sorted[0].companies as unknown as { id: number; name: string } | null;
    const companyName = companyInfo?.name ?? String(cid);

    let i = 0;
    while (i < sorted.length) {
      const startDate = sorted[i].transaction_date as string;
      const startMs = new Date(startDate).getTime();

      // Collect all transactions within 7 days of window start
      let j = i;
      while (j < sorted.length) {
        const jMs = new Date(sorted[j].transaction_date as string).getTime();
        if (jMs - startMs <= 7 * 86400_000) j++;
        else break;
      }
      const windowTxs = sorted.slice(i, j);

      // Deduplicate insiders by normalised (lowercased) name.
      // Multiple transactions by the same person are merged into one row.
      const insiderMap = new Map<string, ClusterInsider>();
      for (const tx of windowTxs) {
        const ins = tx.insiders as unknown as { full_name: string; role: string | null } | null;
        const rawName = ins?.full_name ?? "Unknown";
        const key = rawName.toLowerCase().trim();
        if (!insiderMap.has(key)) {
          insiderMap.set(key, {
            name: rawName,
            role: ins?.role ?? null,
            date: tx.transaction_date as string,
            quantity: tx.quantity as number,
            total_value: tx.total_value as number,
            transaction_type: (tx as { transaction_type?: string | null }).transaction_type ?? null,
          });
        } else {
          const existing = insiderMap.get(key)!;
          insiderMap.set(key, {
            ...existing,
            quantity: existing.quantity + (tx.quantity as number),
            total_value: existing.total_value + (tx.total_value as number),
          });
        }
      }

      if (insiderMap.size >= 2) {
        const endDate = sorted[j - 1].transaction_date as string;
        const insiderList = Array.from(insiderMap.values());
        const totalValue = windowTxs.reduce((s, t) => s + (t.total_value as number), 0);
        const cashValue = windowTxs.reduce((s, t) => {
          const tt = (t as { transaction_type?: string | null }).transaction_type;
          return NON_CASH_TYPES.has(tt ?? "") ? s : s + (t.total_value as number);
        }, 0);

        signals.push({
          company_id: cid,
          company_name: companyName,
          window_start: startDate,
          window_end: endDate,
          insiders: insiderList,
          total_value: totalValue,
          cash_value: cashValue,
        });

        // Advance past the entire window so we don't create overlapping clusters
        i = j;
      } else {
        i++;
      }
    }
  }

  // Drop clusters where we couldn't determine any value — these are parsing
  // failures (price/qty = 0 but not classified as grants) and not real signals.
  const meaningful = signals.filter((s) => s.total_value > 0);

  // Most recent clusters first
  meaningful.sort((a, b) => b.window_end.localeCompare(a.window_end));
  return meaningful;
}
