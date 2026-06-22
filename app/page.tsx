import { cookies } from "next/headers";
import { getDashboardStats, getCompanies, getTransactions } from "@/lib/queries";
import { isSupabaseConfigured } from "@/lib/supabase";
import { Direction } from "@/lib/types";
import { firstParam } from "@/lib/url";
import { Filters } from "@/components/Filters";
import { TransactionTable } from "@/components/TransactionTable";
import { Pagination } from "@/components/Pagination";
import { MetricCards } from "@/components/MetricCards";
import { ConfigNotice } from "@/components/ConfigNotice";

export const dynamic = "force-dynamic";

interface PageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function Home({ searchParams }: PageProps) {
  if (!isSupabaseConfigured) {
    return <ConfigNotice />;
  }

  const jar  = await cookies();
  const lang = (jar.get("insidewatch_lang")?.value === "en" ? "en" : "it") as "it" | "en";
  const t    = (it: string, en: string) => lang === "it" ? it : en;

  const sp = await searchParams;
  const companyParam = firstParam(sp.company);
  const companyId    = companyParam ? Number(companyParam) : undefined;
  const direction    = firstParam(sp.direction) as Direction | undefined;
  const dateFrom     = firstParam(sp.from);
  const dateTo       = firstParam(sp.to);
  const sort         = firstParam(sp.sort) ?? "transaction_date";
  const order        = (firstParam(sp.order) as "asc" | "desc") ?? "desc";
  const pageParam    = firstParam(sp.page);
  const page         = pageParam ? Number(pageParam) : 1;

  const [stats, companies, result] = await Promise.all([
    getDashboardStats(),
    getCompanies(),
    getTransactions({ companyId, direction, dateFrom, dateTo, sort, order, page, pageSize: 25 }),
  ]);

  const current = { company: companyId, direction, from: dateFrom, to: dateTo, sort, order, page };

  return (
    <div className="space-y-5">
      <MetricCards stats={stats} />

      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold tracking-tight text-[#E8EDF7]">
          {t("Operazioni Recenti", "Recent Transactions")}
        </h1>
        <span className="text-xs text-muted">
          {result.total.toLocaleString("it-IT")} {t("operazioni", "records")}
        </span>
      </div>

      <Filters
        companies={companies}
        current={{ company: companyId, direction, from: dateFrom, to: dateTo }}
      />

      <TransactionTable rows={result.rows} current={current} basePath="/" lang={lang} />

      <Pagination page={result.page} totalPages={result.totalPages} current={current} basePath="/" />
    </div>
  );
}
