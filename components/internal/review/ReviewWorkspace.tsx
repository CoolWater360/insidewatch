"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import type { ExtendedReviewTransaction, ReviewFiling, UnmatchedIssuer } from "@/lib/review";
import {
  groupTransactions,
  countsByCategory,
  type QueueCategory,
} from "@/lib/review-categories";
import {
  dispatchTransactionAction,
  dispatchFilingRetry,
  dispatchIssuerAction,
} from "@/lib/review-mutations";
import { QueuePanel } from "./QueuePanel";
import { DetailPanel } from "./DetailPanel";
import { EvidencePanel } from "./EvidencePanel";

type SelectionType = "transaction" | "filing" | "issuer";

interface Props {
  transactions: ExtendedReviewTransaction[];
  filings: ReviewFiling[];
  issuers: UnmatchedIssuer[];
}

export function ReviewWorkspace({ transactions, filings, issuers }: Props) {
  const router = useRouter();

  // ── Left-pane state ───────────────────────────────────────────────────────
  const [activeCategory, setActiveCategory] = useState<QueueCategory>("low_confidence");
  const [issuerSearch, setIssuerSearch]     = useState("");

  // ── Selection state ───────────────────────────────────────────────────────
  const [selectedId,   setSelectedId]   = useState<number | null>(null);
  const [selectedType, setSelectedType] = useState<SelectionType>("transaction");

  // ── Mutation state ────────────────────────────────────────────────────────
  const [busy,        setBusy]        = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // ── Derived data ──────────────────────────────────────────────────────────
  const groups = groupTransactions(transactions);
  const counts = countsByCategory(groups, filings, issuers);

  const selectedTx = selectedType === "transaction" && selectedId != null
    ? (transactions.find((t) => t.id === selectedId) ?? null)
    : null;

  const selectedFiling = selectedType === "filing" && selectedId != null
    ? (filings.find((f) => f.id === selectedId) ?? null)
    : null;

  const selectedIssuer = selectedType === "issuer" && selectedId != null
    ? (issuers.find((u) => u.id === selectedId) ?? null)
    : null;

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleSelect = useCallback((id: number, type: SelectionType) => {
    setSelectedId(id);
    setSelectedType(type);
    setActionError(null);
  }, []);

  const handleCategoryChange = useCallback((cat: QueueCategory) => {
    setActiveCategory(cat);
    setSelectedId(null);
    setActionError(null);
  }, []);

  const handleAction = useCallback(async (
    action: string,
    params?: Record<string, unknown>
  ) => {
    if (selectedId == null) return;
    setBusy(true);
    setActionError(null);

    let result: { ok: boolean; error?: string };

    if (selectedType === "transaction") {
      result = await dispatchTransactionAction(selectedId, action, params);
    } else if (selectedType === "filing") {
      result = await dispatchFilingRetry(selectedId);
    } else {
      // issuer — action is "resolve" or "reject" passed via params
      const issuerAction = (params?.action ?? action) as "resolve" | "reject";
      const linkedId = typeof params?.issuer_id === "number"
        ? params.issuer_id
        : undefined;
      result = await dispatchIssuerAction(selectedId, issuerAction, linkedId);
    }

    if (!result.ok) {
      setActionError(result.error ?? "Errore sconosciuto");
    } else {
      setSelectedId(null);
      router.refresh();
    }

    setBusy(false);
  }, [selectedId, selectedType, router]);

  return (
    // Break out of the parent px-6 py-6 padding and fill available viewport
    <div className="-mx-6 -my-6 flex overflow-hidden h-[calc(100vh-2.5rem)]">
      <QueuePanel
        groups={groups}
        filings={filings}
        issuers={issuers}
        counts={counts}
        activeCategory={activeCategory}
        onCategoryChange={handleCategoryChange}
        selectedId={selectedId}
        selectedType={selectedType}
        onSelect={handleSelect}
        issuerSearch={issuerSearch}
        onIssuerSearchChange={setIssuerSearch}
      />

      <DetailPanel
        selectedTx={selectedTx}
        selectedFiling={selectedFiling}
        selectedIssuer={selectedIssuer}
        onAction={handleAction}
        busy={busy}
        actionError={actionError}
      />

      <EvidencePanel
        selectedTx={selectedTx}
        selectedFiling={selectedFiling}
        selectedIssuer={selectedIssuer}
      />
    </div>
  );
}
