import Link from "next/link";
import {
  getFilings,
  FILING_STATUS_LABELS,
  type FilingFilters,
  type FilingRow,
} from "@/lib/filings";
import { RetryFilingButton } from "@/components/internal/RetryFilingButton";

export const dynamic = "force-dynamic";

interface Props {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function str(v: string | string[] | undefined): string | undefined {
  if (!v) return undefined;
  return Array.isArray(v) ? v[0] : v;
}

const STATUS_OPTIONS = [
  { value: "",                label: "Tutti gli stati"        },
  { value: "pending",         label: "In attesa"              },
  { value: "in_progress",     label: "In corso"               },
  { value: "completed",       label: "Completato"             },
  { value: "failed",          label: "Fallito"                },
  { value: "skipped",         label: "Saltato"                },
  { value: "retry_requested", label: "Riesecuzione richiesta" },
  { value: "superseded",      label: "Superato"               },
];

function StatusBadge({ status }: { status: string }) {
  const colours: Record<string, string> = {
    completed:        "bg-emerald-900/50 text-emerald-300",
    failed:           "bg-red-900/50 text-red-300",
    skipped:          "bg-orange-900/50 text-orange-300",
    in_progress:      "bg-blue-900/50 text-blue-300",
    pending:          "bg-white/10 text-muted",
    retry_requested:  "bg-amber-900/50 text-amber-300",
    superseded:       "bg-white/5 text-muted/40",
  };
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${colours[status] ?? "bg-white/10 text-muted"}`}>
      {FILING_STATUS_LABELS[status] ?? status}
    </span>
  );
}

function fmtTs(ts: string | null): string {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" });
}

function TruncUrl({ url }: { url: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="block max-w-[160px] truncate font-mono text-[10px] text-brand-blue hover:underline"
      title={url}
    >
      {url}
    </a>
  );
}

function FilingRow({ filing }: { filing: FilingRow }) {
  return (
    <tr className="border-b border-white/5 align-top transition-colors hover:bg-white/[0.03]">
      <td className="px-3 py-2.5">
        <Link
          href={`/internal/filings/${filing.id}`}
          className="font-mono text-xs text-brand-blue hover:underline"
        >
          #{filing.id}
        </Link>
      </td>
      <td className="px-3 py-2.5 text-xs text-[#E8EDF7]">
        {filing.company_name ?? <span className="text-muted">—</span>}
      </td>
      <td className="px-3 py-2.5">
        {filing.pdf_url ? <TruncUrl url={filing.pdf_url} /> : <span className="text-muted text-xs">—</span>}
      </td>
      <td className="px-3 py-2.5 text-[11px] tabular-nums text-muted">
        {fmtTs(filing.source_published_utc) !== "—"
          ? fmtTs(filing.source_published_utc)
          : filing.filing_date ?? "—"}
      </td>
      <td className="px-3 py-2.5 text-[11px] tabular-nums text-muted">
        {fmtTs(filing.discovered_utc) !== "—"
          ? fmtTs(filing.discovered_utc)
          : fmtTs(filing.first_seen_at)}
      </td>
      <td className="px-3 py-2.5">
        <StatusBadge status={filing.status} />
      </td>
      <td className="px-3 py-2.5 text-[11px] tabular-nums text-muted">
        {filing.attempt_count}/{filing.max_attempts}
      </td>
      <td className="px-3 py-2.5">
        <span className="font-mono text-[10px] text-muted">{filing.scraper_version ?? "—"}</span>
      </td>
      <td className="px-3 py-2.5 text-[11px] tabular-nums text-muted">
        {filing.transactions_inserted}
      </td>
      <td className="px-3 py-2.5 text-center">
        {filing.has_stored_document ? (
          <span className="text-[11px] text-brand-emerald">✓</span>
        ) : (
          <span className="text-[11px] text-muted/30">—</span>
        )}
      </td>
      <td className="px-3 py-2.5">
        {filing.last_error ? (
          <div
            className="max-w-[150px] overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[10px] text-red-400"
            title={filing.last_error}
          >
            {filing.last_error.slice(0, 70)}
            {filing.last_error.length > 70 && "…"}
          </div>
        ) : (
          <span className="text-muted/30 text-xs">—</span>
        )}
      </td>
      <td className="px-3 py-2.5">
        <RetryFilingButton filingId={filing.id} status={filing.status} />
      </td>
    </tr>
  );
}

function buildUrl(
  page: number,
  opts: { status?: string; q?: string; onlyFailed?: boolean; noStorage?: boolean }
): string {
  const sp = new URLSearchParams();
  if (page > 1) sp.set("page", String(page));
  if (opts.status) sp.set("status", opts.status);
  if (opts.q) sp.set("q", opts.q);
  if (opts.onlyFailed) sp.set("only_failed", "1");
  if (opts.noStorage) sp.set("no_storage", "1");
  const qs = sp.toString();
  return qs ? `/internal/filings?${qs}` : "/internal/filings";
}

export default async function FilingsPage({ searchParams }: Props) {
  const sp = await searchParams;
  const page = Math.max(1, parseInt(str(sp.page) ?? "1", 10) || 1);
  const status = str(sp.status) || undefined;
  const q = str(sp.q) || undefined;
  const onlyFailed = str(sp.only_failed) === "1";
  const noStorage = str(sp.no_storage) === "1";

  const filters: FilingFilters = {
    status,
    q,
    only_failed_skipped: onlyFailed,
    no_storage: noStorage,
  };

  const result = await getFilings(page, 50, filters);
  const hasFilters = !!(status || q || onlyFailed || noStorage);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-[#E8EDF7]">Filing</h1>
        <span className="text-sm text-muted">
          {result.total.toLocaleString("it-IT")} totali
        </span>
      </div>

      {/* Filters */}
      <form method="GET" className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wide text-muted/60">Stato</label>
          <select
            name="status"
            defaultValue={status ?? ""}
            className="rounded border border-white/[0.12] bg-navy-900/60 px-2 py-1.5 text-xs text-[#E8EDF7] focus:border-brand-blue/50 focus:outline-none"
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wide text-muted/60">Cerca</label>
          <input
            name="q"
            type="search"
            defaultValue={q ?? ""}
            placeholder="Emittente o URL fonte…"
            className="w-52 rounded border border-white/[0.12] bg-navy-900/60 px-2 py-1.5 text-xs text-[#E8EDF7] placeholder:text-muted/40 focus:border-brand-blue/50 focus:outline-none"
          />
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
          <input
            name="only_failed"
            type="checkbox"
            value="1"
            defaultChecked={onlyFailed}
            className="accent-brand-blue"
          />
          Solo falliti / saltati
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
          <input
            name="no_storage"
            type="checkbox"
            value="1"
            defaultChecked={noStorage}
            className="accent-brand-blue"
          />
          Storage non verificato
        </label>
        <button
          type="submit"
          className="rounded bg-navy-700 px-3 py-1.5 text-xs text-[#E8EDF7] transition-colors hover:bg-navy-600"
        >
          Applica
        </button>
        {hasFilters && (
          <a
            href="/internal/filings"
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
          Nessun filing corrisponde ai filtri selezionati.
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-white/10">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-[10px] uppercase tracking-wide text-muted">
                  <th className="px-3 py-2">ID</th>
                  <th className="px-3 py-2">Emittente</th>
                  <th className="px-3 py-2">Fonte</th>
                  <th className="px-3 py-2">Pubblicato</th>
                  <th className="px-3 py-2">Scoperto</th>
                  <th className="px-3 py-2">Stato</th>
                  <th className="px-3 py-2">Tent.</th>
                  <th className="px-3 py-2">Parser</th>
                  <th className="px-3 py-2">Tx</th>
                  <th className="px-3 py-2">Storage</th>
                  <th className="px-3 py-2">Ultimo errore</th>
                  <th className="px-3 py-2">Azioni</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((filing) => (
                  <FilingRow key={filing.id} filing={filing} />
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
                    href={buildUrl(result.page - 1, { status, q, onlyFailed, noStorage })}
                    className="rounded px-3 py-1 transition-colors hover:bg-white/10 hover:text-[#E8EDF7]"
                  >
                    ← Precedente
                  </a>
                )}
                {result.page < result.totalPages && (
                  <a
                    href={buildUrl(result.page + 1, { status, q, onlyFailed, noStorage })}
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
