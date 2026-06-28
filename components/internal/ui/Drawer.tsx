"use client"

import { ReactNode, useEffect } from "react"

interface DrawerProps {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: ReactNode
  width?: string
}

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  children,
  width = "w-96",
}: DrawerProps) {
  // Close on Escape
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [open, onClose])

  if (!open) return null

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/50"
        onClick={onClose}
        aria-hidden
      />
      {/* Panel */}
      <aside
        className={`fixed inset-y-0 right-0 z-50 flex ${width} flex-col border-l border-white/[0.07] bg-navy-900 shadow-xl`}
      >
        {/* Header */}
        <div className="flex items-start justify-between border-b border-white/[0.07] px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-[#E8EDF7]">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-4 mt-0.5 shrink-0 rounded p-1 text-muted/60 transition-colors hover:bg-white/[0.05] hover:text-[#E8EDF7]"
            aria-label="Chiudi"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-4 w-4">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </aside>
    </>
  )
}
