import Link from "next/link";
import {
  getQualityReviewSummary,
  getReviewCategoryBreakdown,
  getTransactionQualityStats,
  getParserVersionQuality,
  getRecentQualityReviews,
  qualityOutcomeLabel,
  reviewCategoryLabel,
  classifyReviewHealth,
} from "@/lib/quality-view";
import { getReviewQueueCounts } from "@/lib/review";

export const dynamic = "force-dynamic";

function fmtTs(ts: string | null) {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" });
}

function pct(n: number, total: number): string {
  if (total === 0) return "—";
  return `${Math.round((n / total) * 100)}%`;
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
  href,
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
  href?: string;
}) {
  const valueEl = <p className={`text-2xl font-bold tabular-nums ${color}`}>{value}</p>;
  return (
    <div className="rounded-lg border border-white/[0.07] bg-navy-900/50 px-4 py-3">
      {href ? <Link href={href} className="hover:underline">{valueEl}</Link> : valueEl}
      <p className="mt-0.5 text-[11px] font-medium text-[#E8EDF7]">{label}</p>
      {sub && <p className="mt-0.5 text-[10px] text-muted/60">{sub}</p>}
    </div>
  );
}

const OUTCOME_COLORS: Record<string, string> = {
  confirmed: "text-buy",
  corrected: "text-signal",
  rejected:  "text-sell",
};

