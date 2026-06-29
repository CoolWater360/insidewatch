import Link from "next/link";
import { getIssuerBrowserRows, type IssuerBrowserRow } from "@/lib/signal-view";

export const dynamic = "force-dynamic";

interface Props {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function str(v: string | string[] | undefined): string | undefined {
  if (!v) return undefined;
  return Array.isArray(v) ? v[0] : v;
}

const ISSUER_STATUS_LABELS: Record<string, string> = {
  active:         "Attivo",
  delisted:       "Delistato",
  suspended:      "Sospeso",
  pending_review: "In revisione",
};

const ISSUER_STATUS_COLORS: Record<string, string> = {
  active:         "text-buy border-buy/25 bg-buy/10",
  delisted:       "text-muted border-white/10 bg-white/5",
  suspended:      "text-sell border-sell/25 bg-sell/10",
  pending_review: "text-signal border-signal/25 bg-signal/10",
};

function StatusBadge({ status }: { status: string }) {
  const cls = ISSUER_STATUS_COLORS[status] ?? "text-muted border-white/10 bg-white/5";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${cls}`}>
      {ISSUER_STATUS_LABELS[status] ?? status}
    </span>
  );
}

function IssuerRow({ row }: { row: IssuerBrowserRow }) {
  return (
    <tr className="border-b border-white/[0.05] align-middle transition-colors hover:bg-white/[0.03]">
      <td className="px-3 py-2.5">
        <Link
          href={`/internal/issuers/${row.id}`}
          className="text-xs font-medium text-[#E8EDF7] hover:text-brand-blue hover:underline"
        >
          {row.canonical_name}
        </Link>
        {row.short_name && row.short_name !== row.canonical_name && (
          <span className="ml-1.5 text-[10px] text-muted/60">({row.short_name})</span>
        )}
      </td>
      <td className="px-3 py-2.5 font-mono text-[11px] text-muted">
        {row.isin ?? <span className="text-muted/30">—</span>}
      </td>
      <td className="px-3 py-2.5 font-mono text-[11px] text-muted">
        {row.lei ? (
          <span title={row.lei}>{row.lei.slice(0, 8)}…</span>
        ) : (
          <span className="text-muted/30">—</span>
        )}
      </td>
      <td className="px-3 py-2.5 text-xs text-muted">{row.market ?? "—"}</td>
      <td className="px-3 py-2.5 text-xs text-muted">{row.sector ?? "—"}</td>
      <td className="px-3 py-2.5">
        <StatusBadge status={row.status} />
      </td>
      <td className="px-3 py-2.5 text-[11px] text-muted">{row.country}</td>
      <td className="px-3 py-2.5 text-[11px] tabular-nums text-muted">
        {new Date(row.created_at).toLocaleDateString("it-IT", { day: "2-digit", month: "short", year: "numeric" })}
      </td>
    </tr>
  );
}

function buildUrl(page: number, opts: { q?: string }): string {
  const sp = new URLSearchParams();
  if (page > 1) sp.set("page", String(page));
  if (opts.q) sp.set("q", opts.q);
  const qs = sp.toString();
  return qs ? `/internal/issuers?${qs}` : "/internal/issuers";
}

export default async function IssuersPage({ searchParams }: Props) {
  const sp = await searchParams;
  const page = Math.max(1, parseInt(str(sp.page) ?? "1", 10) || 1);
  const q    = str(sp.q)?.trim() || undefined;

  const result = await getIssuerBrowserRows(page, 50, q);
  const hasFilters = !!q;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[#E8EDF7]">Emittenti</h1>
          <p className="mt-0.5 text-xs text-muted">Registro degli emittenti con dati ISIN, mercato e stato</p>
        </div>
        <span className="text-sm text-muted">
          {result.total.toLocaleString("it-IT")} emittenti
        </span>
      </div>

      {/* Filters */}
      <form method="GET" className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wide text-muted/60">Cerca emittente</label>
          <input
            name="q"
            type="search"
            defaultValue={q ?? ""}
            placeholder="Nome emittente…"
            className="w-56 rounded border border-white/[0.12] bg-navy-900/60 px-2 py-1.5 text-xs text-[#E8EDF7] placeholder:text-muted/40 focus:border-brand-blue/50 focus:outline-none"
          />
        </div>
        <button
          type="submit"
          className="rounded bg-navy-700 px-3 py-1.5 text-xs text-[#E8EDF7] transition-colors hover:bg-navy-600"
        >
          Cerca
        </button>
        {hasFilters && (
          <a
            href="/internal/issuers"
            className="text-xs text-muted/50 transition-colors hover:text-muted"
          >
            Azzera filtri
          </a>
        )}
      </form>

      {result.queryError && (
        <div className="rounded border border-red-900/50 bg-red-950/30 px-3 py-2 font-mono text-xs text-red-300">
          Errore query: {result.queryError}
        </div>
      )}

      {result.rows.length === 0 ? (
        <div className="rounded-lg border border-white/10 bg-navy-900/50 p-10 text-center text-sm text-muted">
          {result.total === 0
            ? "Nessun emittente registrato nel sistema."
            : "Nessun emittente corrisponde alla ricerca."}
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-white/10">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-[10px] uppercase tracking-wide text-muted">
                  <th className="px-3 py-2">Emittente</th>
                  <th className="px-3 py-2">ISIN</th>
                  <th className="px-3 py-2">LEI</th>
                  <th className="px-3 py-2">Mercato</th>
                  <th className="px-3 py-2">Settore</th>
                  <th className="px-3 py-2">Stato</th>
                  <th className="px-3 py-2">Paese</th>
                  <th className="px-3 py-2">Registrato</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row) => (
                  <IssuerRow key={row.id} row={row} />
                ))}
              </tbody>
            </table>
          </div>

          {result.totalPages > 1 && (
            <div className="flex items-center justify-between text-sm text-muted">
              <span>
                Pagina {result.page} di {result.totalPages}
              </span>
              <div className="flex gap-2">
                {result.page > 1 && (
                  <a
                    href={buildUrl(result.page - 1, { q })}
                    className="rounded px-3 py-1 transition-colors hover:bg-white/10 hover:text-[#E8EDF7]"
                  >
                    ← Precedente
                  </a>
                )}
                {result.page < result.totalPages && (
                  <a
                    href={buildUrl(result.page + 1, { q })}
                    className="rounded px-3 py-1 transition-colors hover:bg-white/10 hover:text-[#E8EDF7]"
                  >
                    Successiva →
                  </a>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
