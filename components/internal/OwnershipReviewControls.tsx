"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

type Kind = "entity" | "event" | "relationship";

interface BaseProps {
  kind: Kind;
  id: number;
}

interface EntityProps extends BaseProps {
  kind: "entity";
  /** When set, offers a one-click "approve as <proposedType>" button. */
  proposedType?: string | null;
}

type Props = EntityProps | BaseProps;

const ENTITY_TYPES = [
  "natural_person", "company", "holding_company", "trust",
  "fiduciary", "foundation", "fund", "nominee", "other",
];

async function post(body: Record<string, unknown>): Promise<string | null> {
  try {
    const res = await fetch("/api/internal/ownership-review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const j = (await res.json().catch(() => ({}))) as { error?: string };
      return j.error ?? `HTTP ${res.status}`;
    }
    return null;
  } catch (e) {
    return e instanceof Error ? e.message : "Network error";
  }
}

export function OwnershipReviewControls(props: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const proposedType =
    props.kind === "entity" ? (props as EntityProps).proposedType ?? null : null;

  async function run(body: Record<string, unknown>, label: string) {
    setBusy(true);
    setError(null);
    const err = await post({ kind: props.kind, id: props.id, ...body });
    setBusy(false);
    if (err) {
      setError(err);
    } else {
      setDone(label);
      router.refresh();
    }
  }

  if (done) {
    return <span className="text-[11px] font-medium text-brand-emerald">✓ {done}</span>;
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex flex-wrap items-center justify-end gap-1.5">
        {proposedType && (
          <button
            disabled={busy}
            onClick={() =>
              run(
                { action: "set_type", entity_type: proposedType },
                `set ${proposedType} + confirmed`
              )
            }
            className="rounded bg-brand-blue/20 px-2 py-1 text-[11px] font-medium text-brand-blue transition-colors hover:bg-brand-blue/30 disabled:opacity-50"
          >
            {busy ? "…" : `Approve as ${proposedType}`}
          </button>
        )}
        <button
          disabled={busy}
          onClick={() => run({ action: "approve" }, "approved")}
          className="rounded bg-brand-emerald/15 px-2 py-1 text-[11px] font-medium text-brand-emerald transition-colors hover:bg-brand-emerald/25 disabled:opacity-50"
        >
          Approve
        </button>
        <button
          disabled={busy}
          onClick={() => run({ action: "reject" }, "rejected")}
          className="rounded bg-sell/15 px-2 py-1 text-[11px] font-medium text-sell transition-colors hover:bg-sell/25 disabled:opacity-50"
        >
          Reject
        </button>
      </div>
      {props.kind === "entity" && (
        <details className="text-[10px] text-muted/60">
          <summary className="cursor-pointer select-none hover:text-muted">
            set specific type…
          </summary>
          <div className="mt-1 flex flex-wrap justify-end gap-1">
            {ENTITY_TYPES.map((t) => (
              <button
                key={t}
                disabled={busy}
                onClick={() =>
                  run({ action: "set_type", entity_type: t }, `set ${t} + confirmed`)
                }
                className="rounded border border-white/10 px-1.5 py-0.5 text-[10px] text-muted/70 hover:bg-white/[0.05] disabled:opacity-50"
              >
                {t}
              </button>
            ))}
          </div>
        </details>
      )}
      {error && <div className="text-[10px] text-sell">{error}</div>}
    </div>
  );
}
