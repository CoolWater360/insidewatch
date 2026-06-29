"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useState } from "react"

function Icon({ path, className = "" }: { path: string | readonly string[]; className?: string }) {
  const paths: readonly string[] = Array.isArray(path) ? path : [path]
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className={`h-4 w-4 shrink-0 ${className}`}>
      {paths.map((d, i) => <path key={i} d={d} />)}
    </svg>
  )
}

const ICONS = {
  dashboard:    "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6",
  signals:      "M13 10V3L4 14h7v7l9-11h-7z",
  review:       ["M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"],
  transactions: "M22 12h-4l-3 9L9 3l-3 9H2",
  filings:      "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
  issuers:      "M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-2 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4",
  quality:      "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z",
  operations:   ["M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z", "M15 12a3 3 0 11-6 0 3 3 0 016 0z"],
  ownership:    "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z",
  buybacks:     "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15",
  governance:   "M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4",
  corporate:    "M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z",
  entities:     "M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
  exports:      "M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12",
  settings:     ["M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z", "M15 12a3 3 0 11-6 0 3 3 0 016 0z"],
  menu:         "M4 6h16M4 12h16M4 18h16",
  close:        "M6 18L18 6M6 6l12 12",
  plus:         "M12 4v16m8-8H4",
  support:      "M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
  health:       ["M22 12h-4l-3 9L9 3l-3 9H2"],
  arrow:        "M17 8l4 4m0 0l-4 4m4-4H3",
} as const

type IconKey = keyof typeof ICONS

// ─── Live nav item ─────────────────────────────────────────────────────────────

