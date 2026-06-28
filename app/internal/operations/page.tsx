import { getReviewQueueCounts } from "@/lib/review";
import { PageHeader } from "@/components/internal/ui/PageHeader";

export const dynamic = "force-dynamic";

export default async function OperationsPage() {
  const counts = await getReviewQueueCounts();

  const totalPending = counts.pendingTransactions + counts.failedFilings + counts.skippedFilings + counts.pendingIssuers;

  const pipelineStatus = [
    {
      component: "Parser PDF",
      status: counts.failedFilings === 0 ? "Operativo" : "Degradato",
      detail: counts.failedFilings === 0 ? "Nessun filing fallito" : `${counts.failedFilings} filing falliti`,
      ok: counts.failedFilings === 0,
    },
    {
      component: "Classificatore transazioni",
      status: counts.pendingTransactions < 10 ? "Operativo" : "Attenzione richiesta",
      detail: `${counts.pendingTransactions} transazioni in coda revisione`,
      ok: counts.pendingTransactions < 10,
    },
    {
      component: "Abbinamento emittenti",
      status: counts.pendingIssuers === 0 ? "Operativo" : "Attenzione richiesta",
      detail: `${counts.pendingIssuers} emittenti senza corrispondenza`,
      ok: counts.pendingIssuers === 0,
    },
    {
      component: "Scraper Consob",
      status: "Stato non disponibile",
      detail: "Monitoraggio scraper non ancora integrato",
      ok: null,
    },
  ];

  return (
    <div>
      <PageHeader
        title="Operazioni"
        subtitle="Stato della pipeline di acquisizione e elaborazione dati"
        badge={
          totalPending === 0 ? (
            <span className="rounded-full border border-buy/30 bg-buy/10 px-2.5 py-0.5 text-[11px] font-semibold text-buy">
              Pipeline operativa
            </span>
          ) : (
            <span className="rounded-full border border-signal/30 bg-signal/10 px-2.5 py-0.5 text-[11px] font-semibold text-signal">
              {totalPending} elementi in attesa
            </span>
          )
        }
      />

      <div className="space-y-2">
        {pipelineStatus.map((p) => (
          <div
            key={p.component}
            className="flex items-center justify-between rounded-lg border border-white/[0.07] bg-white/[0.02] px-4 py-3"
          >
            <div>
              <p className="text-xs font-semibold text-[#E8EDF7]">{p.component}</p>
              <p className="text-[11px] text-muted">{p.detail}</p>
            </div>
            <span
              className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${
                p.ok === null
                  ? "border-muted/20 bg-white/[0.02] text-muted/60"
                  : p.ok
                  ? "border-buy/30 bg-buy/10 text-buy"
                  : "border-signal/30 bg-signal/10 text-signal"
              }`}
            >
              {p.status}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-6 rounded-xl border border-white/[0.07] bg-white/[0.02] px-5 py-4">
        <p className="text-xs font-semibold text-muted/60">Pianificazione e log esecuzioni</p>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          La cronologia delle esecuzioni scraper, il log degli errori dettagliati e il controllo
          manuale dei job di acquisizione saranno disponibili in una fase successiva dello sviluppo
          del modulo Operazioni.
        </p>
      </div>
    </div>
  );
}
