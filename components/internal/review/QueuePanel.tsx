import type { ReviewTransaction, ReviewFiling, UnmatchedIssuer } from "@/lib/review";
import { QUEUE_CATEGORIES, type QueueCategory, type CategoryCoverage } from "@/lib/review-categories";
import { formatCurrency, formatDate } from "@/lib/format";

const COVERAGE_INDICATOR: Record<CategoryCoverage, string> = {
  live:    "●",
  partial: "◑",
  planned: "○",
};

interface Props {
  groups: Record<QueueCategory, ReviewTransaction[]>;
  filings: ReviewFiling[];
  issuers: UnmatchedIssuer[];
  counts: Record<QueueCategory, number>;
  activeCategory: QueueCategory;
  onCategoryChange: (c: QueueCategory) => void;
  selectedId: number | null;
  selectedType: "transaction" | "filing" | "issuer";
  onSelect: (id: number, type: "transaction" | "filing" | "issuer") => void;
  issuerSearch: string;
  onIssuerSearchChange: (s: string) => void;
}

function confPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${Math.round(v * 100)}%`;
}

function DirectionDot({ direction }: { direction: string }) {
  const d = direction.toLowerCase();
  const cls =
    d === "buy"  ? "bg-buy" :
    d === "sell" ? "bg-sell" :
    "bg-muted/40";
  return <span className={`inline-block h-1.5 w-1.5 rounded-full shrink-0 ${cls}`} />;
}

export function QueuePanel({
  groups,
  filings,
  issuers,
  counts,
  activeCategory,
  onCategoryChange,
  selectedId,
  selectedType,
  onSelect,
  issuerSearch,
  onIssuerSearchChange,
}: Props) {
  // Derive visible items for the active category
  const txItems: ReviewTransaction[] =
    activeCategory !== "failed_filing" &&
    activeCategory !== "issuer_unmatched" &&
    activeCategory !== "relationship" &&
    activeCategory !== "fixture_candidate"
      ? groups[activeCategory].filter((tx) => {
          if (!issuerSearch) return true;
          const q = issuerSearch.toLowerCase();
          return (
            (tx.companies?.name ?? "").toLowerCase().includes(q) ||
            (tx.insiders?.full_name ?? "").toLowerCase().includes(q)
          );
        })
      : [];

  const total = Object.values(counts).reduce((a, b) => a + b, 0);

  return (
    <div className="flex w-64 shrink-0 flex-col overflow-hidden border-r border-white/[0.07]">
      {/* Header */}
      <div className="shrink-0 border-b border-white/[0.07] px-4 py-3">
        <div className="flex items-baseline justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted/60">
            Coda di Revisione
          </span>
          <span className="text-xs font-bold tabular-nums text-signal">{total}</span>
        </div>
      </div>

      {/* Category list */}
      <div className="shrink-0 border-b border-white/[0.07] py-1.5">
        {QUEUE_CATEGORIES.map((cat) => {
          const isActive  = cat.id === activeCategory;
          const isPlanned = cat.coverage === "planned";
          const count     = counts[cat.id];

          return (
            <button
              key={cat.id}
              type="button"
              disabled={isPlanned}
              onClick={() => !isPlanned && onCategoryChange(cat.id)}
              title={cat.description}
              className={`flex w-full items-center gap-2 px-4 py-1.5 text-left text-xs transition-colors ${
                isPlanned
                  ? "cursor-default text-muted/25"
                  : isActive
                  ? "bg-navy-800 text-[#E8EDF7]"
                  : "text-muted hover:bg-white/[0.04] hover:text-[#E8EDF7]"
              }`}
            >
              <span className={`shrink-0 text-[10px] ${
                isPlanned ? "text-muted/25" :
                cat.coverage === "partial" ? "text-amber-400/70" :
                isActive ? "text-brand-emerald" : "text-muted/40"
              }`}>
                {COVERAGE_INDICATOR[cat.coverage]}
              </span>
              <span className="flex-1 truncate">{cat.label}</span>
              <span className={`shrink-0 tabular-nums ${
                isPlanned ? "text-muted/20" :
                isActive  ? "text-[#E8EDF7]" : "text-muted/60"
              }`}>
                {isPlanned ? "–" : count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Search filter */}
      {activeCategory !== "relationship" && activeCategory !== "fixture_candidate" && (
        <div className="shrink-0 border-b border-white/[0.07] px-3 py-2">
          <input
            type="search"
            placeholder="Cerca società / insider…"
            value={issuerSearch}
            onChange={(e) => onIssuerSearchChange(e.target.value)}
            className="w-full rounded border border-white/[0.12] bg-navy-900/60 px-2 py-1 text-[11px] text-[#E8EDF7] placeholder:text-muted/40 focus:border-brand-blue/50 focus:outline-none"
          />
        </div>
      )}

      {/* Item list */}
      <div className="flex-1 overflow-y-auto">
        {activeCategory === "relationship" || activeCategory === "fixture_candidate" ? (
          <PlannedNotice />
        ) : activeCategory === "failed_filing" ? (
          filings.length === 0 ? (
            <EmptyNotice label="Nessun filing in errore" />
          ) : (
            filings
              .filter((f) => {
                if (!issuerSearch) return true;
                return (f.company_name ?? "").toLowerCase().includes(issuerSearch.toLowerCase());
              })
              .map((f) => (
                <FilingItem
                  key={f.id}
                  filing={f}
                  isSelected={selectedType === "filing" && selectedId === f.id}
                  onSelect={() => onSelect(f.id, "filing")}
                />
              ))
          )
        ) : activeCategory === "issuer_unmatched" ? (
          issuers.length === 0 ? (
            <EmptyNotice label="Nessun emittente non abbinato" />
          ) : (
            issuers
              .filter((u) => {
                if (!issuerSearch) return true;
                return (u.raw_name ?? "").toLowerCase().includes(issuerSearch.toLowerCase());
              })
              .map((u) => (
                <IssuerItem
                  key={u.id}
                  issuer={u}
                  isSelected={selectedType === "issuer" && selectedId === u.id}
                  onSelect={() => onSelect(u.id, "issuer")}
                />
              ))
          )
        ) : txItems.length === 0 ? (
          <EmptyNotice label="Nessun elemento in questa categoria" />
        ) : (
          txItems.map((tx) => (
            <TransactionItem
              key={tx.id}
              tx={tx}
              isSelected={selectedType === "transaction" && selectedId === tx.id}
              onSelect={() => onSelect(tx.id, "transaction")}
            />
          ))
        )}
      </div>
    </div>
  );
}

function TransactionItem({
  tx,
  isSelected,
  onSelect,
}: {
  tx: ReviewTransaction;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full flex-col gap-0.5 border-b border-white/[0.04] px-4 py-2.5 text-left transition-colors hover:bg-white/[0.04] ${
        isSelected ? "bg-navy-800/70 border-l-2 border-l-brand-blue" : ""
      }`}
    >
      <div className="flex items-center justify-between gap-1">
        <span className="truncate text-xs font-medium text-[#E8EDF7]">
          {tx.companies?.name ?? "—"}
        </span>
        <span className="shrink-0 text-[10px] tabular-nums text-muted/70">
          {confPct(tx.extraction_confidence)}
        </span>
      </div>
      <div className="flex items-center justify-between gap-1">
        <span className="truncate text-[11px] text-muted">
          {tx.insiders?.full_name ?? "—"}
          {tx.insiders?.role ? ` · ${tx.insiders.role}` : ""}
        </span>
        <DirectionDot direction={tx.direction} />
      </div>
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] text-muted/50">{formatDate(tx.transaction_date)}</span>
        <span className="text-[10px] tabular-nums text-muted/70">
          {formatCurrency(tx.total_value, tx.currency)}
        </span>
      </div>
    </button>
  );
}

