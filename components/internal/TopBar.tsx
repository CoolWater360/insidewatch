export function TopBar() {
  return (
    <header className="flex h-10 shrink-0 items-center justify-end gap-3 border-b border-white/[0.07] bg-navy-900/60 px-5">
      <span className="h-1.5 w-1.5 rounded-full bg-brand-emerald" />
      <span className="text-[10px] font-medium text-muted/60">Feed attivo</span>
    </header>
  )
}
