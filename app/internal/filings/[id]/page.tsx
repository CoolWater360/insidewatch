import Link from "next/link";
import { notFound } from "next/navigation";
import { getFilingDetail, FILING_STATUS_LABELS, type FilingDetailFull } from "@/lib/filings";
import { RetryFilingButton } from "@/components/internal/RetryFilingButton";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

interface Props {
  params: Promise<{ id: string }>;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtTs(ts: string | null): string {
  if (!ts) return "Dato non disponibile";
  return new Date(ts).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "medium" });
}

function fmtBytes(n: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(2)} MB`;
}

function latencyLabel(from: string | null, to: string | null): string | null {
  if (!from || !to) return null;
  const ms = new Date(to).getTime() - new Date(from).getTime();
  if (ms < 0) return null;
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)} min`;
  return `${(ms / 3600000).toFixed(1)} h`;
}

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
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${colours[status] ?? "bg-white/10 text-muted"}`}>
      {FILING_STATUS_LABELS[status] ?? status}
    </span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-white/10 bg-navy-900/40">
      <div className="border-b border-white/[0.07] px-5 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted/60">{title}</h2>
      </div>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-muted/50">{label}</span>
      <div className="text-sm text-[#E8EDF7]">{children}</div>
    </div>
  );
}

// ─── Lifecycle Timeline ───────────────────────────────────────────────────────

function LifecycleTimeline({ filing }: { filing: FilingDetailFull }) {
  const stages: { label: string; ts: string | null; prev?: string | null }[] = [
    { label: "Pubblicato",  ts: filing.source_published_utc },
    { label: "Scoperto",    ts: filing.discovered_utc,   prev: filing.source_published_utc },
    { label: "Scaricato",   ts: filing.downloaded_utc,   prev: filing.discovered_utc },
    { label: "Archiviato",  ts: filing.stored_utc,       prev: filing.downloaded_utc },
    { label: "Analizzato",  ts: filing.parsed_utc,       prev: filing.stored_utc ?? filing.downloaded_utc },
    { label: "Validato",    ts: filing.validated_utc,    prev: filing.parsed_utc },
    { label: "Consegnato",  ts: filing.delivered_utc,    prev: filing.validated_utc ?? filing.parsed_utc },
    { label: "Completato",  ts: filing.completed_at,     prev: filing.delivered_utc },
  ];

  return (
    <Section title="Ciclo di vita — pipeline di elaborazione">
      <div className="space-y-0">
        {stages.map((stage, i) => {
          const latency = stage.prev != null ? latencyLabel(stage.prev, stage.ts) : null;
          const isLast = i === stages.length - 1;
          return (
            <div key={stage.label} className="flex items-stretch gap-3">
              {/* Connector line */}
              <div className="flex flex-col items-center">
                <div className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                  stage.ts ? "bg-brand-emerald" : "bg-white/20"
                }`} />
                {!isLast && <div className="w-px flex-1 bg-white/[0.07]" />}
              </div>
              {/* Stage info */}
              <div className={`pb-3 ${isLast ? "pb-0" : ""}`}>
                <div className="flex items-baseline gap-3">
                  <span className={`text-xs font-medium ${stage.ts ? "text-[#E8EDF7]" : "text-muted/40"}`}>
                    {stage.label}
                  </span>
                  {latency && (
                    <span className="text-[10px] text-muted/40">+{latency}</span>
                  )}
                </div>
                <div className={`font-mono text-[11px] ${stage.ts ? "text-muted" : "text-muted/30 italic"}`}>
                  {fmtTs(stage.ts)}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

// ─── Transactions Table ───────────────────────────────────────────────────────

const REVIEW_STATUS_LABELS: Record<string, string> = {
  pending_review: "Da rivedere",
  under_review:   "In revisione",
  confirmed:      "Confermato",
  rejected:       "Rifiutato",
  corrected:      "Corretto",
};

const DIRECTION_LABELS: Record<string, string> = {
  buy:     "Acquisto",
  sell:    "Vendita",
  unknown: "Sconosciuto",
};