function NavItem({
  href,
  label,
  icon,
  exact = false,
  badge,
  pathname,
}: {
  href: string
  label: string
  icon: IconKey
  exact?: boolean
  badge?: number
  pathname: string
}) {
  const isActive = exact
    ? pathname === href
    : pathname === href || pathname.startsWith(href + "/")

  return (
    <Link
      href={href}
      className={`group relative flex items-center gap-2.5 rounded-md px-2.5 py-[7px] text-[13px] font-medium transition-colors ${
        isActive
          ? "bg-navy-700 text-brand-blue"
          : "text-muted hover:bg-white/[0.05] hover:text-[#E8EDF7]"
      }`}
    >
      {isActive && (
        <span className="absolute left-0 top-1/2 h-[18px] w-0.5 -translate-y-1/2 rounded-r-full bg-brand-blue" />
      )}
      <Icon
        path={ICONS[icon]}
        className={isActive ? "text-brand-blue" : "text-muted/60 group-hover:text-[#E8EDF7]"}
      />
      <span className="truncate">{label}</span>
      {badge !== undefined && badge > 0 && (
        <span className="ml-auto shrink-0 rounded-full bg-signal/20 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-signal">
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </Link>
  )
}

// ─── Planned item ─────────────────────────────────────────────────────────────

function PlannedItem({
  href,
  label,
  icon,
}: {
  href: string
  label: string
  icon: IconKey
}) {
  return (
    <Link
      href={href}
      className="group flex items-center gap-2.5 rounded-md px-2.5 py-[7px] text-[13px] font-medium text-muted/35 transition-colors hover:bg-white/[0.02] hover:text-muted/55"
    >
      <Icon path={ICONS[icon]} className="text-muted/25 group-hover:text-muted/40" />
      <span className="truncate">{label}</span>
      <span className="ml-auto shrink-0 rounded px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-muted/25 ring-1 ring-muted/15">
        Presto
      </span>
    </Link>
  )
}

// ─── Section label ────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-4 mb-1 px-2.5 text-[9px] font-semibold uppercase tracking-widest text-muted/35">
      {children}
    </p>
  )
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────

export function Sidebar() {
  const pathname = usePathname()
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <>
      {/* Mobile toggle */}
      <button
        type="button"
        className="fixed left-4 top-3 z-50 flex h-7 w-7 items-center justify-center rounded-md bg-navy-800 text-muted lg:hidden"
        onClick={() => setMobileOpen((v) => !v)}
        aria-label="Toggle navigation"
      >
        <Icon path={mobileOpen ? ICONS.close : ICONS.menu} />
      </button>

      {/* Mobile backdrop */}
      {mobileOpen && (
        <div className="fixed inset-0 z-30 bg-black/60 lg:hidden" onClick={() => setMobileOpen(false)} />
      )}

      {/* Sidebar panel */}
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-52 flex-col border-r border-white/[0.06] bg-navy-900 transition-transform duration-200 ease-out lg:translate-x-0 ${mobileOpen ? "translate-x-0" : "-translate-x-full"}`}>

        {/* Logo block */}
        <div className="flex h-[52px] shrink-0 flex-col justify-center px-4">
          <span className="text-[15px] font-semibold tracking-tight text-[#E8EDF7]">InsideWatch</span>
          <span className="mt-0.5 text-[10px] font-normal tracking-wide text-muted/55">
            Institutional Intelligence Console
          </span>
        </div>

        {/* Primary CTA */}
        <div className="px-3 pb-3">
          <button
            type="button"
            className="flex w-full items-center justify-center gap-1.5 rounded-md bg-brand-blue px-3 py-[7px] text-[13px] font-semibold text-white shadow-sm transition-opacity hover:opacity-90"
          >
            <Icon path={ICONS.plus} className="h-3.5 w-3.5" />
            Nuova Analisi
          </button>
        </div>

        <div className="mx-3 h-px bg-white/[0.06]" />

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-3 py-1">

          <SectionLabel>Dashboard</SectionLabel>
          <NavItem href="/internal"              label="Dashboard"           icon="dashboard"     exact pathname={pathname} />

          <SectionLabel>Intelligence</SectionLabel>
          <NavItem href="/internal/signals"      label="Segnali & Contesto"  icon="signals"             pathname={pathname} />
          <NavItem href="/internal/review"       label="Coda di Revisione"   icon="review"              pathname={pathname} />
          <NavItem href="/internal/transactions" label="Transazioni"          icon="transactions"        pathname={pathname} />

          <SectionLabel>Emittenti</SectionLabel>
          <NavItem href="/internal/issuers"      label="Emittenti"           icon="issuers"             pathname={pathname} />

          <SectionLabel>Audit</SectionLabel>
          <NavItem href="/internal/filings"      label="Filing"              icon="filings"             pathname={pathname} />

          <SectionLabel>Operativo</SectionLabel>
          <NavItem href="/internal/quality"      label="Qualità Dati"        icon="quality"             pathname={pathname} />
          <NavItem href="/internal/operations"   label="Operazioni"          icon="operations"          pathname={pathname} />

          <div className="my-2 h-px bg-white/[0.06]" />

          <SectionLabel>In Sviluppo</SectionLabel>
          <PlannedItem href="/internal/ownership"        label="Proprietà & Controllo" icon="ownership"   />
          <PlannedItem href="/internal/buybacks"         label="Buyback"               icon="buybacks"    />
          <PlannedItem href="/internal/governance"       label="Governance"            icon="governance"  />
          <PlannedItem href="/internal/corporate-events" label="Azioni Societarie"     icon="corporate"   />
          <PlannedItem href="/internal/entities"         label="Entità & Veicoli"      icon="entities"    />
          <PlannedItem href="/internal/exports"          label="Workspace Dati"        icon="exports"     />
        </nav>

        {/* Bottom section */}
        <div className="shrink-0 border-t border-white/[0.06] px-3 py-2 space-y-0.5">
          <NavItem href="/internal/settings" label="Impostazioni" icon="settings" pathname={pathname} />
          <a
            href="mailto:support@insidewatch.it"
            className="flex items-center gap-2.5 rounded-md px-2.5 py-[7px] text-[13px] font-medium text-muted/40 transition-colors hover:bg-white/[0.05] hover:text-muted"
          >
            <Icon path={ICONS.support} className="h-4 w-4 text-muted/30" />
            Supporto
          </a>
          <Link
            href="/"
            className="flex items-center gap-2.5 rounded-md px-2.5 py-[7px] text-[13px] font-medium text-muted/30 transition-colors hover:bg-white/[0.05] hover:text-muted/50"
          >
            <Icon path={ICONS.arrow} className="h-4 w-4 rotate-180 text-muted/25" />
            Vista pubblica
          </Link>

          {/* Admin identity chip */}
          <div className="mt-2 flex items-center gap-2 rounded-md border border-white/[0.06] bg-navy-800/60 px-2.5 py-2">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-blue/20 text-[10px] font-semibold text-brand-blue">
              A
            </div>
            <div className="min-w-0">
              <p className="truncate text-[11px] font-medium text-[#E8EDF7]">Admin</p>
              <p className="truncate text-[10px] text-muted/40">Operatore</p>
            </div>
          </div>
        </div>
      </aside>
    </>
  )
}
