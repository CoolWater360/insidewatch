/**
 * Phase 12 — meaningful transaction intelligence signals.
 *
 * All signals exclude mechanical events (grants, option exercises, sell-to-cover,
 * conversions, transfers, etc.) and operate only on discretionary cash transactions.
 *
 * Signals are designed for internal operational use only.  They carry explicit
 * confidence scores and rationale strings so every signal is auditable and
 * no output is a black-box recommendation.
 *
 * Discretionary = economic_intent = 'discretionary' AND direction IN ('buy','sell')
 * Mechanical exclusion = same as NON_CASH_TYPES in queries.ts plus sell_to_cover,
 *                        conversion, inheritance, gift_*, transfer_*, pledge_or_security,
 *                        derivative_transaction.
 */

import { getSupabase } from "./supabase";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface NetDiscretionaryFlow {
  company_id: number;
  company_name: string;
  buy_value: number;
  sell_value: number;
  net_value: number;          // buy_value - sell_value (positive = net buyer)
  transaction_count: number;
  lookback_days: number;
}

export interface RepeatBuyerSignal {
  insider_id: number;
  insider_name: string;
  role: string | null;
  role_category: string | null;
  company_id: number;
  company_name: string;
  buy_count: number;
  total_buy_value: number;
  first_buy: string;
  last_buy: string;
  lookback_days: number;
}

export interface ClusterInsiderWithRole {
  name: string;
  role: string | null;
  role_category: string | null;
  date: string;
  quantity: number;
  total_value: number;
  transaction_type: string | null;
}

export interface ClusterSignalWithConfidence {
  company_id: number;
  company_name: string;
  window_start: string;
  window_end: string;
  insiders: ClusterInsiderWithRole[];
  insider_count: number;
  total_value: number;
  cash_value: number;
  confidence: number;          // 0.0–1.0; see scoreClusterConfidence()
  rationale: string[];         // ordered list of factors that determined confidence
}

// ─── Constants ────────────────────────────────────────────────────────────────

// All non-discretionary transaction types that should be excluded from signals.
export const MECHANICAL_TYPES = new Set([
  "grant", "option_exercise", "sell_to_cover",
  "conversion", "inheritance",
  "gift_in", "gift_out",
  "transfer_in", "transfer_out",
  "pledge_or_security", "derivative_transaction",
]);

// Roles considered executive/board-level for confidence boosting.
const SENIOR_ROLES = new Set([
  "ceo", "cfo", "coo", "cto", "chairman", "board_member",
  "executive_director", "managing_director", "president",
  "direttore_generale", "amministratore_delegato", "consigliere",
  "presidente",
]);

// ─── Confidence scoring ───────────────────────────────────────────────────────

/**
 * Score confidence for a cluster signal.
 *
 * Base: 2 insiders = 0.50.
 * Boosts (cumulative, capped at 1.0):
 *   +0.10 per additional insider beyond 2  (max +0.30 at 5 insiders)
 *   +0.15 if 2+ are executive / board level
 *   +0.10 if cash_value > 100 000 EUR
 */
export function scoreClusterConfidence(
  insiderCount: number,
  seniorInsiderCount: number,
  cashValue: number
): { confidence: number; rationale: string[] } {
  const rationale: string[] = [];
  let score = 0.50;
  rationale.push(`base: ${insiderCount} independent insiders`);

  const extraInsiders = Math.min(insiderCount - 2, 3);
  if (extraInsiders > 0) {
    score += extraInsiders * 0.10;
    rationale.push(`+${(extraInsiders * 0.10).toFixed(2)} for ${extraInsiders} additional insider(s)`);
  }

  if (seniorInsiderCount >= 2) {
    score += 0.15;
    rationale.push(`+0.15 for ${seniorInsiderCount} executive/board insiders`);
  }

  if (cashValue > 100_000) {
    score += 0.10;
    rationale.push(`+0.10 for cash_value > €100 000 (${cashValue.toFixed(0)})`);
  }

  return {
    confidence: Math.round(Math.min(1.0, score) * 1000) / 1000,
    rationale,
  };
}

function _isSeniorRole(role: string | null, roleCategory: string | null): boolean {
  if (!role && !roleCategory) return false;
  const r = (role ?? "").toLowerCase().replace(/\s+/g, "_");
  const rc = (roleCategory ?? "").toLowerCase();
  return SENIOR_ROLES.has(r) || SENIOR_ROLES.has(rc)
    || rc === "executive" || rc === "board";
}

// ─── Signal queries ───────────────────────────────────────────────────────────

/**
 * Net discretionary buying vs selling per company over the lookback window.
 *
 * Only includes transactions with economic_intent='discretionary' and
 * direction in ('buy','sell').  Mechanical events are excluded.
 *
 * Returns companies sorted by net_value descending (biggest net buyers first).
 */