export default async function DataQualityPage() {
  const [reviewSummary, categoryBreakdown, txStats, parserVersions, recentReviews, queueCounts] =
    await Promise.all([
      getQualityReviewSummary(),
      getReviewCategoryBreakdown(),
      getTransactionQualityStats(),
      getParserVersionQuality(),
      getRecentQualityReviews(20),
      getReviewQueueCounts(),
    ]);

  const health = classifyReviewHealth(reviewSummary);
  const unknownDirRate = pct(txStats.unknown_direction_count, txStats.total_transactions);
  const unknownTypeRate = pct(txStats.unknown_type_count, txStats.total_transactions);

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-[#E8EDF7]">Qualità Dati</h1>
          <p className="mt-0.5 text-xs text-muted">
            Monitoraggio · Validazione · Calibrazione parser
          </p>
        </div>
        {health !== "none" && (
          <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${
            health === "ok"
              ? "border-buy/30 bg-buy/10 text-buy"
              : "border-signal/30 bg-signal/10 text-signal"
          }`}>
            {health === "ok" ? "Qualità operativa" : "Attenzione richiesta"}
          </span>
        )}
      </div>

      {/* ── Scorecards: review outcomes ──────────────────────────────────────── */}
      <div>
        <SectionHeader title="Esiti revisione umana" />
        {reviewSummary.total === 0 ? (
          <div className="rounded-lg border border-white/[0.07] bg-navy-900/40 px-4 py-6 text-center text-xs text-muted">
            Nessuna revisione umana registrata. Le revisioni vengono create tramite la pipeline di qualità offline.
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <MetricCell label="Revisioni totali" value={reviewSummary.total} />
            <MetricCell
              label="Confermati"
              value={reviewSummary.confirmed}
              sub={pct(reviewSummary.confirmed, reviewSummary.total)}
              color="text-buy"
            />
            <MetricCell
              label="Corretti"
              value={reviewSummary.corrected}
              sub={pct(reviewSummary.corrected, reviewSummary.total)}
              color="text-signal"
            />
            <MetricCell
              label="Rifiutati"
              value={reviewSummary.rejected}
              sub={pct(reviewSummary.rejected, reviewSummary.total)}
              color="text-sell"
            />
            <MetricCell
              label="Fixture pendenti"
              value={reviewSummary.fixture_eligible_pending}
              sub="eligible ma non ancora create"
              color={reviewSummary.fixture_eligible_pending > 0 ? "text-signal" : "text-muted/60"}
            />
          </div>
        )}
      </div>

      {/* ── Scorecards: transaction anomalies ────────────────────────────────── */}
      <div>
        <SectionHeader
          title="Anomalie transazioni"
          subtitle={`Su ${txStats.total_transactions.toLocaleString("it-IT")} transazioni totali`}
        />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricCell
            label="Direzione sconosciuta"
            value={txStats.unknown_direction_count}
            sub={`${unknownDirRate} del totale`}
            color={txStats.unknown_direction_count > 0 ? "text-signal" : "text-buy"}
            href="/internal/review"
          />
          <MetricCell
            label="Tipo sconosciuto"
            value={txStats.unknown_type_count}
            sub={`${unknownTypeRate} del totale`}
            color={txStats.unknown_type_count > 0 ? "text-signal" : "text-buy"}
            href="/internal/review"
          />
          <MetricCell
            label="In coda revisione"
            value={queueCounts.pendingTransactions}
            sub="needs_review attivi"
            color={queueCounts.pendingTransactions > 0 ? "text-signal" : "text-buy"}
            href="/internal/review"
          />
          <MetricCell
            label="Emittenti non abbinati"
            value={queueCounts.pendingIssuers}
            sub="unmatched_issuers pending"
            color={queueCounts.pendingIssuers > 0 ? "text-signal" : "text-buy"}
            href="/internal/review"
          />
        </div>
      </div>

      {/* ── Scorecards: filing + fixture ─────────────────────────────────────── */}
      <div>
        <SectionHeader title="Filing e fixture" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <MetricCell
            label="Filing falliti"
            value={queueCounts.failedFilings}
            sub="eleggibili per retry"
            color={queueCounts.failedFilings > 0 ? "text-sell" : "text-buy"}
            href="/internal/operations"
          />
          <MetricCell
            label="Filing saltati"
            value={queueCounts.skippedFilings}
            sub="max retry raggiunto"
            color={queueCounts.skippedFilings > 0 ? "text-signal" : "text-buy"}
            href="/internal/operations"
          />
          <MetricCell
            label="Fixture regressione"
            value={reviewSummary.fixture_eligible_pending > 0
              ? reviewSummary.fixture_eligible_pending
              : "—"}
            sub="Workflow: pipeline qualità offline"
            color="text-muted/60"
          />
        </div>
      </div>

      {/* ── Recent reviews table ──────────────────────────────────────────────── */}
      <Card>
        <SectionHeader
          title="Azioni revisione recenti"
          subtitle="Ultime 20 revisioni umane dalla tabella quality_reviews"
        />
        {recentReviews.length === 0 ? (
          <p className="text-xs text-muted">
            Nessuna revisione umana trovata. Le revisioni vengono registrate dalla pipeline di qualità
            offline tramite <code className="text-[#E8EDF7]">sample_review_queue</code> e{" "}
            <code className="text-[#E8EDF7]">quality_eval</code>.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/[0.07] text-left text-[10px] uppercase tracking-wide text-muted/60">
                  <th className="pb-2 pr-3">ID Rev.</th>
                  <th className="pb-2 pr-3">TX</th>
                  <th className="pb-2 pr-3">Categoria</th>
                  <th className="pb-2 pr-3">Esito</th>
                  <th className="pb-2 pr-3">Parser</th>
                  <th className="pb-2 pr-3">Data</th>
                  <th className="pb-2">Note</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {recentReviews.map((r) => (
                  <tr key={r.id} className="hover:bg-white/[0.02]">
                    <td className="py-1.5 pr-3 font-mono text-muted/60">#{r.id}</td>
                    <td className="py-1.5 pr-3">
                      <Link href="/internal/review" className="font-mono text-brand-blue hover:underline">
                        #{r.transaction_id}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-3 text-muted">{reviewCategoryLabel(r.review_category)}</td>
                    <td className={`py-1.5 pr-3 font-semibold ${OUTCOME_COLORS[r.outcome] ?? "text-muted"}`}>
                      {qualityOutcomeLabel(r.outcome)}
                    </td>
                    <td className="py-1.5 pr-3 font-mono text-[10px] text-muted/70">
                      {r.parser_version ?? "—"}
                    </td>
                    <td className="py-1.5 pr-3 tabular-nums text-muted">{fmtTs(r.reviewed_at)}</td>
                    <td
                      className="max-w-[180px] truncate py-1.5 text-muted/60"
                      title={r.correction_notes ?? undefined}
                    >
                      {r.correction_notes ?? <span className="text-muted/30">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Error taxonomy ────────────────────────────────────────────────────── */}
      <Card>
        <SectionHeader
          title="Tassonomia errori (quality_reviews)"
          subtitle="Distribuzione per categoria di revisione"
        />
        {categoryBreakdown.length === 0 ? (
          <p className="text-xs text-muted">Nessuna revisione categorizzata disponibile.</p>
        ) : (
          <div className="space-y-1.5">
            {categoryBreakdown.map((row) => {
              const max = Math.max(...categoryBreakdown.map((r) => r.count));
              const barWidth = Math.round((row.count / max) * 100);
              return (
                <div key={row.category} className="flex items-center gap-3">
                  <div className="w-44 flex-shrink-0 text-[11px] text-muted">{row.label}</div>
                  <div className="flex flex-1 items-center gap-2">
                    <div
                      className="h-1.5 rounded-full bg-brand-blue/50"
                      style={{ width: `${barWidth}%` }}
                    />
                    <span className="text-[11px] tabular-nums text-[#E8EDF7]">{row.count}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        <p className="mt-3 text-[10px] text-muted/40">
          Conteggi basati su revisioni umane in quality_reviews. La tassonomia degli errori attivi
          nella coda di revisione si trova nella{" "}
          <Link href="/internal/review" className="text-brand-blue hover:underline">
            Coda di Revisione
          </Link>.
        </p>
      </Card>

      {/* ── Parser version quality ────────────────────────────────────────────── */}
      <Card>
        <SectionHeader
          title="Qualità per versione parser"
          subtitle="Distribuzione transazioni e revisioni per versione del parser"
        />
        {parserVersions.length === 0 ? (
          <p className="text-xs text-muted">
            Nessuna versione parser distinta trovata nelle transazioni.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/[0.07] text-left text-[10px] uppercase tracking-wide text-muted/60">
                  <th className="pb-2 pr-4">Versione Parser</th>
                  <th className="pb-2 pr-4 text-right">Transazioni</th>
                  <th className="pb-2 pr-4 text-right">Revisioni</th>
                  <th className="pb-2 pr-4 text-right">Corretti</th>
                  <th className="pb-2 text-right">Tasso correzione</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {parserVersions.map((v) => (
                  <tr key={v.parser_version} className="hover:bg-white/[0.02]">
                    <td className="py-1.5 pr-4 font-mono text-[11px] text-[#E8EDF7]">
                      {v.parser_version}
                    </td>
                    <td className="py-1.5 pr-4 text-right tabular-nums text-muted">
                      {v.tx_count.toLocaleString("it-IT")}
                    </td>
                    <td className="py-1.5 pr-4 text-right tabular-nums text-muted">
                      {v.review_count}
                    </td>
                    <td className={`py-1.5 pr-4 text-right tabular-nums ${v.corrected_count > 0 ? "text-signal" : "text-muted/40"}`}>
                      {v.corrected_count}
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-muted">
                      {v.review_count > 0
                        ? pct(v.corrected_count, v.review_count)
                        : <span className="text-muted/30">dato non disponibile</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Fixture pipeline status ───────────────────────────────────────────── */}
      <Card>
        <SectionHeader title="Stato fixture di regressione" />
        <div className="space-y-2">
          <div className="flex items-center justify-between rounded-lg border border-brand-blue/20 bg-brand-blue/5 px-3 py-2.5">
            <div>
              <p className="text-xs font-medium text-brand-blue">
                Fixture disponibili via pipeline qualità
              </p>
              <p className="mt-0.5 text-[11px] text-muted/60">
                Workflow:{" "}
                <code>sample_review_queue --mark-fixture</code> →{" "}
                <code>create_regression_fixture</code>
              </p>
            </div>
            <span className="text-[10px] font-semibold uppercase tracking-wide text-brand-blue/70">
              Offline
            </span>
          </div>
          {reviewSummary.fixture_eligible_pending > 0 && (
            <p className="text-[11px] text-signal">
              {reviewSummary.fixture_eligible_pending} revisione/i marcata/e come eligible ma fixture
              non ancora creata — eseguire <code>create_regression_fixture</code> dalla pipeline.
            </p>
          )}
          <p className="text-[10px] text-muted/40">
            L&apos;integrazione UI del fixture runner è in sviluppo. I conteggi (fixture_eligible,
            fixture_created) sono live dalla tabella quality_reviews.
          </p>
        </div>
      </Card>
    </div>
  );
}
