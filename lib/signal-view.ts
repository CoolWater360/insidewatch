/**
 * Phase 4 — server-side query helpers for the Signals workspace and Issuer browser.
 *
 * All signal computation uses getSupabaseServer() (service-role key) because the
 * private `transactions` table is not accessible to the anon key. The public
 * lib/signals.ts functions (which use the anon key) are kept for the public
 * /api/v1/signals endpoint and are NOT called from here.
 *
 * Issuer and security data also use getSupabaseServer().
 */

import { unstable_noStore as noStore } from "next/cache";
import {
  scoreClusterConfidence,
  MECHANICAL_TYPES,
  type ClusterSignalWithConfidence,
  type ClusterInsiderWithRole,
} from "./signals";
import { getSupabaseServer } from "./supabase-server";
import type { PaginatedResult } from "./review";

// ─── Server-side cluster signal computation ───────────────────────────────────
// Mirrors getClusterSignalsWithConfidence() from lib/signals.ts but uses the
// service-role client so it can read the private transactions table.

const SENIOR_ROLES_SV = new Set([
  "ceo", "cfo", "coo", "cto", "chairman", "board_member",
  "executive_director", "managing_director", "president",
  "direttore_generale", "amministratore_delegato", "consigliere",
  "presidente",
]);

function _isSeniorRoleSv(role: string | null, roleCategory: string | null): boolean {
  if (!role && !roleCategory) return false;
  const r  = (role ?? "").toLowerCase().replace(/\s+/g, "_");
  const rc = (roleCategory ?? "").toLowerCase();
  return SENIOR_ROLES_SV.has(r) || SENIOR_ROLES_SV.has(rc)
    || rc === "executive" || rc === "board";
}