function FilingItem({
  filing,
  isSelected,
  onSelect,
}: {
  filing: ReviewFiling;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full flex-col gap-0.5 border-b border-white/[0.04] px-4 py-2.5 text-left transition-colors hover:bg-white/[0.04] ${
        isSelected ? "bg-navy-800/70 border-l-2 border-l-brand-blue" : ""
      }`}
    >
      <div className="flex items-center justify-between gap-1">
        <span className="truncate text-xs font-medium text-[#E8EDF7]">
          {filing.company_name ?? `Filing #${filing.id}`}
        </span>
        <span className={`shrink-0 text-[10px] font-medium ${
          filing.status === "failed" ? "text-sell" : "text-muted/60"
        }`}>
          {filing.status}
        </span>
      </div>
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] text-muted/50">
          {filing.last_attempted_at
            ? formatDate(filing.last_attempted_at.split("T")[0])
            : formatDate(filing.filing_date)}
        </span>
        <span className="text-[10px] text-muted/50">
          {filing.attempt_count} tent.
        </span>
      </div>
    </button>
  );
}

function IssuerItem({
  issuer,
  isSelected,
  onSelect,
}: {
  issuer: UnmatchedIssuer;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full flex-col gap-0.5 border-b border-white/[0.04] px-4 py-2.5 text-left transition-colors hover:bg-white/[0.04] ${
        isSelected ? "bg-navy-800/70 border-l-2 border-l-brand-blue" : ""
      }`}
    >
      <span className="truncate text-xs font-medium text-[#E8EDF7]">{issuer.raw_name}</span>
      {issuer.raw_isin && (
        <span className="font-mono text-[10px] text-muted/60">{issuer.raw_isin}</span>
      )}
      <span className="text-[10px] text-muted/50">{formatDate(issuer.created_at.split("T")[0])}</span>
    </button>
  );
}

function EmptyNotice({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
      <span className="text-xs text-muted/40">{label}</span>
    </div>
  );
}

function PlannedNotice() {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 px-4 text-center">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-muted/30">
        In sviluppo
      </span>
      <span className="text-xs text-muted/40">
        Questa categoria è in sviluppo e non è ancora integrata con dati live.
      </span>
    </div>
  );
}
