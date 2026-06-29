import Link from "next/link";
import { getSignalListRows, type SignalListRow } from "@/lib/signal-view";
import { isSupabaseConfigured } from "@/lib/supabase";
import { ConfigNotice } from "@/components/ConfigNotice";
import { formatCurrency } from "@/lib/format";

export const dynamic = "force-dynamic";

interface Props {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function str(v: string | string[] | undefined): string | undefined {
  if (!v) return undefined;
  return Array.isArray(v) ? v[0] : v;
}

function ConfidenceBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const barColor =
    pct >= 80 ? "bg-buy"
    : pct >= 60 ? "bg-signal"
    : "bg-sell";
  const textColor =
    pct >= 80 ? "text-buy"
    : pct >= 60 ? "text-signal"
    : "text-sell";
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-white/10">
        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`tabular-nums text-[11px] font-semibold ${textColor}`}>{pct}%</span>
    </div>
  );
}

function SignalTypeBadge({ type }: { type: string }) {
  return (
    <span className="inline-flex items-center rounded-full border border-buy/25 bg-buy/10 px-2 py-0.5 text-[11px] font-medium text-buy">
      {type}
    </span>
  );
}

function fmtDate(d: string) {
  return new Date(d).toLocaleDateString("it-IT", { day: "2-digit", month: "short" });
}

function SignalRow({ row }: { row: SignalListRow }) {
  return (
    <tr className="border-b border-white/[0.05] align-middle transition-colors hover:bg-white/[0.03]">
      <td className="px-3 py-2.5">
        <Link
          href={`/internal/signals/${row.slug}`}
          className="text-xs font-medium text-[#E8EDF7] hover:text-brand-blue hover:underline"
        >
          {row.company_name}
        </Link>
      </td>
      <td className="px-3 py-2.5">
        <SignalTypeBadge type={row.signal_type} />
        {row.confidence_caveat && (
          <span className="ml-1.5 inline-flex items-center rounded-full border border-signal/25 bg-signal/10 px-1.5 py-0.5 text-[10px] font-medium text-signal">
            Contesto insufficiente
          </span>
        )}
      </td>
      <td className="px-3 py-2.5 text-[11px] tabular-nums text-muted">
        {fmtDate(row.window_start)}–{fmtDate(row.window_end)}
      </td>
      <td className="px-3 py-2.5 text-[11px] tabular-nums text-muted">
        {new Date(row.window_end).toLocaleDateString("it-IT", {
          day: "2-digit", month: "short", year: "numeric",
        })}
      </td>
      <td className="px-3 py-2.5 text-center">
        <span className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-white/10 px-1 text-[11px] font-semibold text-[#E8EDF7]">
          {row.insider_count}
        </span>
      </td>
      <td className="px-3 py-2.5 text-right text-[11px] tabular-nums text-[#E8EDF7]">
        {formatCurrency(row.total_value)}
      </td>
      <td className="px-3 py-2.5">
        <ConfidenceBar score={row.confidence} />
      </td>
    </tr>
  );
}

export default async function SignalsPage({ searchParams }: Props) {
  if (!isSupabaseConfigured) return <ConfigNotice />;

  const sp = await searchParams;
  const q              = str(sp.q)?.trim() || undefined;
  const minInsiders    = str(sp.min_insiders) || "2";
  const confidenceFilter = str(sp.confidence) || "all";

  const allRows = await getSignalListRows(90);

  // In-memory filters — signal list is typically < 100 rows
  let rows = allRows;
  if (q) {
    const ql = q.toLowerCase();
    rows = rows.filter((r) => r.company_name.toLowerCase().includes(ql));
  }
  const minN = parseInt(minInsiders, 10) || 2;
  if (minN > 2) rows = rows.filter((r) => r.insider_count >= minN);
  if (confidenceFilter === "high")   rows = rows.filter((r) => r.confidence >= 0.80);
  if (confidenceFilter === "medium") rows = rows.filter((r) => r.confidence >= 0.60 && r.confidence < 0.80);
  if (confidenceFilter === "low")    rows = rows.filter((r) => r.confidence < 0.60);

  const hasFilters = !!(q || minInsiders !== "2" || confidenceFilter !== "all");

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[#E8EDF7]">Segnali & Contesto</h1>
          <p className="mt-0.5 text-xs text-muted">
            Acquisti coordinati da 2+ insider nella stessa società in 7 giorni · ultimi 90 giorni
          </p>
        </div>
        <span className="text-sm text-muted">
          {rows.length}{allRows.length !== rows.length && ` / ${allRows.length}`} segnali
        </span>
      </div>

      {/* Filters */}
      <form method="GET" className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wide text-muted/60">Emittente</label>
          <input
            name="q"
            type="search"
            defaultValue={q ?? ""}
            placeholder="Nome società…"
            className="w-44 rounded border border-white/[0.12] bg-navy-900/60 px-2 py-1.5 text-xs text-[#E8EDF7] placeholder:text-muted/40 focus:border-brand-blue/50 focus:outline-none"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wide text-muted/60">Soggetti min.</label>
          <select
            name="min_insiders"
            defaultValue={minInsiders}
            className="rounded border border-white/[0.12] bg-navy-900/60 px-2 py-1.5 text-xs text-[#E8EDF7] focus:border-brand-blue/50 focus:outline-none"
          >
            <option value="2">≥ 2</option>
            <option value="3">≥ 3</option>
            <option value="4">≥ 4</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wide text-muted/60">Fiducia</label>
          <select
            name="confidence"
            defaultValue={confidenceFilter}
            className="rounded border border-white/[0.12] bg-navy-900/60 px-2 py-1.5 text-xs text-[#E8EDF7] focus:border-brand-blue/50 focus:outline-none"
          >
            <option value="all">Tutte</option>
            <option value="high">Alta (≥ 80%)</option>
            <option value="medium">Media (60–79%)</option>
            <option value="low">Bassa (&lt; 60%)</option>
          </select>
        </div>
        <button
          type="submit"
          className="rounded bg-navy-700 px-3 py-1.5 text-xs text-[#E8EDF7] transition-colors hover:bg-navy-600"
        >
          Applica
        </button>
        {hasFilters && (
          <a
            href="/internal/signals"
            className="text-xs text-muted/50 transition-colors hover:text-muted"
          >
            Azzera filtri
          </a>
        )}
      </form>

      {rows.length === 0 ? (
        <div className="rounded-lg border border-white/10 bg-navy-900/50 p-10 text-center text-sm text-muted">
          {allRows.length === 0
            ? "Nessun segnale di acquisto coordinato nei dati correnti."
            : "Nessun segnale corrisponde ai filtri selezionati."}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-white/10">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 text-left text-[10px] uppercase tracking-wide text-muted">
                <th className="px-3 py-2">Emittente</th>
                <th className="px-3 py-2">Tipo Segnale</th>
                <th className="px-3 py-2">Finestra</th>
                <th className="px-3 py-2">Data Segnale</th>
                <th className="px-3 py-2 text-center">Soggetti</th>
                <th className="px-3 py-2 text-right">Val. Aggregato EUR</th>
                <th className="px-3 py-2">Fiducia</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <SignalRow key={row.slug} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[10px] text-muted/40">
        Confidence score: base 50% · +10% per insider aggiuntivo (max +30%) · +15% per 2+ dirigenti ·
        +10% per valore &gt; €100k. Segnali con fiducia &lt; 60% mostrano il caveat &quot;Contesto insufficiente&quot;.
      </p>
    </div>
  );
}
