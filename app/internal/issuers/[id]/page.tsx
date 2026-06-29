import Link from "next/link";
import { notFound } from "next/navigation";
import { getIssuerDetail } from "@/lib/signal-view";

export const dynamic = "force-dynamic";

interface Props {
  params: Promise<{ id: string }>;
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
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${cls}`}>
      {ISSUER_STATUS_LABELS[status] ?? status}
    </span>
  );
}

function SectionCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-white/[0.08] bg-navy-900/40 p-4">
      <h2 className="mb-3 text-[11px] font-semibold uppercase tracking-widest text-muted/60">
        {title}
      </h2>
      {children}
    </div>
  );
}

function PlaceholderBlock({
  title,
  status,
  description,
}: {
  title: string;
  status: "planned" | "partial" | "normalizing";
  description?: string;
}) {
  const label =
    status === "planned"     ? "Fonte non ancora integrata"
    : status === "partial"   ? "Copertura parziale"
    : "Dati storici in normalizzazione";
  const borderCls =
    status === "planned"     ? "border-white/[0.06] bg-white/[0.02]"
    : status === "partial"   ? "border-signal/20 bg-signal/5"
    : "border-brand-blue/20 bg-brand-blue/5";
  const textCls =
    status === "planned"     ? "text-muted/50"
    : status === "partial"   ? "text-signal"
    : "text-brand-blue";
  return (
    <div className={`rounded-lg border p-4 ${borderCls}`}>
      <div className="flex items-center justify-between">
        <p className={`text-xs font-medium ${textCls}`}>{title}</p>
        <span className={`text-[10px] font-semibold uppercase tracking-wide opacity-70 ${textCls}`}>
          {label}
        </span>
      </div>
      {description && (
        <p className="mt-1 text-[11px] text-muted/50">{description}</p>
      )}
    </div>
  );
}

export default async function IssuerDetailPage({ params }: Props) {
  const { id } = await params;
  const issuerId = parseInt(id, 10);
  if (isNaN(issuerId)) notFound();

  const issuer = await getIssuerDetail(issuerId);
  if (!issuer) notFound();

  return (
    <div className="space-y-5">
      {/* Back link */}
      <Link
        href="/internal/issuers"
        className="inline-flex items-center gap-1 text-xs text-muted/50 transition-colors hover:text-muted"
      >
        ← Tutti gli emittenti
      </Link>

      {/* Header */}
      <div className="rounded-lg border border-white/[0.08] bg-navy-900/50 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold text-[#E8EDF7]">{issuer.canonical_name}</h1>
            {issuer.short_name && issuer.short_name !== issuer.canonical_name && (
              <p className="mt-0.5 text-xs text-muted">{issuer.short_name}</p>
            )}
          </div>
          <StatusBadge status={issuer.status} />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded bg-white/[0.04] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted/60">ISIN</p>
            <p className="mt-0.5 font-mono text-sm font-semibold text-[#E8EDF7]">
              {issuer.isin ?? <span className="text-muted">—</span>}
            </p>
          </div>
          <div className="rounded bg-white/[0.04] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted/60">LEI</p>
            <p className="mt-0.5 font-mono text-xs text-muted" title={issuer.lei ?? undefined}>
              {issuer.lei ? `${issuer.lei.slice(0, 8)}…` : "—"}
            </p>
          </div>
          <div className="rounded bg-white/[0.04] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted/60">Mercato</p>
            <p className="mt-0.5 text-sm font-semibold text-[#E8EDF7]">{issuer.market ?? "—"}</p>
          </div>
          <div className="rounded bg-white/[0.04] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted/60">Settore</p>
            <p className="mt-0.5 text-sm text-[#E8EDF7]">{issuer.sector ?? "—"}</p>
          </div>
        </div>

        {issuer.unmatched_pending > 0 && (
          <div className="mt-3 rounded border border-signal/20 bg-signal/5 px-3 py-2 text-xs text-signal">
            {issuer.unmatched_pending} nome/i non risolto/i in coda di revisione
            <Link href="/internal/review" className="ml-2 font-semibold hover:underline">
              Vai alla coda →
            </Link>
          </div>
        )}
      </div>

      {/* Linked companies */}
      <SectionCard title="Società collegate">
        {issuer.companies.length === 0 ? (
          <p className="text-xs text-muted">
            Nessuna società collegata. Le società vengono associate tramite il flusso di
            normalizzazione degli emittenti.
          </p>
        ) : (
          <div className="divide-y divide-white/[0.06]">
            {issuer.companies.map((c) => (
              <div key={c.id} className="flex items-center justify-between py-2">
                <span className="text-xs text-[#E8EDF7]">{c.name}</span>
                <span className="font-mono text-[10px] text-muted">ID {c.id}</span>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      {/* Placeholder blocks for unintegrated modules */}
      <div className="space-y-2">
        <PlaceholderBlock
          title="Proprietà & Controllo"
          status="planned"
          description="Struttura azionariato, partecipazioni rilevanti, soglie di notifica CONSOB."
        />
        <PlaceholderBlock
          title="Buyback"
          status="planned"
          description="Programmi di riacquisto azioni proprie, volumi e scadenze autorizzate."
        />
        <PlaceholderBlock
          title="Governance"
          status="planned"
          description="Composizione CdA, comitati, proxy advisor recommendations."
        />
        <PlaceholderBlock
          title="Azioni Societarie"
          status="planned"
          description="Dividendi, operazioni straordinarie, comunicati price-sensitive."
        />
        <PlaceholderBlock
          title="Entità & Veicoli"
          status="normalizing"
          description="Holding, fiduciarie, veicoli societari intestati a insider o entità correlate."
        />
      </div>

      {/* Metadata footer */}
      <div className="rounded border border-white/[0.06] bg-white/[0.02] px-4 py-3">
        <div className="flex flex-wrap gap-6 text-[11px] text-muted">
          <span>
            Paese: <span className="text-[#E8EDF7]">{issuer.country}</span>
          </span>
          <span>
            Registrato:{" "}
            <span className="text-[#E8EDF7]">
              {new Date(issuer.created_at).toLocaleDateString("it-IT", {
                day: "2-digit", month: "long", year: "numeric",
              })}
            </span>
          </span>
          <span>
            Issuer ID: <span className="font-mono text-[#E8EDF7]">{issuer.id}</span>
          </span>
        </div>
      </div>
    </div>
  );
}
