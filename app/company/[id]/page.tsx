import { cookies } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getCompanyById, getCompanyStats, getTransactions } from "@/lib/queries";
import { isSupabaseConfigured } from "@/lib/supabase";
import { firstParam } from "@/lib/url";
import { TransactionTable } from "@/components/TransactionTable";
import { Pagination } from "@/components/Pagination";
import { ConfigNotice } from "@/components/ConfigNotice";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function StatCard({
  label, value, accent,
}: {
  label: string; value: number;
  accent: "blue" | "buy" | "sell";
}) {
  const accentClass = {
    blue: "metric-accent-blue",
    buy:  "metric-accent-buy",
    sell: "metric-accent-sell",
  }[accent];
  return (
    <div className={`glass-card rounded-xl px-4 py-3 ${accentClass}`}>
      <div className="text-xl font-semibold tabular-nums text-[#E8EDF7]">
        {value.toLocaleString("it-IT")}
      </div>
      <div className="mt-0.5 text-[10px] font-semibold uppercase tracking-widest text-muted">
        {label}
      </div>
    </div>
  );
}

export default async function CompanyPage({ params, searchParams }: PageProps) {
  if (!isSupabaseConfigured) return <ConfigNotice />;

  const jar  = await cookies();
  const lang = (jar.get("insidewatch_lang")?.value === "en" ? "en" : "it") as "it" | "en";
  const t    = (it: string, en: string) => lang === "it" ? it : en;

  const { id: idStr } = await params;
  const sp = await searchParams;
  const id = Number(idStr);
  if (!Number.isFinite(id)) notFound();

  const company = await getCompanyById(id);
  if (!company) notFound();

  const sort  = firstParam(sp.sort) ?? "transaction_date";
  const order = (firstParam(sp.order) as "asc" | "desc") ?? "desc";
  const page  = Number(firstParam(sp.page) ?? "1");

  const [stats, result] = await Promise.all([
    getCompanyStats(id),
    getTransactions({ companyId: id, sort, order, page, pageSize: 25 }),
  ]);

  const basePath = `/company/${id}`;
  const current  = { sort, order, page };

  return (
    <div className="space-y-5 animate-fade-up">
      <Link
        href="/"
        className="inline-flex items-center gap-1 text-xs text-muted hover:text-brand-blue transition-colors"
      >
        ← {t("Tutte le operazioni", "All transactions")}
      </Link>

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-[#E8EDF7]">
            {company.name}
          </h1>
          <p className="mt-1 text-xs text-muted">
            {[company.ticker, company.sector, company.isin && company.isin !== "unknown" ? company.isin : null]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {company.ir_internal_dealing_url && (
          <a
            href={company.ir_internal_dealing_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-brand-blue/70 hover:text-brand-blue transition-colors"
          >
            Investor Relations →
          </a>
        )}
      </div>

      <div className="grid grid-cols-3 gap-3">
        <StatCard label={t("Totale", "Total")} value={stats.total} accent="blue" />
        <StatCard label={t("Acquisti", "Buys")}  value={stats.buys}  accent="buy"  />
        <StatCard label={t("Vendite", "Sells")} value={stats.sells} accent="sell" />
      </div>

      <h2 className="text-sm font-semibold uppercase tracking-widest text-muted">
        {t("Storico Operazioni", "Transaction History")}
      </h2>
      <TransactionTable rows={result.rows} current={current} basePath={basePath} lang={lang} />
      <Pagination page={result.page} totalPages={result.totalPages} current={current} basePath={basePath} />
    </div>
  );
}
