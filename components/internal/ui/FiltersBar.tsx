"use client"

import { ReactNode } from "react"

interface FiltersBarProps {
  children: ReactNode
  className?: string
}

export function FiltersBar({ children, className = "" }: FiltersBarProps) {
  return (
    <div
      className={`mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2 ${className}`}
    >
      {children}
    </div>
  )
}

interface FilterSelectProps {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
}

export function FilterSelect({ label, value, options, onChange }: FilterSelectProps) {
  return (
    <label className="flex items-center gap-1.5 text-xs text-muted">
      <span className="shrink-0">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-white/[0.07] bg-navy-800 px-2 py-1 text-xs text-[#E8EDF7] focus:outline-none focus:ring-1 focus:ring-brand-blue/50"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </label>
  )
}

interface FilterInputProps {
  placeholder: string
  value: string
  onChange: (v: string) => void
}

export function FilterInput({ placeholder, value, onChange }: FilterInputProps) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className="rounded border border-white/[0.07] bg-navy-800 px-2 py-1 text-xs text-[#E8EDF7] placeholder:text-muted/50 focus:outline-none focus:ring-1 focus:ring-brand-blue/50"
    />
  )
}
