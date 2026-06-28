import { ReactNode } from "react"
import { PageHeader } from "./PageHeader"

type PlaceholderStatus = "planned" | "partial" | "normalizing"

interface PlaceholderPageProps {
  title: string
  subtitle?: string
  status: PlaceholderStatus
  description?: string
  actions?: ReactNode
}

const STATUS_LABELS: Record<PlaceholderStatus, string> = {
  planned:     "Fonte non ancora integrata",
  partial:     "Copertura parziale",
  normalizing: "Dati storici in normalizzazione",
}

const STATUS_COLORS: Record<PlaceholderStatus, string> = {
  planned:     "text-muted/60 border-white/[0.07] bg-white/[0.03]",
  partial:     "text-signal border-signal/20 bg-signal/5",
  normalizing: "text-brand-blue border-brand-blue/20 bg-brand-blue/5",
}

export function PlaceholderPage({
  title,
  subtitle,
  status,
  description,
  actions,
}: PlaceholderPageProps) {
  return (
    <div>
      <PageHeader title={title} subtitle={subtitle} actions={actions} />
      <div className="flex flex-col items-center justify-center rounded-xl border border-white/[0.07] bg-white/[0.02] px-6 py-16 text-center">
        <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-white/[0.05]">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} className="h-5 w-5 text-muted/50">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
        </div>
        <span
          className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${STATUS_COLORS[status]}`}
        >
          {STATUS_LABELS[status]}
        </span>
        {description && (
          <p className="mt-3 max-w-sm text-xs leading-relaxed text-muted">{description}</p>
        )}
      </div>
    </div>
  )
}
