export type ActionResult = { ok: true } | { ok: false; error: string };

export async function dispatchTransactionAction(
  id: number,
  action: string,
  params?: Record<string, unknown>
): Promise<ActionResult> {
  const isNote = action === "note";
  const url = isNote
    ? `/api/internal/transactions/${id}/note`
    : `/api/internal/transactions/${id}/review`;
  const body = isNote ? { note: params?.note } : { action, ...params };

  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Errore di rete" };
  }

  if (!res.ok) {
    const data = await res.json().catch(() => ({})) as { error?: string };
    return { ok: false, error: data.error ?? `HTTP ${res.status}` };
  }
  return { ok: true };
}

export async function dispatchFilingRetry(filingId: number): Promise<ActionResult> {
  let res: Response;
  try {
    res = await fetch(`/api/internal/filings/${filingId}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Errore di rete" };
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({})) as { error?: string };
    return { ok: false, error: data.error ?? `HTTP ${res.status}` };
  }
  return { ok: true };
}

export async function dispatchIssuerAction(
  issuerId: number,
  action: "resolve" | "reject",
  linkedIssuerId?: number
): Promise<ActionResult> {
  const body = action === "resolve"
    ? { action, issuer_id: linkedIssuerId }
    : { action };

  let res: Response;
  try {
    res = await fetch(`/api/internal/issuers/${issuerId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Errore di rete" };
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({})) as { error?: string };
    return { ok: false, error: data.error ?? `HTTP ${res.status}` };
  }
  return { ok: true };
}
