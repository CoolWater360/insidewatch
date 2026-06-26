import Link from "next/link";
import { getReviewQueueCounts, getTransactionsForReview } from "@/lib/review";
import { isSupabaseConfigured } from "@/lib/supabase";

function QueueCard({
  href,
  label,
  count,
  description,
  urgent,
}: {
  href: string;
  label: string;
  count: number;
  description: string;
  urgent?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`block rounded-lg border p-6 transition-colors hover:bg-white/5 ${
        urgent && count > 0
          ? "border-amber-500/50 bg-amber-950/20"
          : "border-white/10 bg-navy-900/50"
      }`}
    >
      <div
        className={`text-3xl font-bold tabular-nums ${
          urgent && count > 0 ? "text-amber-400" : "text-[#E8EDF7]"
        }`}
      >
        {count}
      </div>
      <div className="mt-1 text-sm font-semibold text-[#E8EDF7]">{label}</div>
      <div className="mt-1 text-xs text-muted">{description}</div>
    </Link>
  );
}

export default async function InternalDashboard() {
  if (!isSupabaseConfigured) {
    return (
      <div className="rounded-lg border border-white/10 bg-navy-900/50 p-8 text-center text-muted">
        Supabase not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.
      </div>
    );
  }

  let counts = { pendingTransactions: 0, failedFilings: 0, skippedFilings: 0, pendingIssuers: 0 };
  let recentTx = { rows: [] as Awaited<ReturnType<typeof getTransactionsForReview>>["rows"] };

  try {
    [counts, recentTx] = await Promise.all([
      getReviewQueueCounts(),
      getTransactionsForReview(1, 10),
    ]);
  } catch {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-950/20 p-8 text-center text-red-300">
        Could not load review queue. Check SUPABASE_SERVICE_ROLE_KEY is set.
      </div>
    );
  }

  const total =
    counts.pendingTransactions + counts.failedFilings + counts.skippedFilings + counts.pendingIssuers;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold text-[#E8EDF7]">Review Queue</h1>
        <p className="mt-1 text-sm text-muted">
          {total === 0
            ? "All queues clear."
            : `${total} item${total !== 1 ? "s" : ""} need attention.`}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <QueueCard
          href="/internal/transactions"
          label="Transactions to review"
          count={counts.pendingTransactions}
          description="Low confidence or unclassified extractions"
          urgent
        />
        <QueueCard
          href="/internal/filings"
          label="Failed filings"
          count={counts.failedFilings}
          description="Download or parse errors — eligible for retry"
          urgent
        />
        <QueueCard
          href="/internal/filings"
          label="Skipped filings"
          count={counts.skippedFilings}
          description="Max retries reached — requires manual reset"
        />
        <QueueCard
          href="/internal/issuers"
          label="Unmatched issuers"
          count={counts.pendingIssuers}
          description="Company names not found in issuer master"
        />
      </div>

      {recentTx.rows.length > 0 && (
        <div>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted">
            Lowest-confidence transactions
          </h2>
          <div className="overflow-x-auto rounded-lg border border-white/10">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-xs text-muted">
                  <th className="px-4 py-2">Date</th>
                  <th className="px-4 py-2">Insider</th>
                  <th className="px-4 py-2">Company</th>
                  <th className="px-4 py-2">Type</th>
                  <th className="px-4 py-2">Confidence</th>
                  <th className="px-4 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {recentTx.rows.map((tx) => (
                  <tr
                    key={tx.id}
                    className="border-b border-white/5 transition-colors hover:bg-white/5"
                  >
                    <td className="px-4 py-2 tabular-nums text-muted">
                      {tx.transaction_date}
                    </td>
                    <td className="px-4 py-2">
                      {tx.insiders?.full_name ?? "—"}
                    </td>
                    <td className="px-4 py-2 text-muted">
                      {tx.companies?.name ?? "—"}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {tx.transaction_type ?? "—"}
                    </td>
                    <td className="px-4 py-2 tabular-nums">
                      {tx.extraction_confidence != null
                        ? (tx.extraction_confidence * 100).toFixed(0) + "%"
                        : "—"}
                    </td>
                    <td className="max-w-xs truncate px-4 py-2 text-xs text-muted">
                      {tx.review_reason ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2 text-right">
            <Link
              href="/internal/transactions"
              className="text-xs text-brand-blue hover:underline"
            >
              View all {counts.pendingTransactions} →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
