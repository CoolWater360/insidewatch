import Link from "next/link";
import { getReviewQueueCounts } from "@/lib/review";
import { getFilingStatusCounts, getLastActivity, getRecentProcessingRuns } from "@/lib/ops-view";
import { getSignalListRows } from "@/lib/signal-view";
import { getTransactionQualityStats } from "@/lib/quality-view";
import { isSupabaseConfigured } from "@/lib/supabase";

export const dynamic = "force-dynamic";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtTs(ts: string | null) {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" });
}

function fmtSyncTime(ts: string | null) {
  if (!ts) return null;
  const d = new Date(ts);
  return d.toLocaleTimeString("it-IT", { timeZone: "UTC", hour: "2-digit", minute: "2-digit", second: "2-digit" }) + " UTC";
}

function fmtEur(n: number) {
  if (n === 0) return "—";
  if (n >= 1_000_000) return `€${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `€${(n / 1_000).toFixed(0)}K`;
  return `€${n}`;
}

// ─── KPI card ─────────────────────────────────────────────────────────────────

function KpiCard({
  label,
  value,
  sub,
  color = "text-[#E8EDF7]",
  badge,
  href,
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
  badge?: { text: string; cls: string };
  href?: string;
}) {
  const inner = (
    <div className="rounded-lg border border-white/[0.07] bg-gradient-card px-4 py-3.5 transition-colors hover:border-white/[0.12]">
      <div className="flex items-start justify-between gap-2">
        <p className={`text-[28px] font-bold leading-none tabular-nums ${color}`}>{value}</p>
        {badge && (
          <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${badge.cls}`}>
            {badge.text}
          </span>
        )}
      </div>
      <p className="mt-2 text-[10px] font-medium uppercase tracking-wider text-muted/70">{label}</p>
      {sub && <p className="mt-0.5 text-[10px] text-muted/50">{sub}</p>}
    </div>
  );

  return href ? <Link href={href}>{inner}</Link> : inner;
}

// ─── Confidence bar ────────────────────────────────────────────────────────────

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? "bg-buy" : pct >= 50 ? "bg-signal" : "bg-muted/40";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1 w-16 overflow-hidden rounded-full bg-white/[0.07]">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[11px] tabular-nums text-muted">{(value).toFixed(2)}</span>
    </div>
  );
}

// ─── Review workload row ──────────────────────────────────────────────────────

function WorkloadRow({
  label,
  count,
  href,
  urgent,
}: {
  label: string;
  count: number;
  href: string;
  urgent?: boolean;
}) {
  return (
    <Link href={href} className="flex items-center justify-between gap-2 rounded px-2 py-1.5 transition-colors hover:bg-white/[0.04]">
      <span className="text-[12px] text-muted">{label}</span>
      <span className={`min-w-[28px] rounded px-1.5 py-0.5 text-center text-[11px] font-semibold tabular-nums ${
        count > 0 && urgent
          ? "bg-sell/15 text-sell"
          : count > 0
          ? "bg-signal/15 text-signal"
          : "bg-white/[0.04] text-muted/40"
      }`}>
        {count}
      </span>
    </Link>
  );
}

// ─── Health row ───────────────────────────────────────────────────────────────

function HealthRow({
  label,
  sub,
  ok,
}: {
  label: string;
  sub?: string;
  ok: boolean | null;
}) {
  return (
    <div className="flex items-center justify-between gap-2 py-1.5">
      <div className="min-w-0">
        <p className="text-[12px] text-muted">{label}</p>
        {sub && <p className="text-[10px] text-muted/40">{sub}</p>}
      </div>
      {ok === null ? (
        <span className="text-[10px] text-muted/30">—</span>
      ) : (
        <span className={`flex items-center gap-1 text-[10px] font-semibold ${ok ? "text-buy" : "text-sell"}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-buy" : "bg-sell"}`} />
          {ok ? "OK" : "ERRORE"}
        </span>
      )}
    </div>
  );
}

// ─── Direction badge ─────────────────────────────────────────────────────────

function RunStatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    completed:   "bg-brand-emerald/15 text-brand-emerald",
    failed:      "bg-sell/15 text-sell",
    skipped:     "bg-muted/10 text-muted/50",
    pending:     "bg-signal/15 text-signal",
    in_progress: "bg-brand-blue/15 text-brand-blue",
  };
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${map[status] ?? "text-muted"}`}>
      {status}
    </span>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default async function InternalDashboard() {
  if (!isSupabaseConfigured) {
    return (
      <div className="rounded-lg border border-white/10 bg-navy-900/50 p-8 text-center text-muted">
        Supabase non configurato. Impostare SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY.
      </div>
    );
  }

  const [queueCounts, statusCounts, lastActivity, recentRuns, signals, txStats] =
    await Promise.all([
      getReviewQueueCounts(),
      getFilingStatusCounts(),
      getLastActivity(),
      getRecentProcessingRuns(5),
      getSignalListRows(90).catch(() => []),
      getTransactionQualityStats(),
    ]);

  const syncTime = fmtSyncTime(lastActivity.last_run_at);

  const totalReviewQueue =
    queueCounts.pendingTransactions +
    queueCounts.failedFilings +
    queueCounts.skippedFilings +
    queueCounts.pendingIssuers;

  const lowConfidenceCount = Math.max(
    0,
    queueCounts.pendingTransactions -
      txStats.unknown_direction_count -
      txStats.unknown_type_count
  );

  return (
    <div className="space-y-5">

      {/* ── Page header ──────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-[18px] font-medium text-[#E8EDF7]">Dashboard Operativa Centrale</h1>
          <p className="mt-0.5 text-xs text-muted/70">
            Overview analitica e monitoraggio, dati in tempo reale
          </p>
        </div>
        {syncTime && (
          <p className="text-[10px] tabular-nums text-muted/40">
            SYNC: {syncTime}
          </p>
        )}
      </div>

      {/* ── KPI strip ────────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-2.5 sm:grid-cols-4 lg:grid-cols-8">
        <KpiCard
          label="Nuovi Filing"
          value={statusCounts.total}
          sub="totali nel sistema"
          href="/internal/filings"
        />
        <KpiCard
          label="Transazioni"
          value={txStats.total_transactions.toLocaleString("it-IT")}
          sub="transazioni inserite"
          href="/internal/transactions"
        />
        <KpiCard
          label="Segnali Attivi"
          value={signals.length}
          sub="ultimi 90 giorni"
          color={signals.length > 0 ? "text-signal" : "text-[#E8EDF7]"}
          href="/internal/signals"
        />
        <KpiCard
          label="In Revisione"
          value={queueCounts.pendingTransactions}
          sub="needs_review"
          color={queueCounts.pendingTransactions > 0 ? "text-signal" : "text-[#E8EDF7]"}
          badge={queueCounts.pendingTransactions > 50 ? { text: "Elevato", cls: "bg-signal/20 text-signal" } : undefined}
          href="/internal/review"
        />
        <KpiCard
          label="Filing Completati"
          value={statusCounts.completed}
          sub={`${statusCounts.total > 0 ? Math.round((statusCounts.completed / statusCounts.total) * 100) : 0}% del totale`}
          color="text-buy"
        />
        <KpiCard
          label="Filing Falliti"
          value={statusCounts.failed}
          sub={statusCounts.retry_eligible > 0 ? `${statusCounts.retry_eligible} retry eligibili` : "nessun retry"}
          color={statusCounts.failed > 0 ? "text-sell" : "text-[#E8EDF7]"}
          badge={statusCounts.failed > 0 ? { text: "Errore", cls: "bg-sell/20 text-sell" } : undefined}
          href="/internal/operations"
        />
        <KpiCard
          label="Filing Saltati"
          value={statusCounts.skipped}
          sub="max retry raggiunto"
          color={statusCounts.skipped > 0 ? "text-signal" : "text-[#E8EDF7]"}
          href="/internal/operations"
        />
        <KpiCard
          label="Entità Non Risolte"
          value={queueCounts.pendingIssuers}
          sub="unmatched_issuers"
          color={queueCounts.pendingIssuers > 0 ? "text-signal" : "text-[#E8EDF7]"}
          href="/internal/issuers"
        />
      </div>

      {/* ── Main content grid ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_240px]">

        {/* ── Left column ────────────────────────────────────────────────────── */}
        <div className="space-y-4">

          {/* Priority Signals */}
          <div className="rounded-lg border border-white/[0.07] bg-navy-800">
            <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-3">
              <h2 className="text-[13px] font-semibold text-[#E8EDF7]">Priority Signal Signals</h2>
              <Link href="/internal/signals" className="text-[11px] text-brand-blue hover:underline">
                Vedi tutti →
              </Link>
            </div>

            {signals.length === 0 ? (
              <div className="px-4 py-8 text-center">
                <p className="text-[13px] text-muted/60">Nessun segnale attivo negli ultimi 90 giorni.</p>
                <p className="mt-1 text-[11px] text-muted/35">I segnali vengono generati da acquisti coordinati di insider.</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-white/[0.05] text-left text-[11px] font-medium uppercase tracking-wider text-muted/60">
                      <th className="px-4 pb-2 pt-3">Data</th>
                      <th className="px-4 pb-2 pt-3">Emittente</th>
                      <th className="px-4 pb-2 pt-3">Tipo Segnale</th>
                      <th className="px-4 pb-2 pt-3 text-right">Soggetti</th>
                      <th className="px-4 pb-2 pt-3 text-right">Valore Agg.</th>
                      <th className="px-4 pb-2 pt-3">Signal Score</th>
                      <th className="px-4 pb-2 pt-3 w-8" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04]">
                    {signals.slice(0, 5).map((sig) => (
                      <tr key={sig.slug} className="group transition-colors hover:bg-navy-700/50">
                        <td className="px-4 py-2.5 tabular-nums text-[12px] text-muted">
                          {sig.window_start}
                        </td>
                        <td className="px-4 py-2.5">
                          <Link
                            href={`/internal/signals/${sig.slug}`}
                            className="text-[13px] font-medium text-[#E8EDF7] hover:text-brand-blue"
                          >
                            {sig.company_name}
                          </Link>
                        </td>
                        <td className="px-4 py-2.5 text-[12px] text-muted">
                          {sig.signal_type}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-[12px] text-muted">
                          {sig.insider_count}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-[12px] text-[#E8EDF7]">
                          {fmtEur(sig.total_value)}
                        </td>
                        <td className="px-4 py-2.5">
                          <ConfidenceBar value={sig.confidence} />
                        </td>
                        <td className="px-4 py-2.5">
                          <Link
                            href={`/internal/signals/${sig.slug}`}
                            className="flex h-6 w-6 items-center justify-center rounded text-muted/40 transition-colors hover:bg-white/[0.06] hover:text-brand-blue"
                          >
                            →
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Recent Activity / Live Feed */}
          <div className="rounded-lg border border-white/[0.07] bg-navy-800">
            <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-3">
              <h2 className="text-[13px] font-semibold text-[#E8EDF7]">Attività Recente — Live Feed</h2>
              <Link href="/internal/operations" className="text-[11px] text-brand-blue hover:underline">
                Operazioni →
              </Link>
            </div>

            {recentRuns.length === 0 ? (
              <div className="px-4 py-6 text-center">
                <p className="text-[12px] text-muted/50">Nessuna esecuzione pipeline registrata.</p>
              </div>
            ) : (
              <div className="divide-y divide-white/[0.04]">
                {recentRuns.map((run) => (
                  <div key={run.id} className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-navy-700/30">
                    <div className="w-28 shrink-0 tabular-nums text-[11px] text-muted/60">
                      {fmtTs(run.run_at)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[12px] text-[#E8EDF7]">
                        {run.company_name ?? <span className="text-muted/40">Filing #{run.filing_id}</span>}
                      </p>
                      <p className="text-[10px] text-muted/50">
                        {run.transactions_found} trovate · {run.transactions_inserted} inserite
                        {run.transactions_versioned > 0 ? ` · ${run.transactions_versioned} versionate` : ""}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {run.parser_version && (
                        <span className="font-mono text-[10px] text-muted/40">{run.parser_version}</span>
                      )}
                      <RunStatusBadge status="completed" />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Issuer Context (planned modules) */}
          <div className="rounded-lg border border-white/[0.07] bg-navy-800">
            <div className="border-b border-white/[0.07] px-4 py-3">
              <h2 className="text-[13px] font-semibold text-[#E8EDF7]">Contesto Emittenti</h2>
            </div>
            <div className="grid grid-cols-1 gap-0 divide-y divide-white/[0.05] sm:grid-cols-3 sm:divide-x sm:divide-y-0">
              {[
                { label: "Assetti Proprietari", href: "/internal/ownership", note: "Fonte non ancora integrata" },
                { label: "Stato Buyback",        href: "/internal/buybacks",  note: "Dati storici in normalizzazione" },
                { label: "Governance & Eventi",  href: "/internal/governance",note: "Copertura parziale" },
              ].map((item) => (
                <Link
                  key={item.label}
                  href={item.href}
                  className="flex flex-col gap-1 px-4 py-3 transition-colors hover:bg-navy-700/30"
                >
                  <p className="text-[12px] font-medium text-muted/60">{item.label}</p>
                  <p className="text-[11px] text-muted/35">{item.note}</p>
                  <span className="mt-1 inline-flex w-fit rounded border border-white/[0.07] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-muted/30">
                    Presto
                  </span>
                </Link>
              ))}
            </div>
          </div>
        </div>

        {/* ── Right column ───────────────────────────────────────────────────── */}
        <div className="space-y-4">

          {/* Review Workload */}
          <div className="rounded-lg border border-white/[0.07] bg-navy-800">
            <div className="border-b border-white/[0.07] px-4 py-3">
              <div className="flex items-center justify-between">
                <h2 className="text-[13px] font-semibold text-[#E8EDF7]">Review Workload</h2>
                <Link href="/internal/review" className="text-[11px] text-brand-blue hover:underline">
                  Apri →
                </Link>
              </div>
            </div>
            <div className="px-2 py-2">
              <WorkloadRow label="Direzione sconosciuta"  count={txStats.unknown_direction_count} href="/internal/review" urgent />
              <WorkloadRow label="Tipo sconosciuto"       count={txStats.unknown_type_count}      href="/internal/review" urgent />
              <WorkloadRow label="Bassa confidenza"       count={lowConfidenceCount}              href="/internal/review" />
              <WorkloadRow label="Emittenti non risolti"  count={queueCounts.pendingIssuers}      href="/internal/issuers" />
              <WorkloadRow label="Filing falliti"         count={queueCounts.failedFilings}       href="/internal/operations" urgent />
              <WorkloadRow label="Filing saltati"         count={queueCounts.skippedFilings}      href="/internal/operations" />
            </div>
            <div className="border-t border-white/[0.06] px-4 py-2.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-muted/60">Total Pending</span>
                <span className={`text-[14px] font-bold tabular-nums ${totalReviewQueue > 0 ? "text-signal" : "text-buy"}`}>
                  {totalReviewQueue}
                </span>
              </div>
            </div>
          </div>

          {/* Pipeline Health */}
          <div className="rounded-lg border border-white/[0.07] bg-navy-800">
            <div className="border-b border-white/[0.07] px-4 py-3">
              <h2 className="text-[13px] font-semibold text-[#E8EDF7]">Pipeline Health</h2>
            </div>
            <div className="px-4 py-2 divide-y divide-white/[0.05]">
              <HealthRow label="Filing Pipeline" sub={`${statusCounts.completed} completati / ${statusCounts.total} totali`} ok={statusCounts.failed === 0} />
              <HealthRow label="Parser Engine"   sub={lastActivity.last_run_parser_version ? `v${lastActivity.last_run_parser_version}` : "Nessun run registrato"} ok={lastActivity.last_run_at !== null} />
              <HealthRow label="Storage Fonte"   sub="Fonte non ancora integrata" ok={null} />
              <HealthRow label="Listener Worker" sub="Fonte non ancora integrata" ok={null} />
            </div>
            <div className="border-t border-white/[0.06] px-4 py-2.5">
              <p className="text-[10px] text-muted/40">
                Ultima esecuzione:{" "}
                <span className="text-muted/60">{fmtTs(lastActivity.last_run_at)}</span>
              </p>
              {statusCounts.in_progress > 0 && (
                <p className="mt-0.5 text-[10px] text-brand-blue">
                  {statusCounts.in_progress} filing in elaborazione
                </p>
              )}
            </div>
          </div>

          {/* Quick nav */}
          <div className="rounded-lg border border-white/[0.07] bg-navy-800">
            <div className="border-b border-white/[0.07] px-4 py-3">
              <h2 className="text-[13px] font-semibold text-[#E8EDF7]">Accesso Rapido</h2>
            </div>
            <div className="grid grid-cols-2 gap-px bg-white/[0.05]">
              {[
                { label: "Filing",       href: "/internal/filings",      note: "Documenti" },
                { label: "Emittenti",    href: "/internal/issuers",      note: "Registro" },
                { label: "Qualità",      href: "/internal/quality",      note: "Parser" },
                { label: "Operazioni",   href: "/internal/operations",   note: "Pipeline" },
              ].map((item) => (
                <Link key={item.label} href={item.href} className="flex flex-col bg-navy-800 px-3 py-2.5 transition-colors hover:bg-navy-700/50">
                  <p className="text-[12px] font-medium text-[#E8EDF7]">{item.label}</p>
                  <p className="text-[10px] text-muted/50">{item.note}</p>
                </Link>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
