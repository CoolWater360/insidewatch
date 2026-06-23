import { getUnmatchedIssuers } from "@/lib/review";
import { ResolveIssuerForm } from "@/components/internal/ResolveIssuerForm";

interface Props {
  searchParams: Promise<{ page?: string }>;
}

export default async function UnmatchedIssuersPage({ searchParams }: Props) {
  const sp = await searchParams;
  const page = Math.max(1, parseInt(sp.page ?? "1", 10) || 1);
  const result = await getUnmatchedIssuers(page, 25);

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-[#E8EDF7]">
          Unmatched issuers
        </h1>
        <span className="text-sm text-muted">{result.total} pending</span>
      </div>

      <p className="text-sm text-muted">
        These company names were not found in the issuer master. Either link them to
        an existing issuer or reject if they are noise.
      </p>

      {result.rows.length === 0 ? (
        <div className="rounded-lg border border-white/10 bg-navy-900/50 p-8 text-center text-muted">
          No unmatched issuers.
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {result.rows.map((issuer) => (
              <div
                key={issuer.id}
                className="rounded-lg border border-white/10 bg-navy-900/50 p-4"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <div className="font-medium text-[#E8EDF7]">
                      {issuer.raw_name}
                    </div>
                    {issuer.raw_isin && (
                      <div className="font-mono text-xs text-muted">
                        ISIN: {issuer.raw_isin}
                      </div>
                    )}
                    {issuer.suggestion_issuer_id && (
                      <div className="text-xs text-amber-300">
                        Suggestion: issuer #{issuer.suggestion_issuer_id}
                      </div>
                    )}
                    <div className="text-xs text-muted">
                      Added{" "}
                      {new Date(issuer.created_at).toLocaleDateString("it-IT")}
                    </div>
                  </div>
                  <ResolveIssuerForm
                    unmatchedId={issuer.id}
                    suggestedIssuerId={issuer.suggestion_issuer_id ?? undefined}
                  />
                </div>
              </div>
            ))}
          </div>

          {result.totalPages > 1 && (
            <div className="flex items-center justify-between text-sm text-muted">
              <span>
                Page {result.page} of {result.totalPages}
              </span>
              <div className="flex gap-2">
                {result.page > 1 && (
                  <a
                    href={`/internal/issuers?page=${result.page - 1}`}
                    className="rounded px-3 py-1 transition-colors hover:bg-white/10 hover:text-[#E8EDF7]"
                  >
                    ← Previous
                  </a>
                )}
                {result.page < result.totalPages && (
                  <a
                    href={`/internal/issuers?page=${result.page + 1}`}
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
