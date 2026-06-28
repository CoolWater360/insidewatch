import { getReviewQueueCounts } from "@/lib/review";
import { PageHeader } from "@/components/internal/ui/PageHeader";

export const dynamic = "force-dynamic";

export default async function DataQualityPage() {
  const counts = await getReviewQueueCounts();

  const metrics = [
    {
      label: "Transazioni in revisione",
      value: counts.pendingTransactions,
      note: "Bassa confidenza o classificazione ambigua",
      signal: counts.pendingTransactions > 0 ? "warn" : "ok",
    },
    {
      label: "Filing non elaborati",
      value: counts.failedFilings + counts.skippedFilings,
      note: `${counts.failedFilings} falliti · ${counts.skippedFilings} saltati`,
      signal: counts.failedFilings > 0 ? "error" : counts.skippedFilings > 0 ? "warn" : "ok",
    },
    {
      label: "Emittenti non abbinati",
      value: counts.pendingIssuers,
      note: "Nomi estratti senza corrispondenza nel registro",
      signal: counts.pendingIssuers > 0 ? "warn" : "ok",
    },
  ] as const;

  const signalColors = {
    ok:    "border-buy/20 bg-buy/[0.04] text-buy",
    warn:  "border-signal/20 bg-signal/[0.04] text-signal",
    error: "border-sell/20 bg-sell/[0.04] text-sell",
  };

  return (
    <div>
      <PageHeader
        title="Qualità Dati"
        subtitle="Indicatori operativi estratti dalla pipeline di elaborazione in tempo reale"
      />

      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
        {metrics.map((m) => (
          <div
            key={m.label}
            className={`rounded-xl border p-5 ${signalColors[m.signal]}`}
          >
            <p className="text-2xl font-bold tabular-nums">{m.value}</p>
            <p className="mt-1 text-sm font-semibold text-[#E8EDF7]">{m.label}</p>
            <p className="mt-0.5 text-xs text-muted">{m.note}</p>
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-5 py-4">
        <p className="text-xs font-semibold text-muted/60">Metriche avanzate</p>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          Le metriche di completezza dei campi, accuratezza del parser per versione e distribuzione
          della confidenza di estrazione saranno disponibili con la prossima fase di sviluppo del
          modulo di qualità dati. I dati sopra riportati sono estratti in tempo reale dalla pipeline.
        </p>
      </div>
    </div>
  );
}
