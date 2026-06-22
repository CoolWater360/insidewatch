"use client";

import { useLanguage } from "./LanguageProvider";

export function LanguageToggle() {
  const { lang, toggle } = useLanguage();
  return (
    <button
      onClick={toggle}
      title={lang === "it" ? "Switch to English" : "Passa all'italiano"}
      className="rounded-md px-2.5 py-1 text-xs font-semibold tracking-wide text-muted hover:text-[#E8EDF7] hover:bg-white/5 transition-colors border border-white/10"
    >
      {lang === "it" ? "EN" : "IT"}
    </button>
  );
}
