import Link from "next/link";
import { notFound } from "next/navigation";
import { getFilingById } from "@/lib/review";

interface Props {
  params: Promise<{ id: string }>;
}

function StatusBadge({ status }: { status: string }) {
  const colours: Record<string, string> = {
    completed:   "bg-emerald-900/50 text-emerald-300",
    failed:      "bg-red-900/50 text-red-300",
    skipped:     "bg-orange-900/50 text-orange-300",
    in_progress: "bg-blue-900/50 text-blue-300",
    pending:     "bg-white/10 text-muted",
  };
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${colours[status] ?? "bg-white/10 text-muted"}`}>
      {status}
    </span>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <tr className="border-b border-white/5">
      <td className="py-2 pr-6 text-xs uppercase tracking-wide text-muted w-44">{label}</td>
      <td className="py-2 text-sm">{children}</td>
    </tr>
  );
}

function fmtBytes(n: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export default async function FilingDetailPage({ params }: Props) {
  const { id } = await params;
  const filingId = parseInt(id, 10);
  if (!filingId || isNaN(filingId)) notFound();

  const filing = await getFilingById(filingId);
  if (!filing) notFound();

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-[#E8EDF7]">
          Filing #{filing.id}
          {filing.company_name && (
            <span className="ml-2 text-base font-normal text-muted">— {filing.company_name}</span>
          )}
        </h1>
        <Link
          href="/internal/transactions"
          className="text-xs text-muted hover:text-[#E8EDF7] transition-colors"
        >
          ← Back to transactions
        </Link>
      </div>

      <div className="rounded-lg border border-white/10 bg-navy-900/40 px-6 py-4">
        <table className="w-full">
          <tbody>
            <Row label="Status"><StatusBadge status={filing.status} /></Row>
            <Row label="Filing date">{filing.filing_date ?? <span className="text-muted">—</span>}</Row>
            <Row label="Company">{filing.company_name ?? <span className="text-muted">—</span>}</Row>
            <Row label="Original PDF URL">
              <a
                href={filing.pdf_url}
                target="_blank"
                rel="noopener noreferrer"
                className="break-all text-blue-400 hover:text-blue-300 hover:underline"
              >
                {filing.pdf_url} ↗
              </a>
            </Row>
            <Row label="Transactions inserted">
              <span className="tabular-nums">{filing.transactions_inserted}</span>
            </Row>
            <Row label="Attempts">
              <span className="tabular-nums">{filing.attempt_count}</span>
              {filing.last_attempted_at && (
                <span className="ml-2 text-xs text-muted">last: {filing.last_attempted_at}</span>
              )}
            </Row>
            <Row label="Scraper version">{filing.scraper_version ?? <span className="text-muted">—</span>}</Row>
            <Row label="SHA-256">
              {filing.pdf_sha256
                ? <span className="font-mono text-xs break-all">{filing.pdf_sha256}</span>
                : <span className="text-muted">—</span>}
            </Row>
            <Row label="Stored backup">
              {filing.has_stored_document
                ? <span className="text-emerald-400">Yes — {fmtBytes(filing.file_size_bytes)}</span>
                : <span className="text-muted">No</span>}
            </Row>
            {filing.last_error && (
              <Row label="Last error">
                <span className="font-mono text-xs text-red-400 break-all">{filing.last_error}</span>
              </Row>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
