"use client";

import { useLanguage } from "./LanguageProvider";

export function Footer() {
  const { lang } = useLanguage();
  return (
    <footer className="mt-8 border-t border-white/[0.06] bg-navy-900/50">
      <div className="mx-auto max-w-7xl px-4 py-6 text-xs leading-relaxed text-muted sm:px-6">
        <p className="font-semibold text-[#E8EDF7]/70">Disclaimer</p>
        {lang === "it" ? (
          <p className="mt-1">
            Questo strumento aggrega e mostra esclusivamente informazioni{" "}
            <strong className="text-[#E8EDF7]/50">pubblicamente divulgate</strong>, provenienti da
            comunicazioni Borsa Italiana e CONSOB ai sensi del Regolamento MAR Art. 19.
            Non esegue operazioni, non fornisce consulenza in materia di investimenti e non gestisce
            patrimoni. I dati sono forniti &ldquo;così come sono&rdquo; a fini informativi e di ricerca,
            possono contenere errori od omissioni e devono essere verificati sulle comunicazioni ufficiali
            prima di qualsiasi utilizzo.
          </p>
        ) : (
          <p className="mt-1">
            This tool aggregates and displays{" "}
            <strong className="text-[#E8EDF7]/50">publicly disclosed</strong>{" "}
            information only, sourced from Borsa Italiana and CONSOB disclosures under MAR Art. 19.
            It does <strong className="text-[#E8EDF7]/50">not</strong> execute trades, provide investment
            advice, or manage assets. Data is provided &ldquo;as is&rdquo; for informational and
            research purposes, may contain errors or omissions, and should be independently verified
            against the official source filings before any use.
          </p>
        )}
        <p className="mt-2">
          {lang === "it" ? "Fonte" : "Source"}:{" "}
          <a
            className="text-brand-blue/70 hover:text-brand-blue transition-colors underline"
            href="https://www.borsaitaliana.it/borsa/documenti/societa-quotate/internal-dealing.html"
            target="_blank"
            rel="noopener noreferrer"
          >
            Borsa Italiana — Internal Dealing
          </a>
        </p>
      </div>
    </footer>
  );
}
