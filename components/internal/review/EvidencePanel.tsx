import type { ExtendedReviewTransaction, ReviewFiling, UnmatchedIssuer } from "@/lib/review";
import { formatDate } from "@/lib/format";

interface Props {
  selectedTx: ExtendedReviewTransaction | null;
  selectedFiling: ReviewFiling | null;
  selectedIssuer: UnmatchedIssuer | null;
}

export function EvidencePanel({ selectedTx, selectedFiling, selectedIssuer }: Props) {
  const hasSelection = selectedTx || selectedFiling || selectedIssuer;

  return (
    <div className="flex w-72 shrink-0 flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 border-b border-white/[0.07] px-4 py-3">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted/60">
          Prove & Provenienza
        </span>
      </div>

      {!hasSelection ? (
        <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
          <span className="text-xs text-muted/30">Nessun elemento selezionato</span>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
          {selectedTx ? (
            <TxEvidence tx={selectedTx} />
          ) : selectedFiling ? (
            <FilingEvidence filing={selectedFiling} />
          ) : selectedIssuer ? (
            <IssuerEvidence issuer={selectedIssuer} />
          ) : null}
        </div>
      )}
    </div>
  );
}

// ─── Transaction evidence ─────────────────────────────────────────────────────

function TxEvidence({ tx }: { tx: ExtendedReviewTransaction }) {
  return (
    <>
      {/* Source */}
      <EvidenceSection title="Fonte primaria">
        {tx.source_url ? (
          <EvidenceRow label="URL originale">
            <a
              href={tx.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="break-all font-mono text-[10px] text-brand-blue hover:underline"
            >
              {tx.source_url}
            </a>
          </EvidenceRow>
        ) : (
          <EvidenceRow label="URL originale">
            <span className="text-[11px] text-muted/40">Non disponibile</span>
          </EvidenceRow>
        )}
        {tx.source_filing_id != null && (
          <EvidenceRow label="Filing ID">
            <a
              href={`/internal/filings/${tx.source_filing_id}`}
              className="font-mono text-[11px] text-brand-blue hover:underline"
            >
              #{tx.source_filing_id}
            </a>
          </EvidenceRow>
        )}
        <EvidenceRow label="Disponibilità">
          <AvailabilityBadge available={!!tx.source_url} />
        </EvidenceRow>
      </EvidenceSection>

      {/* Parser provenance */}
      <EvidenceSection title="Provenienza parser">
        {tx.parser_version && (
          <EvidenceRow label="Versione parser">
            <span className="font-mono text-[11px] text-[#E8EDF7]">{tx.parser_version}</span>
          </EvidenceRow>
        )}
        {tx.extraction_confidence != null && (
          <EvidenceRow label="Confidenza estrazione">
            <ConfidenceBadge value={tx.extraction_confidence} />
          </EvidenceRow>
        )}
        {tx.classification_confidence != null && (
          <EvidenceRow label="Confidenza classif.">
            <span className="font-mono text-[11px] text-[#E8EDF7]">
              {Math.round(tx.classification_confidence * 100)}%
            </span>
          </EvidenceRow>
        )}
      </EvidenceSection>

      {/* Extracted text */}
      {tx.raw_nature_text && (
        <EvidenceSection title="Testo estratto">
          <blockquote className="rounded border-l-2 border-white/20 bg-navy-900/50 px-3 py-2 text-[11px] italic leading-relaxed text-muted">
            &ldquo;{tx.raw_nature_text}&rdquo;
          </blockquote>
        </EvidenceSection>
      )}

      {/* Correction history */}
      {tx.classification_override && (
        <EvidenceSection title="Storico correzioni">
          <EvidenceRow label="Corretto manualmente">
            <span className="text-[11px] text-brand-blue">Sì</span>
          </EvidenceRow>
          {tx.classification_overridden_by && (
            <EvidenceRow label="Operatore">
              <span className="font-mono text-[11px] text-muted">{tx.classification_overridden_by}</span>
            </EvidenceRow>
          )}
          {tx.classification_overridden_at && (
            <EvidenceRow label="Data correzione">
              <span className="text-[11px] text-muted">
                {formatDate(tx.classification_overridden_at.split("T")[0])}
              </span>
            </EvidenceRow>
          )}
        </EvidenceSection>
      )}
    </>
  );
}

// ─── Filing evidence ──────────────────────────────────────────────────────────

function FilingEvidence({ filing }: { filing: ReviewFiling }) {
  return (
    <EvidenceSection title="Documento filing">
      <EvidenceRow label="URL documento">
        {filing.pdf_url ? (
          <a
            href={filing.pdf_url}
            target="_blank"
            rel="noopener noreferrer"
            className="break-all font-mono text-[10px] text-brand-blue hover:underline"
          >
            {filing.pdf_url}
          </a>
        ) : (
          <span className="text-[11px] text-muted/40">Non disponibile</span>
        )}
      </EvidenceRow>
      <EvidenceRow label="Data filing">
        <span className="text-[11px] text-muted">{formatDate(filing.filing_date)}</span>
      </EvidenceRow>
      <EvidenceRow label="Versione scraper">
        <span className="font-mono text-[11px] text-muted">{filing.scraper_version ?? "—"}</span>
      </EvidenceRow>
    </EvidenceSection>
  );
}

// ─── Issuer evidence ──────────────────────────────────────────────────────────

function IssuerEvidence({ issuer }: { issuer: UnmatchedIssuer }) {
  return (
    <EvidenceSection title="Dati grezzi">
      <EvidenceRow label="Nome grezzo">
        <span className="text-[11px] text-[#E8EDF7]">{issuer.raw_name}</span>
      </EvidenceRow>
      {issuer.raw_isin && (
        <EvidenceRow label="ISIN grezzo">
          <span className="font-mono text-[11px] text-muted">{issuer.raw_isin}</span>
        </EvidenceRow>
      )}
      <EvidenceRow label="Aggiunto">
        <span className="text-[11px] text-muted">{issuer.discovered_at ? formatDate(issuer.discovered_at.split("T")[0]) : null}</span>
      </EvidenceRow>
      {issuer.suggestion_issuer_id && (
        <EvidenceRow label="Suggerimento ML">
          <a
            href={`/internal/issuers?highlight=${issuer.suggestion_issuer_id}`}
            className="font-mono text-[11px] text-brand-blue hover:underline"
          >
            #{issuer.suggestion_issuer_id}
          </a>
        </EvidenceRow>
      )}
    </EvidenceSection>
  );
}

// ─── Primitives ───────────────────────────────────────────────────────────────

function EvidenceSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted/40">
        {title}
      </p>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function EvidenceRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-[10px] text-muted/40 mb-0.5">{label}</p>
      {children}
    </div>
  );
}

function AvailabilityBadge({ available }: { available: boolean }) {
  return available ? (
    <span className="inline-flex items-center gap-1 text-[11px] text-brand-emerald">
      <span className="h-1.5 w-1.5 rounded-full bg-brand-emerald" />
      Disponibile
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-[11px] text-muted/50">
      <span className="h-1.5 w-1.5 rounded-full bg-muted/30" />
      Non disponibile
    </span>
  );
}

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? "text-brand-emerald" : pct >= 60 ? "text-[#E8EDF7]" : "text-signal";
  return <span className={`font-mono text-[11px] ${color}`}>{pct}%</span>;
}