export async function getNetDiscretionaryFlow(
  lookbackDays = 90
): Promise<NetDiscretionaryFlow[]> {
  const supabase = getSupabase();
  if (!supabase) return [];

  const since = new Date();
  since.setDate(since.getDate() - lookbackDays);
  const sinceStr = since.toISOString().slice(0, 10);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type Row = Record<string, any>;

  const { data, error } = await supabase
    .from("transactions")
    .select(
      "company_id, direction, total_value, transaction_type, economic_intent," +
      " companies(id, name)"
    )
    .eq("economic_intent", "discretionary")
    .in("direction", ["buy", "sell"])
    .gte("transaction_date", sinceStr);

  if (error || !data) {
    console.error("getNetDiscretionaryFlow error:", error?.message);
    return [];
  }

  const rows = (data as Row[]).filter(
    (r) => !MECHANICAL_TYPES.has(r.transaction_type ?? "")
  );

  const companyMap = new Map<number, {
    company_name: string;
    buy_value: number;
    sell_value: number;
    count: number;
  }>();

  for (const row of rows) {
    const cid = row.company_id as number;
    const name = (row.companies as { name: string } | null)?.name ?? String(cid);
    if (!companyMap.has(cid)) {
      companyMap.set(cid, { company_name: name, buy_value: 0, sell_value: 0, count: 0 });
    }
    const entry = companyMap.get(cid)!;
    const val = (row.total_value as number) || 0;
    if (row.direction === "buy") entry.buy_value += val;
    else entry.sell_value += val;
    entry.count++;
  }

  const result: NetDiscretionaryFlow[] = [];
  for (const [cid, e] of companyMap) {
    result.push({
      company_id: cid,
      company_name: e.company_name,
      buy_value: Math.round(e.buy_value),
      sell_value: Math.round(e.sell_value),
      net_value: Math.round(e.buy_value - e.sell_value),
      transaction_count: e.count,
      lookback_days: lookbackDays,
    });
  }

  result.sort((a, b) => b.net_value - a.net_value);
  return result;
}

/**
 * Repeat discretionary buying by a single insider at the same company.
 *
 * Returns insiders who have made at least `minBuys` discretionary cash
 * purchases at the same company within the lookback window.
 *
 * Results are sorted by buy_count descending, then total_buy_value descending.
 * Only insiders with identifiable role information are included.
 */
export async function getRepeatBuyerSignals(
  lookbackDays = 90,
  minBuys = 2
): Promise<RepeatBuyerSignal[]> {
  const supabase = getSupabase();
  if (!supabase) return [];

  const since = new Date();
  since.setDate(since.getDate() - lookbackDays);
  const sinceStr = since.toISOString().slice(0, 10);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type Row = Record<string, any>;

  const { data, error } = await supabase
    .from("transactions")
    .select(
      "insider_id, company_id, transaction_date, total_value, transaction_type, economic_intent," +
      " insiders(id, full_name, role, role_category)," +
      " companies(id, name)"
    )
    .eq("direction", "buy")
    .eq("economic_intent", "discretionary")
    .gte("transaction_date", sinceStr)
    .order("transaction_date");

  if (error || !data) {
    console.error("getRepeatBuyerSignals error:", error?.message);
    return [];
  }

  const rows = (data as Row[]).filter(
    (r) => !MECHANICAL_TYPES.has(r.transaction_type ?? "")
  );

  // Key: insider_id + company_id
  const key = (r: Row) => `${r.insider_id}:${r.company_id}`;
  const grouped = new Map<string, {
    insider_id: number; company_id: number;
    name: string; role: string | null; role_category: string | null;
    company_name: string;
    dates: string[]; values: number[];
  }>();

  for (const row of rows) {
    const k = key(row);
    const ins = row.insiders as { id: number; full_name: string; role: string | null; role_category: string | null } | null;
    const comp = row.companies as { id: number; name: string } | null;
    if (!grouped.has(k)) {
      grouped.set(k, {
        insider_id: row.insider_id as number,
        company_id: row.company_id as number,
        name: ins?.full_name ?? "Unknown",
        role: ins?.role ?? null,
        role_category: ins?.role_category ?? null,
        company_name: comp?.name ?? String(row.company_id),
        dates: [],
        values: [],
      });
    }
    const g = grouped.get(k)!;
    g.dates.push(row.transaction_date as string);
    g.values.push((row.total_value as number) || 0);
  }

  const result: RepeatBuyerSignal[] = [];
  for (const g of grouped.values()) {
    if (g.dates.length < minBuys) continue;
    result.push({
      insider_id:      g.insider_id,
      insider_name:    g.name,
      role:            g.role,
      role_category:   g.role_category,
      company_id:      g.company_id,
      company_name:    g.company_name,
      buy_count:       g.dates.length,
      total_buy_value: Math.round(g.values.reduce((s, v) => s + v, 0)),
      first_buy:       g.dates[0],
      last_buy:        g.dates[g.dates.length - 1],
      lookback_days:   lookbackDays,
    });
  }

  result.sort((a, b) =>
    b.buy_count - a.buy_count || b.total_buy_value - a.total_buy_value
  );
  return result;
}

