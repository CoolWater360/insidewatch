import Link from "next/link";
import { notFound } from "next/navigation";
import { getSignalDetail } from "@/lib/signal-view";
import { isSupabaseConfigured } from "@/lib/supabase";
import { ConfigNotice } from "@/components/ConfigNotice";
import { formatCurrency } from "@/lib/format";
import { PlaceholderPage } from "@/components/internal/ui/PlaceholderPage";

export const dynamic = "force-dynamic";

interface Props {
  params: Promise<{ id: string }>;
}

function fmtDate(d: string | null) {
  if (!d) return "—";
  return new Date(d).toLocaleDateString("it-IT", { day: "2-digit", month: "long", year: "numeric" });
}

function fmtTs(ts: string | null) {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" });
}

function ConfidencePill({ score, caveat }: { score: number; caveat: boolean }) {
  const pct = Math.round(score * 100);
  const cls =
    pct >= 80 ? "border-buy/30 bg-buy/10 text-buy"
    : pct >= 60 ? "border-signal/30 bg-signal/10 text-signal"
    : "border-sell/30 bg-sell/10 text-sell";
  return (
    <div className="flex flex-col items-start gap-1">
      <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${cls}`}>
        Fiducia {pct}%
      </span>
      {caveat && (
        <span className="text-[10px] text-signal">
          Contesto insufficiente — segnale sotto soglia di qualità
        </span>
      )}
    </div>
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
}: {
  title: string;
  status: "planned" | "partial" | "normalizing";
}) {
  const label =
    status === "planned"     ? "Fonte non ancora integrata"
    : status === "partial"   ? "Copertura parziale"
    : "Dati storici in normalizzazione";
  const cls =
    status === "planned"     ? "text-muted/50 border-white/[0.06] bg-white/[0.02]"
    : status === "partial"   ? "text-signal border-signal/20 bg-signal/5"
    : "text-brand-blue border-brand-blue/20 bg-brand-blue/5";
  return (
    <div className={`flex items-center justify-between rounded-lg border px-3 py-2.5 ${cls}`}>
      <span className="text-xs font-medium">{title}</span>
      <span className="text-[10px] font-semibold uppercase tracking-wide opacity-70">{label}</span>
    </div>
  );
}

export default async function SignalDetailPage({ params }: Props) {
  if (!isSupabaseConfigured) return <ConfigNotice />;

  const { id } = await params;

  // Parse slug: "{companyId}_{windowStart}"
  const underscoreIdx = id.indexOf("_");
  if (underscoreIdx < 1) notFound();

  const companyId   = parseInt(id.slice(0, underscoreIdx), 10);
  const windowStart = id.slice(underscoreIdx + 1);
  if (isNaN(companyId) || !windowStart) notFound();

  const detail = await getSignalDetail(companyId, windowStart);
  if (!detail) notFound();

  const { row, insiders, transactions, filings } = detail;
  const reviewCount = transactions.filter((t) => t.review_status === "pending_review" || t.review_status === "under_review").length;
  const uniqueFilingIds = [...new Set(transactions.map((t) => t.source_filing_id).filter((x): x is number => x != null))];

  return (
    <div className="space-y-5">
      {/* Back link */}
      <Link
        href="/internal/signals"
        className="inline-flex items-center gap-1 text-xs text-muted/50 transition-colors hover:text-muted"
      >
        ← Tutti i segnali
      </Link>

      {/* Header */}
      <div className="rounded-lg border border-white/[0.08] bg-navy-900/50 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold text-[#E8EDF7]">{row.company_name}</h1>
            <p className="mt-0.5 text-xs text-muted">
              {row.signal_type} · {fmtDate(row.window_start)}–{fmtDate(row.window_end)}
            </p>
          </div>
          <ConfidencePill score={row.confidence} caveat={row.confidence_caveat} />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded bg-white/[0.04] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted/60">Soggetti</p>
            <p className="mt-0.5 text-sm font-semibold tabular-nums text-[#E8EDF7]">{row.insider_count}</p>
          </div>
          <div className="rounded bg-white/[0.04] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted/60">Operazioni</p>
            <p className="mt-0.5 text-sm font-semibold tabular-nums text-[#E8EDF7]">{transactions.length}</p>
          </div>
          <div className="rounded bg-white/[0.04] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted/60">Valore aggregato</p>
            <p className="mt-0.5 text-sm font-semibold tabular-nums text-[#E8EDF7]">{formatCurrency(row.total_value)}</p>
          </div>
          <div className="rounded bg-white/[0.04] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted/60">Filing fonte</p>
            <p className="mt-0.5 text-sm font-semibold tabular-nums text-[#E8EDF7]">{uniqueFilingIds.length}</p>
          </div>
        </div>
      </div>

      {/* Perché esiste questo segnale */}
      <SectionCard title="Perché esiste questo segnale">
        <div className="space-y-3">
          <div className="flex flex-wrap gap-1.5">
            {row.rationale.map((r, i) => (
              <span
                key={i}
                className="rounded-full border border-white/[0.08] bg-white/[0.04] px-2.5 py-0.5 text-[11px] text-muted"
              >
                {r}
              </span>
            ))}
          </div>

          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/[0.07] text-left text-[10px] uppercase tracking-wide text-muted/60">
                  <th className="pb-2 pr-4">Insider</th>
                  <th className="pb-2 pr-4">Ruolo</th>
                  <th className="pb-2 pr-4">Data prima operazione</th>
                  <th className="pb-2 text-right">Valore</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {insiders.map((ins, i) => (
                  <tr key={i}>
                    <td className="py-1.5 pr-4 font-medium text-[#E8EDF7]">
                      {ins.name === "Unknown"
                        ? <span className="italic text-muted/60">Nome non disponibile</span>
                        : ins.name}
                    </td>
                    <td className="py-1.5 pr-4 text-muted">{ins.role ?? "—"}</td>
                    <td className="py-1.5 pr-4 tabular-nums text-muted">{fmtDate(ins.date)}</td>
                    <td className="py-1.5 text-right tabular-nums text-[#E8EDF7]">
                      {ins.total_value > 0 ? formatCurrency(ins.total_value) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </SectionCard>

      {/* Operazioni incluse */}
      <SectionCard title="Operazioni incluse">
        {transactions.length === 0 ? (
          <p className="text-xs text-muted">Nessuna operazione trovata per questa finestra.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/[0.07] text-left text-[10px] uppercase tracking-wide text-muted/60">
                  <th className="pb-2 pr-3">Data</th>
                  <th className="pb-2 pr-3">Insider</th>
                  <th className="pb-2 pr-3">Ruolo</th>
                  <th className="pb-2 pr-3">Tipo</th>
                  <th className="pb-2 pr-3 text-right">Valore EUR</th>
                  <th className="pb-2 pr-3">Rev.</th>
                  <th className="pb-2">Filing</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {transactions.map((tx) => (
                  <tr key={tx.id} className="hover:bg-white/[0.02]">
                    <td className="py-1.5 pr-3 tabular-nums text-muted">{fmtDate(tx.transaction_date)}</td>
                    <td className="py-1.5 pr-3 font-medium text-[#E8EDF7]">{tx.insider_name}</td>
                    <td className="py-1.5 pr-3 text-muted">{tx.insider_role ?? "—"}</td>
                    <td className="py-1.5 pr-3 font-mono text-muted">
                      {tx.transaction_type ?? "—"}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-[#E8EDF7]">
                      {formatCurrency(tx.total_value)}
                    </td>
                    <td className="py-1.5 pr-3">
                      {tx.review_status && tx.review_status !== "confirmed" ? (
                        <span className="text-[10px] text-signal">{tx.review_status}</span>
                      ) : (
                        <span className="text-muted/30">—</span>
                      )}
                    </td>
                    <td className="py-1.5 font-mono text-[10px] text-muted">
                      {tx.source_filing_id ? (
                        <Link
                          href={`/internal/filings/${tx.source_filing_id}`}
                          className="text-brand-blue hover:underline"
                        >
                          #{tx.source_filing_id}
                        </Link>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* Contesto emittente */}
      <SectionCard title="Contesto emittente">
        <div className="space-y-2">
          {/* Real data */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div className="rounded bg-white/[0.04] px-3 py-2">
              <p className="text-[10px] uppercase tracking-wide text-muted/60">Tx in revisione</p>
              <p className="mt-0.5 text-sm font-semibold tabular-nums text-[#E8EDF7]">
                {reviewCount > 0 ? (
                  <span className="text-signal">{reviewCount}</span>
                ) : reviewCount}
              </p>
            </div>
            <div className="rounded bg-white/[0.04] px-3 py-2">
              <p className="text-[10px] uppercase tracking-wide text-muted/60">Filing collegati</p>
              <p className="mt-0.5 text-sm font-semibold tabular-nums text-[#E8EDF7]">{uniqueFilingIds.length}</p>
            </div>
            <div className="rounded bg-white/[0.04] px-3 py-2">
              <p className="text-[10px] uppercase tracking-wide text-muted/60">Parser versioni</p>
              <p className="mt-0.5 text-sm font-semibold tabular-nums text-muted">
                {[...new Set(transactions.map((t) => t.parser_version).filter(Boolean))].join(", ") || "—"}
              </p>
            </div>
          </div>

          {/* Placeholder blocks for unintegrated modules */}
          <div className="mt-3 space-y-1.5">
            <PlaceholderBlock title="Proprietà & Controllo" status="planned" />
            <PlaceholderBlock title="Buyback" status="planned" />
            <PlaceholderBlock title="Governance" status="planned" />
            <PlaceholderBlock title="Azioni Societarie" status="planned" />
          </div>
        </div>
      </SectionCard>

      {/* Evidence */}
      <SectionCard title="Evidenza & Provenienza">
        {filings.length === 0 ? (
          <p className="text-xs text-muted">Nessun filing identificato per questo segnale.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/[0.07] text-left text-[10px] uppercase tracking-wide text-muted/60">
                  <th className="pb-2 pr-3">Filing ID</th>
                  <th className="pb-2 pr-3">Data Filing</th>
                  <th className="pb-2 pr-3">Stato</th>
                  <th className="pb-2 pr-3">Hash SHA-256</th>
                  <th className="pb-2">URL fonte</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {filings.map((f) => (
                  <tr key={f.id}>
                    <td className="py-1.5 pr-3">
                      <Link
                        href={`/internal/filings/${f.id}`}
                        className="font-mono text-brand-blue hover:underline"
                      >
                        #{f.id}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-3 tabular-nums text-muted">{fmtTs(f.filing_date)}</td>
                    <td className="py-1.5 pr-3 text-muted">{f.status}</td>
                    <td className="py-1.5 pr-3">
                      {f.pdf_sha256 ? (
                        <span className="font-mono text-[10px] text-muted/70" title={f.pdf_sha256}>
                          {f.pdf_sha256.slice(0, 12)}…
                        </span>
                      ) : (
                        <span className="text-muted/30">—</span>
                      )}
                    </td>
                    <td className="py-1.5">
                      <a
                        href={f.pdf_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="block max-w-[200px] truncate font-mono text-[10px] text-brand-blue hover:underline"
                        title={f.pdf_url}
                      >
                        {f.pdf_url}
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </div>
  );
}
