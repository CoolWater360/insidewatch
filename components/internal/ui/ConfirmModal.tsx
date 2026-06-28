"use client"

import { ReactNode, useEffect } from "react"

type Variant = "primary" | "destructive"

interface ConfirmModalProps {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  title: string
  description?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  variant?: Variant
  loading?: boolean
}

export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = "Conferma",
  cancelLabel = "Annulla",
  variant = "primary",
  loading = false,
}: ConfirmModalProps) {
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [open, onClose])

  if (!open) return null

  const confirmCls =
    variant === "destructive"
      ? "bg-sell/90 text-white hover:bg-sell"
      : "bg-brand-blue text-white hover:opacity-90"

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/60" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal
        aria-labelledby="confirm-modal-title"
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="w-full max-w-sm rounded-xl border border-white/[0.07] bg-navy-800 p-6 shadow-2xl">
          <h2 id="confirm-modal-title" className="text-sm font-semibold text-[#E8EDF7]">
            {title}
          </h2>
          {description && (
            <div className="mt-2 text-xs leading-relaxed text-muted">{description}</div>
          )}
          <div className="mt-5 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="rounded-md border border-white/[0.10] px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:bg-white/[0.05] hover:text-[#E8EDF7] disabled:opacity-50"
            >
              {cancelLabel}
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={loading}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-opacity disabled:opacity-60 ${confirmCls}`}
            >
              {loading ? "…" : confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
