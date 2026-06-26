import { getFailedFilings } from "@/lib/review";
import { RetryFilingButton } from "@/components/internal/RetryFilingButton";

interface Props {
  searchParams: Promise<{ page?: string }>;
}

export default async function FailedFilingsPage({ searchParams }: Props) {
  const sp = await searchParams;
  const page = Math.max(1, parseInt(sp.page ?? "1", 10) || 1);
  const result = await getFailedFilings(page, 25);

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-[#E8EDF7]">
          Failed &amp; skipped filings
        </h1>
        <span className="text-sm text-muted">{result.total} total</span>
      </div>

      {result.rows.length === 0 ? (
        <div className="rounded-lg border border-white/10 bg-navy-900/50 p-8 text-center text-muted">
          No failed filings.
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-white/10">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-xs uppercase tracking-wide text-muted">
                  <th className="px-4 py-2">Filing</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Attempts</th>
                  <th className="px-4 py-2">Last tried</th>
                  <th className="px-4 py-2">Error</th>
                  <th className="px-4 py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((filing) => (
                  <tr
                    key={filing.id}
                    className="border-b border-white/5 align-top transition-colors hover:bg-white/5"
                  >
                    <td className="px-4 py-3">
                      <div className="font-medium">
                        {filing.company_name ?? "Unknown company"}
                      </div>
                      <div className="text-xs text-muted">
                        {filing.filing_date ?? "—"}
                      </div>
                      <a
                        href={filing.pdf_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="block max-w-xs truncate font-mono text-xs text-brand-blue hover:underline"
                      >
                        {filing.pdf_url}
                      </a>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${
                          filing.status === "failed"
                            ? "bg-red-900/50 text-red-300"
                            : "bg-white/10 text-muted"
                        }`}
                      >
                        {filing.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 tabular-nums text-muted">
                      {filing.attempt_count}
                    </td>
                    <td className="px-4 py-3 tabular-nums text-muted">
                      {filing.last_attempted_at
                        ? new Date(filing.last_attempted_at).toLocaleString(
                            "it-IT",
                            { dateStyle: "short", timeStyle: "short" }
                          )
                        : "—"}
                    </td>
                    <td className="max-w-xs px-4 py-3">
                      {filing.last_error ? (
                        <div className="max-w-xs overflow-hidden rounded bg-red-950/30 px-2 py-1 font-mono text-xs text-red-300">
                          {filing.last_error.slice(0, 200)}
                          {filing.last_error.length > 200 && "…"}
                        </div>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <RetryFilingButton filingId={filing.id} status={filing.status} />
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
                    href={`/internal/filings?page=${result.page - 1}`}
                    className="rounded px-3 py-1 transition-colors hover:bg-white/10 hover:text-[#E8EDF7]"
                  >
                    ← Previous
                  </a>
                )}
                {result.page < result.totalPages && (
                  <a
                    href={`/internal/filings?page=${result.page + 1}`}
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
