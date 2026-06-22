import Link from "next/link";
import { TransactionWithRelations } from "@/lib/types";
import { DirectionBadge } from "./DirectionBadge";
import { formatCurrency, formatDate, formatNumber, formatPrice } from "@/lib/format";
import { buildHref, QueryValue } from "@/lib/url";

interface Props {
  rows: TransactionWithRelations[];
  current: Record<string, QueryValue>;
  basePath: string;
  lang?: "it" | "en";
}

function SortLink({
  column, label, current, basePath, align = "left",
}: {
  column: string; label: string; current: Record<string, QueryValue>;
  basePath: string; align?: "left" | "right";
}) {
  const active = current.sort === column;
  const nextOrder = active && current.order === "desc" ? "asc" : "desc";
  const arrow = active ? (current.order === "asc" ? " ▲" : " ▼") : "";
  return (
    <Link
      href={buildHref(basePath, { ...current, sort: column, order: nextOrder, page: 1 })}
      className={`sort-link inline-flex items-center gap-0.5 ${active ? "sort-link-active text-[#E8EDF7]" : ""} ${align === "right" ? "flex-row-reverse" : ""}`}
    >
      {label}{arrow && <span className="text-[9px]">{arrow}</span>}
    </Link>
  );
}

const ZERO_LABEL_IT: Record<string, string> = {
  grant:           "assegnazione",
  option_exercise: "esercizio op.",
  sell_to_cover:   "sell-to-cover",
};
const ZERO_LABEL_EN: Record<string, string> = {
  grant:           "grant",
  option_exercise: "option exercise",
  sell_to_cover:   "sell-to-cover",
};

function ValueCell({
  total, currency, txType, lang = "it",
}: {
  total: number; currency: string; txType?: string | null; lang?: "it" | "en";
}) {
  if (total === 0) {
    const labels = lang === "it" ? ZERO_LABEL_IT : ZERO_LABEL_EN;
    const sub = (txType && labels[txType]) ?? "non-cash";
    return (
      <span className="inline-flex flex-col items-end gap-0.5">
        <span className="text-xs font-medium text-muted">—</span>
        <span className="text-[10px] leading-none text-muted/60">{sub}</span>
      </span>
    );
  }
  return (
    <span className="font-semibold tabular-nums text-[#E8EDF7]">
      {formatCurrency(total, currency)}
    </span>
  );
}