function directionClass(d: string): string {
  if (d === "buy") return "text-buy";
  if (d === "sell") return "text-sell";
  return "text-muted/60";
}

function TransactionsSection({ filing }: { filing: FilingDetailFull }) {
  const { transactions } = filing;

  return (
    <Section title={`Transazioni estratte — ${transactions.length} righe`}>
      {transactions.length === 0 ? (
        <p className="text-sm text-muted">
          Nessuna transazione estratta da questo filing.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-white/10 text-left text-[10px] uppercase tracking-wide text-muted">
                <th className="pb-2 pr-4">ID</th>
                <th className="pb-2 pr-4">Data</th>
                <th className="pb-2 pr-4">Insider</th>
                <th className="pb-2 pr-4">Direzione</th>
                <th className="pb-2 pr-4">Tipo</th>
                <th className="pb-2 pr-4">Intent</th>
                <th className="pb-2 pr-4">Valore</th>
                <th className="pb-2 pr-4">Revisione</th>
                <th className="pb-2">Parser</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((tx) => (
                <tr key={tx.id} className="border-b border-white/[0.04] align-top">
                  <td className="py-2 pr-4 font-mono text-muted">#{tx.id}</td>
                  <td className="py-2 pr-4 tabular-nums text-muted">
                    {formatDate(tx.transaction_date)}
                  </td>
                  <td className="py-2 pr-4 text-[#E8EDF7]">
                    {tx.insiders?.full_name ?? <span className="text-muted">—</span>}
                  </td>
                  <td className={`py-2 pr-4 font-medium ${directionClass(tx.direction)}`}>
                    {DIRECTION_LABELS[tx.direction] ?? tx.direction}
                  </td>
                  <td className="py-2 pr-4 text-muted">{tx.transaction_type ?? "—"}</td>
                  <td className="py-2 pr-4 text-muted">{tx.economic_intent ?? "—"}</td>
                  <td className="py-2 pr-4 tabular-nums text-[#E8EDF7]">
                    {tx.total_value != null
                      ? new Intl.NumberFormat("it-IT", {
                          style: "currency",
                          currency: tx.currency || "EUR",
                          maximumFractionDigits: 0,
                        }).format(tx.total_value)
                      : "—"}
                  </td>
                  <td className="py-2 pr-4">
                    {tx.review_status ? (
                      <span className="text-muted">
                        {REVIEW_STATUS_LABELS[tx.review_status] ?? tx.review_status}
                      </span>
                    ) : (
                      <span className="text-muted/30">—</span>
                    )}
                  </td>
                  <td className="py-2 font-mono text-muted/60">
                    {tx.parser_version ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// ─── Processing History ───────────────────────────────────────────────────────

function ProcessingHistory({ filing }: { filing: FilingDetailFull }) {
  const { processing_runs } = filing;

  if (processing_runs.length === 0) return null;

  return (
    <Section title="Storico elaborazioni">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-white/10 text-left text-[10px] uppercase tracking-wide text-muted">
              <th className="pb-2 pr-4">Data esecuzione</th>
              <th className="pb-2 pr-4">Parser</th>
              <th className="pb-2 pr-4">Trovate</th>
              <th className="pb-2 pr-4">Inserite</th>
              <th className="pb-2 pr-4">Aggiornate</th>
              <th className="pb-2">Invariate</th>
            </tr>
          </thead>
          <tbody>
            {processing_runs.map((run) => (
              <tr key={run.id} className="border-b border-white/[0.04]">
                <td className="py-2 pr-4 tabular-nums text-muted">
                  {new Date(run.run_at).toLocaleString("it-IT", {
                    dateStyle: "short",
                    timeStyle: "short",
                  })}
                </td>
                <td className="py-2 pr-4 font-mono text-muted">{run.parser_version ?? "—"}</td>
                <td className="py-2 pr-4 tabular-nums text-[#E8EDF7]">{run.transactions_found}</td>
                <td className="py-2 pr-4 tabular-nums text-brand-emerald">{run.transactions_inserted}</td>
                <td className="py-2 pr-4 tabular-nums text-amber-300">{run.transactions_versioned}</td>
                <td className="py-2 tabular-nums text-muted">{run.transactions_unchanged}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

// ─── Error & Recovery ─────────────────────────────────────────────────────────

function ErrorRecoverySection({ filing }: { filing: FilingDetailFull }) {
  const canRetry = filing.status === "failed" || filing.status === "skipped";
  const isSkipped = filing.status === "skipped";

  return (
    <Section title="Errori e recupero">
      <div className="space-y-4">
        {/* Attempt tracking */}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <InfoRow label="Tentativi">
            <span className="tabular-nums">{filing.attempt_count} / {filing.max_attempts}</span>
          </InfoRow>
          <InfoRow label="Ultimo tentativo">
            <span className="tabular-nums text-sm text-muted">
              {filing.last_attempted_at
                ? new Date(filing.last_attempted_at).toLocaleString("it-IT", {
                    dateStyle: "short",
                    timeStyle: "short",
                  })
                : "—"}
            </span>
          </InfoRow>
          {filing.next_attempt_after && (
            <InfoRow label="Prossimo tentativo dopo">
              <span className="tabular-nums text-sm text-muted">
                {new Date(filing.next_attempt_after).toLocaleString("it-IT", {
                  dateStyle: "short",
                  timeStyle: "short",
                })}
              </span>
            </InfoRow>
          )}
          <InfoRow label="Idoneità riesecuzione">
            <span className={`text-sm ${canRetry ? "text-brand-emerald" : "text-muted/50"}`}>
              {canRetry ? "Idoneo" : "Non applicabile"}
            </span>
          </InfoRow>
        </div>

        {/* Stale claim warning */}
        {filing.has_stale_claim && (
          <div className="rounded border border-amber-800/50 bg-amber-950/30 px-3 py-2 text-xs text-amber-300">
            Claim di elaborazione non rilasciato — il worker potrebbe aver terminato in modo anomalo.
            La riesecuzione rimuoverà il claim.
          </div>
        )}

        {/* Skip reason */}
        {isSkipped && filing.last_error && (
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wide text-muted/50">Motivo salto</p>
            <div className="rounded border border-white/10 bg-navy-900/60 px-3 py-2 font-mono text-xs text-orange-300 break-all">
              {filing.last_error}
            </div>
          </div>
        )}

        {/* Last error */}
        {!isSkipped && filing.last_error && (
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wide text-muted/50">Ultimo errore</p>
            <div className="rounded border border-red-900/50 bg-red-950/30 px-3 py-2 font-mono text-xs text-red-300 break-all">
              {filing.last_error}
            </div>
          </div>
        )}

        {/* Disabled reprocessing note */}
        {!canRetry && filing.status !== "pending" && filing.status !== "in_progress" && (
          <p className="text-xs text-muted/40">
            Riesecuzione disponibile solo tramite flusso operativo approvato.
          </p>
        )}
      </div>
    </Section>
  );
}

// ─── Extracted Text Evidence ──────────────────────────────────────────────────

function ExtractedTextSection({ excerpt }: { excerpt: string }) {
  return (
    <Section title="Testo estratto — estratto controllato">
      <details>
        <summary className="cursor-pointer text-xs text-muted hover:text-[#E8EDF7] transition-colors">
          Mostra estratto ({excerpt.length.toLocaleString("it-IT")} caratteri)
        </summary>
        <div className="mt-3 max-h-64 overflow-y-auto rounded border border-white/10 bg-navy-950/60 p-3 font-mono text-[11px] leading-relaxed text-muted whitespace-pre-wrap break-all">
          {excerpt}
        </div>
      </details>
    </Section>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default async function FilingDetailPage({ params }: Props) {
  const { id } = await params;
  const filingId = parseInt(id, 10);
  if (!filingId || isNaN(filingId)) notFound();

  const filing = await getFilingDetail(filingId);
  if (!filing) notFound();

  return (
    <div className="space-y-5">
      {/* Navigation */}
      <div className="flex items-center justify-between">
        <Link
          href="/internal/filings"
          className="text-xs text-muted transition-colors hover:text-[#E8EDF7]"
        >
          ← Torna ai filing
        </Link>
      </div>

      {/* Header */}
      <div className="rounded-lg border border-white/10 bg-navy-900/40 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <h1 className="font-mono text-lg font-semibold text-[#E8EDF7]">
                Filing #{filing.id}
              </h1>
              <StatusBadge status={filing.status} />
            </div>
            {filing.company_name && (
              <p className="text-sm text-muted">{filing.company_name}</p>
            )}
          </div>
          {/* Actions */}
          <div className="flex items-center gap-2">
            {filing.pdf_url && (
              <a
                href={filing.pdf_url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded border border-white/[0.12] px-3 py-1.5 text-xs text-muted transition-colors hover:border-white/30 hover:text-[#E8EDF7]"
              >
                Apri fonte ↗
              </a>
            )}
            <RetryFilingButton filingId={filing.id} status={filing.status} />
          </div>
        </div>

        {/* Meta grid */}
        <div className="mt-4 grid grid-cols-2 gap-x-8 gap-y-3 border-t border-white/[0.07] pt-4 sm:grid-cols-4">
          <InfoRow label="URL fonte">
            {filing.pdf_url ? (
              <a
                href={filing.pdf_url}
                target="_blank"
                rel="noopener noreferrer"
                className="break-all font-mono text-[11px] text-brand-blue hover:underline"
              >
                {filing.pdf_url}
              </a>
            ) : (
              <span className="text-muted text-sm">Dato non disponibile</span>
            )}
          </InfoRow>
          <InfoRow label="Data pubblicazione">
            <span className="text-sm text-muted">
              {fmtTs(filing.source_published_utc) !== "Dato non disponibile"
                ? fmtTs(filing.source_published_utc)
                : filing.filing_date
                  ? formatDate(filing.filing_date)
                  : "Dato non disponibile"}
            </span>
          </InfoRow>
          <InfoRow label="Data scoperta">
            <span className="text-sm text-muted">
              {fmtTs(filing.discovered_utc) !== "Dato non disponibile"
                ? fmtTs(filing.discovered_utc)
                : fmtTs(filing.first_seen_at)}
            </span>
          </InfoRow>
          <InfoRow label="Versione parser">
            <span className="font-mono text-sm text-muted">{filing.scraper_version ?? "Dato non disponibile"}</span>
          </InfoRow>
          <InfoRow label="Hash documento (SHA-256)">
            {filing.pdf_sha256 ? (
              <span className="break-all font-mono text-[10px] text-muted">{filing.pdf_sha256}</span>
            ) : (
              <span className="text-sm text-muted">Dato non disponibile</span>
            )}
          </InfoRow>
          <InfoRow label="Storage verificato">
            {filing.has_stored_document ? (
              <span className="text-sm text-brand-emerald">
                Sì — {fmtBytes(filing.file_size_bytes)}
              </span>
            ) : (
              <span className="text-sm text-muted/50">No</span>
            )}
          </InfoRow>
          <InfoRow label="Tx inserite / saltate (dedup)">
            <span className="tabular-nums text-sm text-muted">
              {filing.transactions_inserted} / {filing.transactions_skipped_dedup}
            </span>
          </InfoRow>
          <InfoRow label="Prima rilevazione">
            <span className="text-sm text-muted">
              {filing.first_seen_at
                ? new Date(filing.first_seen_at).toLocaleString("it-IT", {
                    dateStyle: "short",
                    timeStyle: "short",
                  })
                : "—"}
            </span>
          </InfoRow>
        </div>
      </div>

      {/* Lifecycle Timeline */}
      <LifecycleTimeline filing={filing} />

      {/* Transactions */}
      <TransactionsSection filing={filing} />

      {/* Processing History */}
      <ProcessingHistory filing={filing} />

      {/* Error & Recovery */}
      <ErrorRecoverySection filing={filing} />

      {/* Extracted Text */}
      {filing.extracted_text_excerpt && (
        <ExtractedTextSection excerpt={filing.extracted_text_excerpt} />
      )}
    </div>
  );
}
