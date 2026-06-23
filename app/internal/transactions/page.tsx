import { getTransactionsForReview } from "@/lib/review";
import { ReviewActions } from "@/components/internal/ReviewActions";

interface Props {
  searchParams: Promise<{ page?: string }>;
}

export default async function TransactionReviewPage({ searchParams }: Props) {
  const sp = await searchParams;
  const page = Math.max(1, parseInt(sp.page ?? "1", 10) || 1);
  const result = await getTransactionsForReview(page, 25);

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-[#E8EDF7]">
          Transactions — review queue
        </h1>
        <span className="text-sm text-muted">{result.total} pending</span>
      </div>

      {result.rows.length === 0 ? (
        <div className="rounded-lg border border-white/10 bg-navy-900/50 p-8 text-center text-muted">
          No transactions pending review.
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-white/10">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-xs uppercase tracking-wide text-muted">
                  <th className="px-4 py-2">Date</th>
                  <th className="px-4 py-2">Insider</th>
                  <th className="px-4 py-2">Company</th>
                  <th className="px-4 py-2">Dir</th>
                  <th className="px-4 py-2">Type</th>
                  <th className="px-4 py-2">Conf</th>
                  <th className="px-4 py-2">Rationale / Reason</th>
                  <th className="px-4 py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((tx) => (
                  <tr
                    key={tx.id}
                    className="border-b border-white/5 align-top transition-colors hover:bg-white/5"
                  >
                    <td className="px-4 py-3 tabular-nums text-muted">
                      {tx.transaction_date}
                    </td>
                    <td className="px-4 py-3">
                      {tx.insiders?.full_name ?? "—"}
                      {tx.insiders?.role && (
                        <div className="text-xs text-muted">{tx.insiders.role}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted">
                      {tx.companies?.name ?? "—"}
                      {tx.isin && (
                        <div className="font-mono text-xs">{tx.isin}</div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${
                          tx.direction === "buy"
                            ? "bg-emerald-900/50 text-emerald-300"
                            : tx.direction === "sell"
                            ? "bg-red-900/50 text-red-300"
                            : "bg-white/10 text-muted"
                        }`}
                      >
                        {tx.direction}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">
                      {tx.transaction_type ?? (
                        <span className="text-amber-400">unknown</span>
                      )}
                      {tx.classification_override && (
                        <div className="text-xs text-amber-400">overridden</div>
                      )}
                    </td>
                    <td className="px-4 py-3 tabular-nums">
                      {tx.extraction_confidence != null ? (
                        <span
                          className={
                            tx.extraction_confidence < 0.6
                              ? "text-red-400"
                              : tx.extraction_confidence < 0.8
                              ? "text-amber-400"
                              : "text-emerald-400"
                          }
                        >
                          {(tx.extraction_confidence * 100).toFixed(0)}%
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="max-w-xs px-4 py-3">
                      {tx.classification_rationale && (
                        <div className="font-mono text-xs text-muted">
                          {tx.classification_rationale}
                        </div>
                      )}
                      {tx.review_reason && (
                        <div className="mt-0.5 text-xs text-amber-300">
                          {tx.review_reason}
                        </div>
                      )}
                      {tx.raw_nature_text && (
                        <div className="mt-0.5 truncate text-xs text-muted italic">
                          "{tx.raw_nature_text}"
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <ReviewActions
                        transactionId={tx.id}
                        currentType={tx.transaction_type}
                        currentStatus={tx.review_status}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {result.totalPages > 1 && (
            <div className="flex items-center justify-between text-sm text-muted">
              <span>
                Page {result.page} of {result.totalPages}
              </span>
              <div className="flex gap-2">
                {result.page > 1 && (
                  <a
                    href={`/internal/transactions?page=${result.page - 1}`}
                    className="rounded px-3 py-1 transition-colors hover:bg-white/10 hover:text-[#E8EDF7]"
                  >
                    ← Previous
                  </a>
                )}
                {result.page < result.totalPages && (
                  <a
                    href={`/internal/transactions?page=${result.page + 1}`}
                    className="rounded px-3 py-1 transition-colors hover:bg-white/10 hover:text-[#E8EDF7]"
                  >
                    Next →
                  </a>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
