import { getClusterSignalsWithConfidence } from "@/lib/signals";
import { isSupabaseConfigured } from "@/lib/supabase";
import { formatCurrency, formatDate, formatNumber } from "@/lib/format";
import { ConfigNotice } from "@/components/ConfigNotice";
import { PageHeader } from "@/components/internal/ui/PageHeader";
import { EmptyState } from "@/components/internal/ui/EmptyState";
import { ConfidenceBadge } from "@/components/internal/ui/StatusBadge";

export const dynamic = "force-dynamic";

export default async function InternalSignalsPage() {
  if (!isSupabaseConfigured) return <ConfigNotice />;

  const signals = await getClusterSignalsWithConfidence(90);

  return (
    <div>
      <PageHeader
        title="Segnali & Contesto"
        subtitle="Acquisti coordinati da 2+ insider nella stessa società in 7 giorni · ultimi 90 giorni"
        badge={
          <span className="rounded-full border border-signal/30 bg-signal/10 px-2.5 py-0.5 text-[11px] font-semibold text-signal">
            {signals.length} segnali
          </span>
        }
      />

      {signals.length === 0 ? (
        <EmptyState
          title="Nessun segnale cluster"
          description="Non sono stati rilevati acquisti coordinati negli ultimi 90 giorni."
        />
      ) : (
        <div className="space-y-3">
          {signals.map((sig, idx) => (
            <div
              key={`${sig.company_id}-${sig.window_start}-${idx}`}
              className="rounded-xl border border-signal/20 bg-signal/[0.04] p-5"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="font-semibold text-[#E8EDF7]">{sig.company_name}</p>
                  <p className="mt-0.5 text-xs text-muted">
                    {formatDate(sig.window_start, "it")}
                    {sig.window_start !== sig.window_end && ` – ${formatDate(sig.window_end, "it")}`}
                    <span className="mx-1 opacity-40">·</span>
                    <span className="font-medium text-signal">{sig.insider_count} insider</span>
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <ConfidenceBadge score={sig.confidence} />
                  <span className="rounded-lg border border-signal/30 bg-signal/10 px-2.5 py-1 text-sm font-bold tabular-nums text-signal">
                    {sig.total_value > 0 ? formatCurrency(sig.total_value) : "—"}
                  </span>
                </div>
              </div>

              {/* Confidence rationale */}
              {sig.rationale.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1">
                  {sig.rationale.map((r, ri) => (
                    <span
                      key={ri}
                      className="rounded px-1.5 py-0.5 text-[10px] text-muted/60 ring-1 ring-white/[0.07]"
                    >
                      {r}
                    </span>
                  ))}
                </div>
              )}

              {/* Insider table */}
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-white/[0.06] text-left">
                      <th className="pb-1.5 pr-4 font-semibold uppercase tracking-widest text-muted/60">Insider</th>
                      <th className="hidden pb-1.5 pr-4 font-semibold uppercase tracking-widest text-muted/60 sm:table-cell">Ruolo</th>
                      <th className="pb-1.5 pr-4 font-semibold uppercase tracking-widest text-muted/60">Data</th>
                      <th className="hidden pb-1.5 pr-4 text-right font-semibold uppercase tracking-widest text-muted/60 sm:table-cell">Qtà</th>
                      <th className="pb-1.5 text-right font-semibold uppercase tracking-widest text-muted/60">Valore</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04]">
                    {sig.insiders.map((ins, j) => (
                      <tr key={j}>
                        <td className="py-1.5 pr-4 font-medium text-[#E8EDF7]">
                          {ins.name === "Unknown"
                            ? <span className="italic text-muted/60">Nome non disp.</span>
                            : ins.name}
                        </td>
                        <td className="hidden py-1.5 pr-4 text-muted sm:table-cell">{ins.role ?? "—"}</td>
                        <td className="py-1.5 pr-4 text-muted">{formatDate(ins.date, "it")}</td>
                        <td className="hidden py-1.5 pr-4 text-right tabular-nums text-muted sm:table-cell">
                          {formatNumber(ins.quantity)}
                        </td>
                        <td className="py-1.5 text-right font-semibold tabular-nums text-[#E8EDF7]">
                          {ins.total_value > 0 ? formatCurrency(ins.total_value) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