async function getClusterSignalsServer(
  lookbackDays = 90
): Promise<ClusterSignalWithConfidence[]> {
  noStore();
  const db = getSupabaseServer();

  const since = new Date();
  since.setDate(since.getDate() - lookbackDays);
  const sinceStr = since.toISOString().slice(0, 10);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type Row = Record<string, any>;

  const { data, error } = await db
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
    console.error("getClusterSignalsServer error:", error?.message);
    return [];
  }

  const rows = (data as Row[]).filter(
    (r) => !MECHANICAL_TYPES.has((r.transaction_type as string | null) ?? "")
  );

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
      const startDate  = sorted[i].transaction_date as string;
      const startMs    = new Date(startDate).getTime();

      let j = i;
      while (j < sorted.length) {
        const jMs = new Date(sorted[j].transaction_date as string).getTime();
        if (jMs - startMs <= 7 * 86_400_000) j++;
        else break;
      }
      const windowTxs = sorted.slice(i, j);

      const insiderMap = new Map<string, ClusterInsiderWithRole>();
      for (const tx of windowTxs) {
        const ins = tx.insiders as { full_name: string; role: string | null; role_category: string | null } | null;
        const rawName = ins?.full_name ?? "Unknown";
        const k = rawName.toLowerCase().trim();
        if (!insiderMap.has(k)) {
          insiderMap.set(k, {
            name:             rawName,
            role:             ins?.role ?? null,
            role_category:    ins?.role_category ?? null,
            date:             tx.transaction_date as string,
            quantity:         tx.quantity as number,
            total_value:      tx.total_value as number,
            transaction_type: (tx.transaction_type as string | null) ?? null,
          });
        } else {
          const existing = insiderMap.get(k)!;
          insiderMap.set(k, {
            ...existing,
            quantity:    existing.quantity    + (tx.quantity    as number),
            total_value: existing.total_value + (tx.total_value as number),
          });
        }
      }

      if (insiderMap.size >= 2) {
        const endDate      = sorted[j - 1].transaction_date as string;
        const insiderList  = Array.from(insiderMap.values());
        const totalValue   = windowTxs.reduce((s, t) => s + ((t.total_value as number) || 0), 0);
        const cashValue    = windowTxs.reduce((s, t) => {
          const tt = (t.transaction_type as string | null);
          return MECHANICAL_TYPES.has(tt ?? "") ? s : s + ((t.total_value as number) || 0);
        }, 0);
        const seniorCount  = insiderList.filter(
          (ins) => _isSeniorRoleSv(ins.role, ins.role_category)
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

  const meaningful = signals.filter((s) => s.total_value > 0);
  meaningful.sort((a, b) =>
    b.window_end.localeCompare(a.window_end) || b.confidence - a.confidence
  );
  return meaningful;
}

// ─── Signal list ──────────────────────────────────────────────────────────────

export interface SignalListRow {
  /** URL key: "{company_id}_{window_start}", e.g. "42_2026-01-15" */
  slug: string;
  company_id: number;
  company_name: string;
  signal_type: string;
  window_start: string;
  window_end: string;
  insider_count: number;
  total_value: number;
  confidence: number;
  /** true when confidence < 0.60 — used to render the "Contesto insufficiente" caveat */
  confidence_caveat: boolean;
  rationale: string[];
}

/** Returns the factual Italian label for a given signal. Exported for tests. */
export function classifySignalType(_sig: ClusterSignalWithConfidence): string {
  // Only buy-cluster signals exist in the current data model.
  // Sell-cluster detection is not yet implemented.
  return "Segnale di acquisto coordinato";
}

/** Pure mapping from a raw cluster signal to a list-row DTO. Exported for tests. */
export function mapToSignalListRow(sig: ClusterSignalWithConfidence): SignalListRow {
  return {
    slug:              `${sig.company_id}_${sig.window_start}`,
    company_id:        sig.company_id,
    company_name:      sig.company_name,
    signal_type:       classifySignalType(sig),
    window_start:      sig.window_start,
    window_end:        sig.window_end,
    insider_count:     sig.insider_count,
    total_value:       sig.total_value,
    confidence:        sig.confidence,
    confidence_caveat: sig.confidence < 0.60,
    rationale:         sig.rationale,
  };
}

export async function getSignalListRows(lookbackDays = 90): Promise<SignalListRow[]> {
  const signals = await getClusterSignalsServer(lookbackDays);
  return signals.map(mapToSignalListRow);
}

// ─── Signal detail ────────────────────────────────────────────────────────────

export interface SignalDetailTransaction {
  id: number;
  transaction_date: string;
  direction: string;
  transaction_type: string | null;
  total_value: number;
  currency: string;
  insider_name: string;
  insider_role: string | null;
  source_filing_id: number | null;
  review_status: string | null;
  parser_version: string | null;
}

export interface SignalDetailFiling {
  id: number;
  pdf_url: string;
  pdf_sha256: string | null;
  status: string;
  filing_date: string | null;
}

export interface SignalDetailFull {
  row: SignalListRow;
  insiders: ClusterInsiderWithRole[];
  transactions: SignalDetailTransaction[];
  filings: SignalDetailFiling[];
}

export async function getSignalDetail(
  companyId: number,
  windowStart: string
): Promise<SignalDetailFull | null> {
  const signals = await getClusterSignalsServer(90);
  const sig = signals.find(
    (s) => s.company_id === companyId && s.window_start === windowStart
  );
  if (!sig) return null;

  const db = getSupabaseServer();

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type Row = Record<string, any>;

  const { data: txData, error: txError } = await db
    .from("transactions")
    .select(
      "id, transaction_date, direction, transaction_type, total_value, currency," +
      " review_status, parser_version, source_filing_id," +
      " insiders(full_name, role)"
    )
    .eq("company_id", companyId)
    .eq("direction", "buy")
    .gte("transaction_date", sig.window_start)
    .lte("transaction_date", sig.window_end)
    .order("transaction_date");

  if (txError) {
    console.error("getSignalDetail transactions error:", txError.message);
    return null;
  }

  const transactions: SignalDetailTransaction[] = ((txData ?? []) as unknown as Row[]).map((r) => {
    const ins = r.insiders as { full_name: string; role: string | null } | null;
    return {
      id:               r.id as number,
      transaction_date: r.transaction_date as string,
      direction:        r.direction as string,
      transaction_type: (r.transaction_type as string | null) ?? null,
      total_value:      (r.total_value as number) || 0,
      currency:         (r.currency as string) || "EUR",
      insider_name:     ins?.full_name ?? "—",
      insider_role:     ins?.role ?? null,
      source_filing_id: (r.source_filing_id as number | null) ?? null,
      review_status:    (r.review_status as string | null) ?? null,
      parser_version:   (r.parser_version as string | null) ?? null,
    };
  });

  // Fetch evidence filings for the transactions in this cluster
  const filingIds = [
    ...new Set(
      transactions.map((t) => t.source_filing_id).filter((id): id is number => id != null)
    ),
  ];

  let filings: SignalDetailFiling[] = [];
  if (filingIds.length > 0) {
    const { data: filData } = await db
      .from("filings")
      .select("id, pdf_url, pdf_sha256, status, filing_date")
      .in("id", filingIds)
      .order("filing_date", { ascending: false });

    if (filData) {
      filings = (filData as unknown as Row[]).map((f) => ({
        id:          f.id as number,
        pdf_url:     f.pdf_url as string,
        pdf_sha256:  (f.pdf_sha256 as string | null) ?? null,
        status:      f.status as string,
        filing_date: (f.filing_date as string | null) ?? null,
      }));
    }
  }

  return {
    row:          mapToSignalListRow(sig),
    insiders:     sig.insiders,
    transactions,
    filings,
  };
}

// ─── Issuer browser ───────────────────────────────────────────────────────────

export interface IssuerBrowserRow {
  id: number;
  canonical_name: string;
  short_name: string | null;
  lei: string | null;
  country: string;
  market: string | null;
  sector: string | null;
  status: string;
  /** Primary equity ISIN from the securities table, or null if not yet catalogued. */
  isin: string | null;
  created_at: string;
}

export async function getIssuerBrowserRows(
  page: number,
  pageSize: number,
  q?: string
): Promise<PaginatedResult<IssuerBrowserRow>> {
  const db = getSupabaseServer();
  const from = (page - 1) * pageSize;
  const to   = from + pageSize - 1;

  // eslint-disable-next-line prefer-const
  let query = db
    .from("issuers")
    .select(
      "id, canonical_name, short_name, lei, country, market, sector, status, created_at",
      { count: "exact" }
    );

  if (q) query = query.ilike("canonical_name", `%${q}%`);
  query = query.order("canonical_name").range(from, to);

  const { data, count, error } = await query;
  if (error) {
    console.error("getIssuerBrowserRows error:", error.message);
    return { rows: [], total: 0, page, pageSize, totalPages: 0, queryError: error.message };
  }

  type Raw = Record<string, unknown>;
  const rawRows = (data ?? []) as unknown as Raw[];
  const issuerIds = rawRows.map((r) => r.id as number);

  // Batch-fetch the primary equity ISIN for each issuer
  const isinMap = new Map<number, string>();
  if (issuerIds.length > 0) {
    const { data: secData } = await db
      .from("securities")
      .select("issuer_id, isin")
      .in("issuer_id", issuerIds)
      .eq("instrument_type", "equity")
      .eq("status", "active");

    if (secData) {
      for (const s of (secData as unknown as { issuer_id: number; isin: string }[])) {
        if (!isinMap.has(s.issuer_id)) isinMap.set(s.issuer_id, s.isin);
      }
    }
  }

  const rows: IssuerBrowserRow[] = rawRows.map((r) => ({
    id:             r.id as number,
    canonical_name: r.canonical_name as string,
    short_name:     (r.short_name as string | null) ?? null,
    lei:            (r.lei as string | null) ?? null,
    country:        r.country as string,
    market:         (r.market as string | null) ?? null,
    sector:         (r.sector as string | null) ?? null,
    status:         r.status as string,
    isin:           isinMap.get(r.id as number) ?? null,
    created_at:     r.created_at as string,
  }));

  const total = count ?? 0;
  return { rows, total, page, pageSize, totalPages: Math.ceil(total / pageSize) };
}

// ─── Issuer detail ────────────────────────────────────────────────────────────

export interface IssuerLinkedCompany {
  id: number;
  name: string;
}

export interface IssuerDetailFull {
  id: number;
  canonical_name: string;
  short_name: string | null;
  lei: string | null;
  country: string;
  market: string | null;
  sector: string | null;
  status: string;
  created_at: string;
  isin: string | null;
  companies: IssuerLinkedCompany[];
  /** Count of unmatched_issuers rows pointing at this issuer as a suggestion, still pending. */
  unmatched_pending: number;
}

export async function getIssuerDetail(id: number): Promise<IssuerDetailFull | null> {
  const db = getSupabaseServer();

  const [issuerResult, companiesResult, secResult, unmatchedResult] = await Promise.all([
    db
      .from("issuers")
      .select("id, canonical_name, short_name, lei, country, market, sector, status, created_at")
      .eq("id", id)
      .single(),
    db
      .from("companies")
      .select("id, name")
      .eq("issuer_id", id)
      .order("name"),
    db
      .from("securities")
      .select("isin")
      .eq("issuer_id", id)
      .eq("instrument_type", "equity")
      .eq("status", "active")
      .limit(1),
    db
      .from("unmatched_issuers")
      .select("id", { count: "exact", head: true })
      .eq("suggestion_issuer_id", id)
      .eq("status", "pending"),
  ]);

  if (issuerResult.error || !issuerResult.data) {
    console.error("getIssuerDetail error:", issuerResult.error?.message);
    return null;
  }

  const raw = issuerResult.data as unknown as Record<string, unknown>;
  type SecRow = { isin: string };
  const isin =
    ((secResult.data as unknown as SecRow[] | null)?.[0]?.isin) ?? null;

  return {
    id:                raw.id as number,
    canonical_name:    raw.canonical_name as string,
    short_name:        (raw.short_name as string | null) ?? null,
    lei:               (raw.lei as string | null) ?? null,
    country:           raw.country as string,
    market:            (raw.market as string | null) ?? null,
    sector:            (raw.sector as string | null) ?? null,
    status:            raw.status as string,
    created_at:        raw.created_at as string,
    isin,
    companies:         (companiesResult.data ?? []) as unknown as IssuerLinkedCompany[],
    unmatched_pending: unmatchedResult.count ?? 0,
  };
}
