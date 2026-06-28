// Reusable status and provenance badge components for internal views.
// These complement the global .badge-buy / .badge-sell CSS classes and add
// structured typing for the values that appear throughout the console.

type DirectionBadgeProps = { direction: string }
type FilingStatusBadgeProps = { status: string }
type ReviewBadgeProps = { status: string }
type ConfidenceBadgeProps = { score: number | null }
type ConfidenceLevelBadgeProps = { level: string }

export function DirectionBadge({ direction }: DirectionBadgeProps) {
  const dir = direction?.toLowerCase()
  if (dir === "buy")  return <span className="badge-buy">Acquisto</span>
  if (dir === "sell") return <span className="badge-sell">Vendita</span>
  return <span className="badge-other capitalize">{direction ?? "—"}</span>
}

export function FilingStatusBadge({ status }: FilingStatusBadgeProps) {
  const s = status?.toLowerCase()
  const variants: Record<string, string> = {
    completed:   "text-buy   border-buy/30   bg-buy/10",
    processed:   "text-buy   border-buy/30   bg-buy/10",
    failed:      "text-sell  border-sell/30  bg-sell/10",
    skipped:     "text-muted border-white/10 bg-white/5",
    pending:     "text-signal border-signal/30 bg-signal/10",
    processing:  "text-brand-blue border-brand-blue/30 bg-brand-blue/10",
  }
  const cls = variants[s] ?? "text-muted border-white/10 bg-white/5"
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${cls}`}>
      {status}
    </span>
  )
}

export function ReviewBadge({ status }: ReviewBadgeProps) {
  const s = status?.toLowerCase()
  const variants: Record<string, string> = {
    confirmed:      "text-buy    border-buy/30   bg-buy/10",
    rejected:       "text-sell   border-sell/30  bg-sell/10",
    pending_review: "text-signal border-signal/30 bg-signal/10",
    under_review:   "text-brand-blue border-brand-blue/30 bg-brand-blue/10",
  }
  const labels: Record<string, string> = {
    confirmed:      "Confermato",
    rejected:       "Rigettato",
    pending_review: "In revisione",
    under_review:   "In lavorazione",
  }
  const cls = variants[s] ?? "text-muted border-white/10 bg-white/5"
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${cls}`}>
      {labels[s] ?? status}
    </span>
  )
}

export function ConfidenceBadge({ score }: ConfidenceBadgeProps) {
  if (score == null) return <span className="text-xs text-muted/60">—</span>
  const pct = Math.round(score * 100)
  const cls =
    pct >= 80 ? "text-buy"
    : pct >= 50 ? "text-signal"
    : "text-sell"
  return <span className={`text-xs font-semibold tabular-nums ${cls}`}>{pct}%</span>
}

export function ConfidenceLevelBadge({ level }: ConfidenceLevelBadgeProps) {
  const labels: Record<string, string> = {
    parsed_fact:         "Fatto estratto",
    heuristic_suggestion: "Euristica",
    reviewer_confirmed:  "Confermato",
  }
  const cls =
    level === "reviewer_confirmed" ? "text-buy   border-buy/30   bg-buy/10"
    : level === "parsed_fact"      ? "text-brand-blue border-brand-blue/30 bg-brand-blue/10"
    :                                "text-signal border-signal/30 bg-signal/10"
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${cls}`}>
      {labels[level] ?? level}
    </span>
  )
}
