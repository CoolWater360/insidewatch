import Link from "next/link";

export const metadata = { title: "InsideWatch — Internal Console" };

export default function InternalLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-navy-950 text-[#E8EDF7]">
      <header className="border-b border-amber-500/30 bg-amber-950/20 px-6 py-3">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <div className="flex items-center gap-4">
            <span className="text-xs font-semibold uppercase tracking-widest text-amber-400">
              Internal Console
            </span>
            <nav className="flex items-center gap-1 text-sm">
              <Link
                href="/internal"
                className="rounded px-3 py-1 text-muted transition-colors hover:bg-white/5 hover:text-[#E8EDF7]"
              >
                Dashboard
              </Link>
              <Link
                href="/internal/transactions"
                className="rounded px-3 py-1 text-muted transition-colors hover:bg-white/5 hover:text-[#E8EDF7]"
              >
                Transactions
              </Link>
              <Link
                href="/internal/filings"
                className="rounded px-3 py-1 text-muted transition-colors hover:bg-white/5 hover:text-[#E8EDF7]"
              >
                Filings
              </Link>
              <Link
                href="/internal/issuers"
                className="rounded px-3 py-1 text-muted transition-colors hover:bg-white/5 hover:text-[#E8EDF7]"
              >
                Issuers
              </Link>
            </nav>
          </div>
          <Link
            href="/"
            className="text-xs text-muted transition-colors hover:text-[#E8EDF7]"
          >
            ← Public view
          </Link>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
    </div>
  );
}
