import Link from "next/link";
import {
  getFilingStatusCounts,
  getRecentProcessingRuns,
  getFailedFilingsSample,
  getPipelineLatencyStats,
  getLastActivity,
  getVersionedTransactionCount,
  filingStatusLabel,
  classifyFilingHealth,
  isRetryEligible,
  formatLatencyHours,
} from "@/lib/ops-view";
import { RetryFilingButton } from "@/components/internal/RetryFilingButton";

export const dynamic = "force-dynamic";

function fmtTs(ts: string | null) {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" });
}

function fmtDate(d: string | null) {
  if (!d) return "—";
  return new Date(d).toLocaleDateString("it-IT", { dateStyle: "short" });
}

function SectionHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-[11px] font-semibold uppercase tracking-widest text-muted/60">{title}</h2>
      {subtitle && <p className="mt-0.5 text-[11px] text-muted/50">{subtitle}</p>}
    </div>
  );
}

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-white/[0.08] bg-navy-900/40 p-4 ${className}`}>
      {children}
    </div>
  );
}

function MetricCell({
  label,
  value,
  sub,
  color = "text-[#E8EDF7]",
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="rounded-lg border border-white/[0.07] bg-navy-900/50 px-4 py-3">
      <p className={`text-2xl font-bold tabular-nums ${color}`}>{value}</p>
      <p className="mt-0.5 text-[11px] font-medium text-[#E8EDF7]">{label}</p>
      {sub && <p className="mt-0.5 text-[10px] text-muted/60">{sub}</p>}
    </div>
  );
}

const STATUS_COLORS: Record<string, string> = {
  pending:     "border-signal/30 bg-signal/10 text-signal",
  in_progress: "border-brand-blue/30 bg-brand-blue/10 text-brand-blue",
  completed:   "border-buy/30 bg-buy/10 text-buy",
  failed:      "border-sell/30 bg-sell/10 text-sell",
  skipped:     "border-muted/20 bg-white/[0.02] text-muted/60",
};

export default async function OperationsPage() {
  const [statusCounts, recentRuns, failedFilings, latency, lastActivity, versionedCount] =
    await Promise.all([
      getFilingStatusCounts(),
      getRecentProcessingRuns(25),
      getFailedFilingsSample(20),
      getPipelineLatencyStats(),
      getLastActivity(),
      getVersionedTransactionCount(),
    ]);

  const health = classifyFilingHealth(statusCounts);

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-[#E8EDF7]">Operazioni</h1>
          <p className="mt-0.5 text-xs text-muted">
            Pipeline · Acquisizione · Elaborazione filing
          </p>
        </div>
        <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${
          health === "ok"
            ? "border-buy/30 bg-buy/10 text-buy"
            : health === "warn"
            ? "border-signal/30 bg-signal/10 text-signal"
            : "border-sell/30 bg-sell/10 text-sell"
        }`}>
          {health === "ok" ? "Pipeline operativa" : health === "warn" ? "Attenzione" : "Errori presenti"}
        </span>
      </div>

      {/* ── Filing status scorecards ──────────────────────────────────────────── */}
      <div>
        <SectionHeader
          title="Stato filing"
          subtitle={`${statusCounts.total.toLocaleString("it-IT")} filing totali`}
        />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricCell
            label="Completati"
            value={statusCounts.completed}
            sub={`${statusCounts.total > 0 ? Math.round((statusCounts.completed / statusCounts.total) * 100) : 0}% del totale`}
            color="text-buy"
          />
          <MetricCell
            label="In attesa"
            value={statusCounts.pending}
            sub="pending elaborazione"
            color={statusCounts.pending > 0 ? "text-signal" : "text-muted/60"}
          />
          <MetricCell
            label="Falliti"
            value={statusCounts.failed}
            sub={statusCounts.retry_eligible > 0 ? `${statusCounts.retry_eligible} eleggibili retry` : "nessun retry disponibile"}
            color={statusCounts.failed > 0 ? "text-sell" : "text-muted/60"}
          />
          <MetricCell
            label="Saltati"
            value={statusCounts.skipped}
            sub="max retry raggiunto"
            color={statusCounts.skipped > 0 ? "text-signal" : "text-muted/60"}
          />
        </div>
      </div>

      {/* ── Last activity + versioning ────────────────────────────────────────── */}
      <div>
        <SectionHeader title="Ultima attività" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <MetricCell
            label="Ultima elaborazione"
            value={lastActivity.last_run_at ? fmtTs(lastActivity.last_run_at) : "—"}
            sub={lastActivity.last_run_parser_version
              ? `Parser ${lastActivity.last_run_parser_version}`
              : "dato non disponibile"}
            color={lastActivity.last_run_at ? "text-[#E8EDF7]" : "text-muted/40"}
          />
          <MetricCell
            label="Transazioni versionate"
            value={versionedCount.toLocaleString("it-IT")}
            sub="storico modifiche in transaction_versions"
            color="text-[#E8EDF7]"
          />
          <MetricCell
            label="In elaborazione ora"
            value={statusCounts.in_progress}
            sub="filing con status in_progress"
            color={statusCounts.in_progress > 0 ? "text-brand-blue" : "text-muted/60"}
          />
        </div>
      </div>

      {/* ── Latency stats ─────────────────────────────────────────────────────── */}
      <Card>
        <SectionHeader
          title="Latenza pipeline"
          subtitle="Tempo scoperta → completamento (ultime 200 elaborazioni completate)"
        />
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <div>
            <p className="text-xl font-bold tabular-nums text-[#E8EDF7]">
              {formatLatencyHours(latency.median_hours)}
            </p>
            <p className="mt-0.5 text-[11px] text-muted">Mediana (P50)</p>
          </div>
          <div>
            <p className="text-xl font-bold tabular-nums text-[#E8EDF7]">
              {formatLatencyHours(latency.p95_hours)}
            </p>
            <p className="mt-0.5 text-[11px] text-muted">P95</p>
          </div>
          <div>
            <p className="text-xl font-bold tabular-nums text-[#E8EDF7]">
              {latency.sample_count}
            </p>
            <p className="mt-0.5 text-[11px] text-muted">Campioni</p>
          </div>
        </div>
        <p className="mt-3 text-[10px] text-muted/40">{latency.note}</p>
      </Card>

      {/* ── Recent processing runs ────────────────────────────────────────────── */}
      <Card>
        <SectionHeader
          title="Elaborazioni recenti"
          subtitle="Ultime 25 esecuzioni del parser da filing_processing_runs"
        />
        {recentRuns.length === 0 ? (
          <p className="text-xs text-muted">
            Nessuna elaborazione registrata in filing_processing_runs.
            Le esecuzioni vengono inserite al termine di ogni run del parser.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/[0.07] text-left text-[10px] uppercase tracking-wide text-muted/60">
                  <th className="pb-2 pr-3">Data run</th>
                  <th className="pb-2 pr-3">Filing</th>
                  <th className="pb-2 pr-3">Emittente</th>
                  <th className="pb-2 pr-3">Parser</th>
                  <th className="pb-2 pr-3 text-right">Trovate</th>
                  <th className="pb-2 pr-3 text-right">Inserite</th>
                  <th className="pb-2 pr-3 text-right">Versionate</th>
                  <th className="pb-2 text-right">Invariate</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {recentRuns.map((r) => (
                  <tr key={r.id} className="hover:bg-white/[0.02]">
                    <td className="py-1.5 pr-3 tabular-nums text-muted">{fmtTs(r.run_at)}</td>
                    <td className="py-1.5 pr-3 font-mono text-[10px] text-muted/70">#{r.filing_id}</td>
                    <td className="max-w-[140px] truncate py-1.5 pr-3 text-[#E8EDF7]">
                      {r.company_name ?? <span className="text-muted/30">—</span>}
                    </td>
                    <td className="py-1.5 pr-3 font-mono text-[10px] text-muted/70">
                      {r.parser_version ?? "—"}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-[#E8EDF7]">
                      {r.transactions_found}
                    </td>
                    <td className={`py-1.5 pr-3 text-right tabular-nums ${r.transactions_inserted > 0 ? "text-buy" : "text-muted/40"}`}>
                      {r.transactions_inserted}
                    </td>
                    <td className={`py-1.5 pr-3 text-right tabular-nums ${r.transactions_versioned > 0 ? "text-signal" : "text-muted/40"}`}>
                      {r.transactions_versioned}
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-muted/60">
                      {r.transactions_unchanged}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Failures & recovery ───────────────────────────────────────────────── */}
      <Card>
        <SectionHeader
          title="Filing falliti e saltati"
          subtitle="Ultimi 20 filing con status failed o skipped — retry disponibile dove eleggibile"
        />
        {failedFilings.length === 0 ? (
          <p className="text-xs text-muted">
            Nessun filing fallito o saltato.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/[0.07] text-left text-[10px] uppercase tracking-wide text-muted/60">
                  <th className="pb-2 pr-3">ID</th>
                  <th className="pb-2 pr-3">Emittente</th>
                  <th className="pb-2 pr-3">Data filing</th>
                  <th className="pb-2 pr-3">Stato</th>
                  <th className="pb-2 pr-3 text-right">Tentativi</th>
                  <th className="pb-2 pr-3">Ultimo tentativo</th>
                  <th className="pb-2 pr-3">Prossimo dopo</th>
                  <th className="pb-2 pr-3">Errore</th>
                  <th className="pb-2">Azione</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {failedFilings.map((f) => {
                  const retryEligible = isRetryEligible(f.status, f.attempt_count, f.max_attempts);
                  return (
                    <tr key={f.id} className="hover:bg-white/[0.02]">
                      <td className="py-1.5 pr-3 font-mono text-[10px] text-muted/70">#{f.id}</td>
                      <td className="max-w-[120px] truncate py-1.5 pr-3 text-[#E8EDF7]">
                        {f.company_name ?? <span className="text-muted/30">—</span>}
                      </td>
                      <td className="py-1.5 pr-3 tabular-nums text-muted">{fmtDate(f.filing_date)}</td>
                      <td className="py-1.5 pr-3">
                        <span className={`rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_COLORS[f.status] ?? "text-muted"}`}>
                          {filingStatusLabel(f.status)}
                        </span>
                      </td>
                      <td className="py-1.5 pr-3 text-right tabular-nums text-muted">
                        {f.attempt_count}/{f.max_attempts}
                      </td>
                      <td className="py-1.5 pr-3 tabular-nums text-muted">{fmtTs(f.last_attempted_at)}</td>
                      <td className="py-1.5 pr-3 tabular-nums text-muted">
                        {f.next_attempt_after ? fmtTs(f.next_attempt_after) : <span className="text-muted/30">—</span>}
                      </td>
                      <td
                        className="max-w-[160px] truncate py-1.5 pr-3 text-[10px] text-muted/60"
                        title={f.last_error ?? undefined}
                      >
                        {f.last_error ?? <span className="text-muted/30">—</span>}
                      </td>
                      <td className="py-1.5">
                        {retryEligible ? (
                          <RetryFilingButton filingId={f.id} status={f.status} />
                        ) : (
                          <span className="text-[10px] text-muted/30">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Operational safeguards ────────────────────────────────────────────── */}
      <Card>
        <SectionHeader title="Salvaguardie operative" />
        <div className="space-y-2 text-[11px] text-muted">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-buy">✓</span>
            <span>La chiave service-role Supabase è usata esclusivamente lato server. Non è mai esposta al browser o al frontend Next.js.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-buy">✓</span>
            <span>Il retry di un filing reimposta lo stato a <code className="text-[#E8EDF7]">pending</code> e incrementa <code className="text-[#E8EDF7]">attempt_count</code>. Il massimo di tentativi (<code className="text-[#E8EDF7]">max_attempts</code>) è fissato per filing.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-buy">✓</span>
            <span>I job di acquisizione (scraper Consob) girano come workflow GitHub Actions su schedule. Nessun cron Vercel è attivo.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-buy">✓</span>
            <span>Il versioning delle transazioni registra ogni modifica in <code className="text-[#E8EDF7]">transaction_versions</code>. Nessuna riga viene sovrascritta senza storico.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-buy">✓</span>
            <span>Il percorso di archiviazione del PDF (<code className="text-[#E8EDF7]">storage_path</code>) non viene mai reso pubblico. Solo la URL firmata lato server è accettabile.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-signal">!</span>
            <span>
              Ricorda di impostare lo <strong>Spend Cap Supabase a €0</strong> in{" "}
              <Link href="https://supabase.com/dashboard/project/_/settings/billing" className="text-brand-blue hover:underline" target="_blank" rel="noopener">
                Settings → Billing
              </Link>{" "}
              per prevenire addebiti imprevisti.
            </span>
          </div>
        </div>
      </Card>
    </div>
  );
}
