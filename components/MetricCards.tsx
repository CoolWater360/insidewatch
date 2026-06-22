"use client";

import { DashboardStats } from "@/lib/queries";
import { formatCurrency } from "@/lib/format";
import { useT, useLanguage } from "./LanguageProvider";

function timeAgo(iso: string, lang: "it" | "en"): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (lang === "it") {
    if (diff < 60)    return `${diff}s fa`;
    if (diff < 3600)  return `${Math.floor(diff / 60)}m fa`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h fa`;
    return `${Math.floor(diff / 86400)}g fa`;
  }
  if (diff < 60)    return `${diff}s ago`;
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

interface CardProps {
  label: string;
  value: string;
  sub?: string;
  accent: "blue" | "buy" | "sell" | "em";
}

function Card({ label, value, sub, accent }: CardProps) {
  const accentClass = {
    blue: "metric-accent-blue",
    buy:  "metric-accent-buy",
    sell: "metric-accent-sell",
    em:   "metric-accent-em",
  }[accent];

  return (
    <div className={`glass-card rounded-xl p-4 ${accentClass} animate-fade-up`}>
      <p className="text-xs font-medium uppercase tracking-widest text-muted">{label}</p>
      <p className="mt-2 text-2xl font-semibold tabular-nums tracking-tight text-[#E8EDF7]">
        {value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </div>
  );
}

export function MetricCards({ stats }: { stats: DashboardStats }) {
  const t = useT();
  const { lang } = useLanguage();
  const lastUpdated = stats.lastUpdatedAt ? timeAgo(stats.lastUpdatedAt, lang) : null;

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card
          label={t("Operazioni Totali", "Total Transactions")}
          value={stats.totalTransactions.toLocaleString("it-IT")}
          sub={`${stats.companiesTracked} ${t("società monitorate", "companies tracked")}`}
          accent="blue"
        />
        <Card
          label={t("Archiviate questa settimana", "Filed This Week")}
          value={stats.weekCount === 0 ? "—" : String(stats.weekCount)}
          sub={stats.weekCount === 0
            ? t("Nessuna operazione", "No filings this week")
            : t("operazioni", "transactions")}
          accent="em"
        />
        <Card
          label={t("Valore Acquisti · 7g", "Buy Value · 7d")}
          value={stats.weekBuyValue > 0 ? formatCurrency(stats.weekBuyValue) : "—"}
          sub={t("totale acquisti", "sum of buys")}
          accent="buy"
        />
        <Card
          label={t("Valore Vendite · 7g", "Sell Value · 7d")}
          value={stats.weekSellValue > 0 ? formatCurrency(stats.weekSellValue) : "—"}
          sub={t("totale vendite", "sum of sells")}
          accent="sell"
        />
      </div>
      {lastUpdated && (
        <p className="text-right text-[11px] text-muted">
          {t("Ultimo aggiornamento", "Last updated")} {lastUpdated}
        </p>
      )}
    </div>
  );
}