export function TransactionTable({ rows, current, basePath, lang = "it" }: Props) {
  const t = (it: string, en: string) => lang === "it" ? it : en;

  if (rows.length === 0) {
    return (
      <div className="glass-card rounded-xl p-10 text-center">
        <p className="text-sm text-muted">
          {t("Nessuna operazione corrisponde ai filtri.", "No transactions match your filters.")}
        </p>
        <p className="mt-1 text-xs text-muted/60">
          {t(
            "Prova ad ampliare il periodo o rimuovere il filtro tipo.",
            "Try broadening the date range or removing the direction filter.",
          )}
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-white/[0.07]">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.07] bg-navy-900/60">
            {/* Mobile-only combined header */}
            <th className="px-4 py-3 text-left sm:hidden">
              <span className="sort-link">{t("Operazione", "Transaction")}</span>
            </th>
            {/* Desktop headers */}
            <th className="hidden px-4 py-3 text-left sm:table-cell">
              <SortLink column="transaction_date" label={t("Data", "Date")} current={current} basePath={basePath} />
            </th>
            <th className="hidden px-4 py-3 text-left sm:table-cell">
              <span className="sort-link">{t("Società", "Company")}</span>
            </th>
            <th className="hidden px-4 py-3 text-left sm:table-cell">
              <span className="sort-link">{t("Sogg. rilevante", "Insider")}</span>
            </th>
            <th className="hidden px-4 py-3 text-left lg:table-cell">
              <span className="sort-link">{t("Ruolo", "Role")}</span>
            </th>
            {/* Type always visible */}
            <th className="px-4 py-3 text-left">
              <SortLink column="direction" label={t("Tipo", "Type")} current={current} basePath={basePath} />
            </th>
            <th className="hidden px-4 py-3 text-right md:table-cell">
              <SortLink column="quantity" label={t("Qtà", "Qty")} current={current} basePath={basePath} align="right" />
            </th>
            <th className="hidden px-4 py-3 text-right lg:table-cell">
              <SortLink column="unit_price" label={t("Prezzo", "Price")} current={current} basePath={basePath} align="right" />
            </th>
            {/* Value always visible */}
            <th className="px-4 py-3 text-right">
              <SortLink column="total_value" label={t("Valore", "Value")} current={current} basePath={basePath} align="right" />
            </th>
            {/* Src — desktop only */}
            <th className="hidden px-4 py-3 text-right sm:table-cell">
              <span className="sort-link">{t("Fonte", "Src")}</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04]">
          {rows.map((tx, i) => (
            <tr
              key={tx.id}
              className={`tx-row ${i % 2 === 0 ? "bg-navy-950" : "bg-navy-900/40"}`}
            >
              {/* Mobile combined cell: company + insider + date stacked */}
              <td className="px-4 py-3 sm:hidden">
                <div className="font-medium text-[#E8EDF7]">
                  {tx.companies ? (
                    <Link
                      href={`/company/${tx.companies.id}`}
                      className="hover:text-brand-blue transition-colors"
                    >
                      {tx.companies.name}
                    </Link>
                  ) : "—"}
                </div>
                <div className="mt-0.5 text-xs text-muted">
                  {tx.insiders?.full_name ?? "—"}
                </div>
                <div className="mt-0.5 text-[11px] tabular-nums text-muted/60">
                  {formatDate(tx.transaction_date, lang)}
                </div>
              </td>
              {/* Desktop: separate date, company, insider cells */}
              <td className="hidden whitespace-nowrap px-4 py-2.5 text-xs tabular-nums text-muted sm:table-cell">
                {formatDate(tx.transaction_date, lang)}
              </td>
              <td className="hidden px-4 py-2.5 font-medium sm:table-cell">
                {tx.companies ? (
                  <Link
                    href={`/company/${tx.companies.id}`}
                    className="text-[#E8EDF7] hover:text-brand-blue transition-colors"
                  >
                    {tx.companies.name}
                  </Link>
                ) : "—"}
              </td>
              <td className="hidden px-4 py-2.5 text-[#E8EDF7] sm:table-cell">
                {tx.insiders?.full_name ?? "—"}
              </td>
              <td className="hidden max-w-[14rem] truncate px-4 py-2.5 text-xs text-muted lg:table-cell"
                  title={tx.insiders?.role ?? ""}>
                {tx.insiders?.role ?? "—"}
              </td>
              {/* Type badge — always visible */}
              <td className="px-4 py-2.5">
                <DirectionBadge
                  direction={tx.direction}
                  transactionType={tx.transaction_type}
                  needsReview={tx.needs_review}
                />
              </td>
              <td className="hidden px-4 py-2.5 text-right tabular-nums text-muted md:table-cell">
                {formatNumber(tx.quantity)}
              </td>
              <td className="hidden px-4 py-2.5 text-right tabular-nums text-muted lg:table-cell">
                {formatPrice(tx.unit_price, tx.currency)}
              </td>
              {/* Value — always visible */}
              <td className="px-4 py-2.5 text-right">
                <ValueCell total={tx.total_value} currency={tx.currency} txType={tx.transaction_type} lang={lang} />
              </td>
              {/* PDF — desktop only */}
              <td className="hidden px-4 py-2.5 text-right sm:table-cell">
                {tx.source_url ? (
                  <a
                    href={tx.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] font-medium text-brand-blue/70 hover:text-brand-blue transition-colors"
                  >
                    PDF
                  </a>
                ) : (
                  <span className="text-muted/30">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
