import Link from "next/link";
import { getReviewQueueCounts } from "@/lib/review";
import { PageHeader } from "@/components/internal/ui/PageHeader";

export const dynamic = "force-dynamic";

export default async function ReviewQueuePage() {
  const counts = await getReviewQueueCounts();

  const queues = [
    {
      label: "Transazioni da rivedere",
      description: "Transazioni a bassa confidenza o con classificazione ambigua che richiedono revisione manuale.",
      count: counts.pendingTransactions,
      href: "/internal/transactions",
      color: "border-signal/20 bg-signal/[0.04]",
      countColor: "text-signal",
    },
    {
      label: "Filing falliti",
      description: "Documenti PDF che non hanno superato il parsing o hanno generato errori durante l'elaborazione.",
      count: counts.failedFilings,
      href: "/internal/filings",
      color: "border-sell/20 bg-sell/[0.04]",
      countColor: "text-sell",
    },
    {
      label: "Filing saltati",
      description: "Documenti ignorati dall'elaborazione automatica, in attesa di revisione.",
      count: counts.skippedFilings,
      href: "/internal/filings",
      color: "border-muted/20 bg-white/[0.02]",
      countColor: "text-muted",
    },
    {
      label: "Emittenti non abbinati",
      description: "Nomi aziendali estratti dai filing che non hanno trovato corrispondenza nel registro emittenti.",
      count: counts.pendingIssuers,
      href: "/internal/issuers",
      color: "border-brand-blue/20 bg-brand-blue/[0.04]",
      countColor: "text-brand-blue",
    },
  ];

  const total = counts.pendingTransactions + counts.failedFilings + counts.skippedFilings + counts.pendingIssuers;

  return (
    <div>
      <PageHeader
        title="Coda di Revisione"
        subtitle="Elementi che richiedono intervento manuale dell'operatore"
        badge={
          total > 0 ? (
            <span className="rounded-full border border-signal/30 bg-signal/10 px-2.5 py-0.5 text-[11px] font-semibold text-signal">
              {total} in attesa
            </span>
          ) : (
            <span className="rounded-full border border-buy/30 bg-buy/10 px-2.5 py-0.5 text-[11px] font-semibold text-buy">
              Tutto revisionato
            </span>
          )
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {queues.map((q) => (
          <Link
            key={q.href + q.label}
            href={q.href}
            className={`group flex flex-col rounded-xl border p-5 transition-opacity hover:opacity-90 ${q.color}`}
          >
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-semibold text-[#E8EDF7]">{q.label}</p>
              <span className={`text-2xl font-bold tabular-nums ${q.countColor}`}>{q.count}</span>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-muted">{q.description}</p>
            <span className="mt-4 text-[11px] font-medium text-muted/60 group-hover:text-muted">
              Vai alla coda →
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
