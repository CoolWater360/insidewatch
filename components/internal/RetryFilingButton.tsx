"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

interface Props {
  filingId: number;
  status: string;
}

export function RetryFilingButton({ filingId, status }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function retry() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/internal/filings/${filingId}/retry`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError((body as { error?: string }).error ?? `HTTP ${res.status}`);
      } else {
        setDone(true);
        router.refresh();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return <span className="text-xs text-emerald-400">Queued for retry</span>;
  }

  return (
    <div className="flex flex-col gap-1">
      <button
        onClick={retry}
        disabled={busy || status === "completed"}
        className="rounded bg-blue-900/50 px-2 py-1 text-xs font-medium text-blue-300 transition-colors hover:bg-blue-800/50 disabled:opacity-50"
      >
        {busy ? "…" : "Retry"}
      </button>
      {error && <div className="text-xs text-red-400">{error}</div>}
    </div>
  );
}
