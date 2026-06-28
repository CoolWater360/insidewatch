import { ReactNode } from "react"

interface EmptyStateProps {
  title?: string
  description?: string
  actions?: ReactNode
}

export function EmptyState({
  title = "Nessun risultato",
  description,
  actions,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-white/[0.07] bg-white/[0.02] px-6 py-14 text-center">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-white/[0.05]">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} className="h-5 w-5 text-muted/50">
          <path strokeLinecap="round" strokeLinejoin="round" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
        </svg>
      </div>
      <p className="text-sm font-medium text-[#E8EDF7]">{title}</p>
      {description && <p className="mt-1 text-xs text-muted">{description}</p>}
      {actions && <div className="mt-4">{actions}</div>}
    </div>
  )
}

export function LoadingState({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-10 rounded-lg bg-white/[0.04] animate-pulse"
          style={{ opacity: 1 - i * 0.15 }}
        />
      ))}
    </div>
  )
}

export function ErrorState({
  message = "Si è verificato un errore durante il caricamento dei dati.",
}: {
  message?: string
}) {
  return (
    <div className="rounded-xl border border-sell/20 bg-sell/5 px-5 py-5 text-sm text-sell">
      {message}
    </div>
  )
}
