"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const ALL_TYPES = [
  "buy", "sell", "grant", "option_exercise", "sell_to_cover",
  "subscription", "conversion", "inheritance",
  "gift_in", "gift_out", "transfer_in", "transfer_out",
  "pledge_or_security", "derivative_transaction",
  "other", "unknown",
] as const;

interface Props {
  transactionId: number;
  currentType: string | null;
  currentStatus: string | null;
}

export function ReviewActions({ transactionId, currentType, currentStatus }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [showClassify, setShowClassify] = useState(false);
  const [newType, setNewType] = useState(currentType ?? "buy");
  const [rationale, setRationale] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function post(action: string, extra?: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/internal/transactions/${transactionId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...extra }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError((body as { error?: string }).error ?? `HTTP ${res.status}`);
      } else {
        router.refresh();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setBusy(false);
    }
  }

  if (currentStatus === "confirmed" || currentStatus === "rejected") {
    return (
      <span className="text-xs text-muted capitalize">{currentStatus}</span>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-1">
        <button
          onClick={() => post("confirm")}
          disabled={busy}
          className="rounded bg-emerald-900/60 px-2 py-1 text-xs font-medium text-emerald-300 transition-colors hover:bg-emerald-800/60 disabled:opacity-50"
        >
          Confirm
        </button>
        <button
          onClick={() => post("reject")}
          disabled={busy}
          className="rounded bg-red-900/40 px-2 py-1 text-xs font-medium text-red-300 transition-colors hover:bg-red-800/40 disabled:opacity-50"
        >
          Reject
        </button>
        <button
          onClick={() => setShowClassify((v) => !v)}
          disabled={busy}
          className="rounded bg-amber-900/40 px-2 py-1 text-xs font-medium text-amber-300 transition-colors hover:bg-amber-800/40 disabled:opacity-50"
        >
          Reclassify
        </button>
      </div>

      {showClassify && (
        <div className="flex flex-col gap-1 rounded border border-amber-500/30 bg-amber-950/20 p-2">
          <select
            value={newType}
            onChange={(e) => setNewType(e.target.value)}
            className="rounded border border-white/20 bg-navy-900 px-2 py-1 text-xs text-[#E8EDF7]"
          >
            {ALL_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder="Rationale (required)"
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            className="rounded border border-white/20 bg-navy-900 px-2 py-1 text-xs text-[#E8EDF7] placeholder:text-muted"
          />
          <button
            onClick={() =>
              post("classify", { transaction_type: newType, rationale })
            }
            disabled={busy || !rationale.trim()}
            className="rounded bg-amber-700/60 px-2 py-1 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-600/60 disabled:opacity-50"
          >
            Apply
          </button>
        </div>
      )}

      {error && <div className="text-xs text-red-400">{error}</div>}
    </div>
  );
}
