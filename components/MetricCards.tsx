import { DashboardStats } from "@/lib/queries";
import { formatCurrency } from "@/lib/format";

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60)  return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
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
  const lastUpdated = stats.lastUpdatedAt ? timeAgo(stats.lastUpdatedAt) : null;

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card
          label="Total Transactions"
          value={stats.totalTransactions.toLocaleString("it-IT")}
          sub={`${stats.companiesTracked} companies tracked`}
          accent="blue"
        />
        <Card
          label="Filed Today"
          value={stats.todayCount === 0 ? "—" : String(stats.todayCount)}
          sub={stats.todayCount === 0 ? "No filings today" : "transactions"}
          accent="em"
        />
        <Card
          label="Buy Value Today"
          value={stats.todayBuyValue > 0 ? formatCurrency(stats.todayBuyValue) : "—"}
          sub="sum of buys"
          accent="buy"
        />
        <Card
          label="Sell Value Today"
          value={stats.todaySellValue > 0 ? formatCurrency(stats.todaySellValue) : "—"}
          sub="sum of sells"
          accent="sell"
        />
      </div>
      {lastUpdated && (
        <p className="text-right text-[11px] text-muted">
          Last updated {lastUpdated}
        </p>
      )}
    </div>
  );
}
