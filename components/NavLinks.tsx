"use client";

import Link from "next/link";
import { useT } from "./LanguageProvider";
import { LanguageToggle } from "./LanguageToggle";

export function NavLinks() {
  const t = useT();
  return (
    <nav className="flex items-center gap-1 text-sm font-medium">
      <Link
        href="/"
        className="rounded-md px-3 py-1.5 text-muted transition-colors hover:text-[#E8EDF7] hover:bg-white/5"
      >
        {t("Operazioni", "Transactions")}
      </Link>
      <Link
        href="/signals"
        className="rounded-md px-3 py-1.5 text-muted transition-colors hover:text-[#E8EDF7] hover:bg-white/5"
      >
        {t("Segnali", "Signals")}
      </Link>
      <LanguageToggle />
    </nav>
  );
}
