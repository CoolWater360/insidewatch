export function Footer() {
  return (
    <footer className="mt-8 border-t border-white/[0.06] bg-navy-900/50">
      <div className="mx-auto max-w-7xl px-4 py-6 text-xs leading-relaxed text-muted sm:px-6">
        <p className="font-semibold text-[#E8EDF7]/70">Disclaimer</p>
        <p className="mt-1">
          This tool aggregates and displays <strong className="text-[#E8EDF7]/50">publicly disclosed</strong>{" "}
          information only, sourced from Borsa Italiana and CONSOB disclosures under MAR Art. 19.
          It does <strong className="text-[#E8EDF7]/50">not</strong> execute trades, provide investment
          advice, or manage assets. Data is provided &ldquo;as is&rdquo; for informational and
          research purposes, may contain errors or omissions, and should be independently verified
          against the official source filings before any use.
        </p>
        <p className="mt-2">
          Source:{" "}
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
