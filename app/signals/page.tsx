import { cookies } from "next/headers";
import Link from "next/link";
import { getClusterSignals } from "@/lib/queries";
import { isSupabaseConfigured } from "@/lib/supabase";
import { formatCurrency, formatDate, formatNumber } from "@/lib/format";
import { ConfigNotice } from "@/components/ConfigNotice";

export const dynamic = "force-dynamic";

export default async function SignalsPage() {
  if (!isSupabaseConfigured) return <ConfigNotice />;

  const jar  = await cookies();
  const lang = (jar.get("insidewatch_lang")?.value === "en" ? "en" : "it") as "it" | "en";
  const t    = (it: string, en: string) => lang === "it" ? it : en;

  const signals = await getClusterSignals(90);

  return (
    <div className="space-y-5 animate-fade-up">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-base font-semibold tracking-tight text-[#E8EDF7]">
            {t("Segnali di Acquisto Coordinato", "Cluster-Buying Signals")}
          </h1>
          <p className="mt-1 text-xs text-muted">
            {t(
              "2+ soggetti rilevanti acquistano nella stessa società in una finestra di 7 giorni · ultimi 90 giorni",
              "2+ insiders buying at the same company within a 7-day window · last 90 days",
            )}
          </p>
        </div>
        <span className="rounded-full border border-signal/30 bg-signal/10 px-3 py-1 text-xs font-semibold text-signal">
          {signals.length} {t("segnali", "signals")}
        </span>
      </div>

      {signals.length === 0 ? (
        <div className="glass-card rounded-xl p-10 text-center">
          <p className="text-sm text-muted">
            {t("Nessun segnale cluster negli ultimi 90 giorni.", "No cluster signals in the last 90 days.")}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {signals.map((sig, idx) => {
            const distinctInsiders = new Set(sig.insiders.map((i) => i.name)).size;
            return (
              <div key={`${sig.company_id}-${sig.window_start}-${idx}`} className="glass-card-signal rounded-xl p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <Link
                      href={`/company/${sig.company_id}`}
                      className="font-semibold text-[#E8EDF7] hover:text-signal transition-colors"
                    >
                      {sig.company_name}
                    </Link>
                    <p className="mt-0.5 text-xs text-muted">
                      {formatDate(sig.window_start, lang)}
                      {sig.window_start !== sig.window_end ? ` – ${formatDate(sig.window_end, lang)}` : ""}
                      <span className="mx-1 opacity-40">·</span>
                      <span className="font-medium text-signal">
                        {sig.insiders.length} {t("acquisti", "buys")} · {distinctInsiders} {t("insider", "insiders")}
                      </span>
                    </p>
                  </div>
                  <span className="rounded-lg border border-signal/30 bg-signal/10 px-3 py-1 text-sm font-bold tabular-nums text-signal">
                    {formatCurrency(sig.total_value)}
                  </span>
                </div>

                <div className="mt-4 overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-white/[0.06] text-left">
                        <th className="pb-1.5 pr-4 font-semibold uppercase tracking-widest text-muted/60">
                          {t("Insider", "Insider")}
                        </th>
                        <th className="hidden pb-1.5 pr-4 font-semibold uppercase tracking-widest text-muted/60 sm:table-cell">
                          {t("Ruolo", "Role")}
                        </th>
                        <th className="pb-1.5 pr-4 font-semibold uppercase tracking-widest text-muted/60">
                          {t("Data", "Date")}
                        </th>
                        <th className="hidden pb-1.5 pr-4 text-right font-semibold uppercase tracking-widest text-muted/60 sm:table-cell">
                          {t("Qtà", "Qty")}
                        </th>
                        <th className="pb-1.5 text-right font-semibold uppercase tracking-widest text-muted/60">
                          {t("Valore", "Value")}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/[0.04]">
                      {sig.insiders.map((ins, j) => (
                        <tr key={j}>
                          <td className="py-1.5 pr-4 font-medium text-[#E8EDF7]">{ins.name}</td>
                          <td className="hidden py-1.5 pr-4 text-muted sm:table-cell">{ins.role ?? "—"}</td>
                          <td className="py-1.5 pr-4 text-muted">{formatDate(ins.date, lang)}</td>
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
            );
          })}
        </div>
      )}
    </div>
  );
}
