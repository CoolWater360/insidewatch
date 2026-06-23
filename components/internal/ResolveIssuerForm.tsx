"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

interface Props {
  unmatchedId: number;
  suggestedIssuerId?: number;
}

export function ResolveIssuerForm({ unmatchedId, suggestedIssuerId }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [issuerId, setIssuerId] = useState(
    suggestedIssuerId ? String(suggestedIssuerId) : ""
  );
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<"resolved" | "rejected" | null>(null);

  async function post(action: "resolve" | "reject"): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/internal/issuers/${unmatchedId}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          action === "resolve"
            ? { action, issuer_id: parseInt(issuerId, 10) }
            : { action }
        ),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError((body as { error?: string }).error ?? `HTTP ${res.status}`);
      } else {
        setDone(action === "resolve" ? "resolved" : "rejected");
        router.refresh();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <span
        className={`text-xs capitalize ${
          done === "resolved" ? "text-emerald-400" : "text-muted"
        }`}
      >
        {done}
      </span>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-1">
        <input
          type="number"
          placeholder="Issuer ID"
          value={issuerId}
          onChange={(e) => setIssuerId(e.target.value)}
          className="w-24 rounded border border-white/20 bg-navy-900 px-2 py-1 text-xs text-[#E8EDF7] placeholder:text-muted"
        />
        <button
          onClick={() => post("resolve")}
          disabled={busy || !issuerId.trim()}
          className="rounded bg-emerald-900/60 px-2 py-1 text-xs font-medium text-emerald-300 transition-colors hover:bg-emerald-800/60 disabled:opacity-50"
        >
          Link
        </button>
        <button
          onClick={() => post("reject")}
          disabled={busy}
          className="rounded bg-white/10 px-2 py-1 text-xs text-muted transition-colors hover:bg-white/20 disabled:opacity-50"
        >
          Reject
        </button>
      </div>
      {error && <div className="text-xs text-red-400">{error}</div>}
    </div>
  );
}
