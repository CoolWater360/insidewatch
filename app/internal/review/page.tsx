import { ReviewWorkspace } from "@/components/internal/review/ReviewWorkspace";
import { getWorkspaceTransactions, getFailedFilings, getUnmatchedIssuers } from "@/lib/review";

export const dynamic = "force-dynamic";

export default async function ReviewQueuePage() {
  const [transactions, filingsResult, issuersResult] = await Promise.all([
    getWorkspaceTransactions(200),
    getFailedFilings(1, 100),
    getUnmatchedIssuers(1, 100),
  ]);

  return (
    <ReviewWorkspace
      transactions={transactions}
      filings={filingsResult.rows}
      issuers={issuersResult.rows}
    />
  );
}