/**
 * Cluster-buying signals with explicit confidence scores and rationale.
 *
 * Extends getClusterSignals() from queries.ts with:
 *  - Role information per insider (for seniority boost)
 *  - Confidence score (0.0–1.0) from scoreClusterConfidence()
 *  - Rationale string array listing each confidence factor
 *
 * Algorithm: greedy 7-day window scan identical to the original, but
 * operates only on discretionary (non-mechanical) buys.
 */
export async function getClusterSignalsWithConfidence(
  lookbackDays = 90
): Promise<ClusterSignalWithConfidence[]> {
  const supabase = getSupabase();
  if (!supabase) return [];

  const since = new Date();
  since.setDate(since.getDate() - lookbackDays);
  const sinceStr = since.toISOString().slice(0, 10);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type Row = Record<string, any>;

  const { data, error } = await supabase
    .from("transactions")
    .select(
      "company_id, transaction_date, quantity, total_value, transaction_type, economic_intent," +
      " companies(id, name)," +
      " insiders(full_name, role, role_category)"
    )
    .eq("direction", "buy")
    .gte("transaction_date", sinceStr)
    .order("company_id")
    .order("transaction_date");

  if (error || !data) {
    console.error("getClusterSignalsWithConfidence error:", error?.message);
    return [];
  }

  // Exclude all mechanical events.
  const rows = (data as Row[]).filter(
    (r) => !MECHANICAL_TYPES.has((r.transaction_type as string | null) ?? "")
  );

  // Group by company
  const byCompany = new Map<number, Row[]>();
  for (const row of rows) {
    const cid = row.company_id as number;
    if (!byCompany.has(cid)) byCompany.set(cid, []);
    byCompany.get(cid)!.push(row);
  }

  const signals: ClusterSignalWithConfidence[] = [];

  for (const [cid, companyRows] of byCompany) {
    const sorted = [...companyRows].sort(
      (a, b) => (a.transaction_date as string).localeCompare(b.transaction_date as string)
    );
    const companyInfo = sorted[0].companies as { id: number; name: string } | null;
    const companyName = companyInfo?.name ?? String(cid);

    let i = 0;
    while (i < sorted.length) {
      const startDate = sorted[i].transaction_date as string;
      const startMs = new Date(startDate).getTime();

      let j = i;
      while (j < sorted.length) {
        const jMs = new Date(sorted[j].transaction_date as string).getTime();
        if (jMs - startMs <= 7 * 86_400_000) j++;
        else break;
      }
      const windowTxs = sorted.slice(i, j);

      // Deduplicate insiders by normalised name.
      const insiderMap = new Map<string, ClusterInsiderWithRole>();
      for (const tx of windowTxs) {
        const ins = tx.insiders as { full_name: string; role: string | null; role_category: string | null } | null;
        const rawName = ins?.full_name ?? "Unknown";
        const k = rawName.toLowerCase().trim();
        if (!insiderMap.has(k)) {
          insiderMap.set(k, {
            name: rawName,
            role: ins?.role ?? null,
            role_category: ins?.role_category ?? null,
            date: tx.transaction_date as string,
            quantity: tx.quantity as number,
            total_value: tx.total_value as number,
            transaction_type: (tx.transaction_type as string | null) ?? null,
          });
        } else {
          const existing = insiderMap.get(k)!;
          insiderMap.set(k, {
            ...existing,
            quantity: existing.quantity + (tx.quantity as number),
            total_value: existing.total_value + (tx.total_value as number),
          });
        }
      }

      if (insiderMap.size >= 2) {
        const endDate = sorted[j - 1].transaction_date as string;
        const insiderList = Array.from(insiderMap.values());
        const totalValue = windowTxs.reduce((s, t) => s + ((t.total_value as number) || 0), 0);
        const cashValue  = windowTxs.reduce((s, t) => {
          const tt = (t.transaction_type as string | null);
          return MECHANICAL_TYPES.has(tt ?? "") ? s : s + ((t.total_value as number) || 0);
        }, 0);

        const seniorCount = insiderList.filter(
          (ins) => _isSeniorRole(ins.role, ins.role_category)
        ).length;

        const { confidence, rationale } = scoreClusterConfidence(
          insiderMap.size, seniorCount, cashValue
        );

        signals.push({
          company_id:    cid,
          company_name:  companyName,
          window_start:  startDate,
          window_end:    endDate,
          insiders:      insiderList,
          insider_count: insiderMap.size,
          total_value:   Math.round(totalValue),
          cash_value:    Math.round(cashValue),
          confidence,
          rationale,
        });

        i = j;
      } else {
        i++;
      }
    }
  }

  // Drop zero-value clusters (parsing failures, not real signals).
  const meaningful = signals.filter((s) => s.total_value > 0);

  // Most recent first, then highest confidence.
  meaningful.sort((a, b) =>
    b.window_end.localeCompare(a.window_end) || b.confidence - a.confidence
  );
  return meaningful;
}
