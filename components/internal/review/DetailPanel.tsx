import { useState } from "react";
import type { ExtendedReviewTransaction, ReviewFiling, UnmatchedIssuer } from "@/lib/review";
import { formatCurrency, formatPrice, formatNumber, formatDate } from "@/lib/format";

const DIRECTION_IT: Record<string, string> = {
  buy: "Acquisto", sell: "Vendita", unknown: "Sconosciuta",
};

const TYPE_IT: Record<string, string> = {
  buy: "Acquisto",
  sell: "Vendita",
  grant: "Assegnazione (grant)",
  option_exercise: "Esercizio opzione",
  sell_to_cover: "Vendita a copertura",
  subscription: "Sottoscrizione",
  conversion: "Conversione",
  inheritance: "Eredità",
  gift_in: "Dono (ricevuto)",
  gift_out: "Dono (ceduto)",
  transfer_in: "Trasferimento (entrata)",
  transfer_out: "Trasferimento (uscita)",
  pledge_or_security: "Pegno / garanzia",
  derivative_transaction: "Derivato",
  other: "Altro",
  unknown: "Sconosciuto",
};

const INTENT_IT: Record<string, string> = {
  discretionary: "Discrezionale",
  mechanical: "Meccanica",
  unclear: "Non chiaro",
};

const STATUS_IT: Record<string, string> = {
  pending_review: "In attesa",
  under_review: "In revisione",
  confirmed: "Confermato",
  rejected: "Rifiutato",
  corrected: "Corretto",
};

const ALL_TYPES = [
  "buy", "sell", "grant", "option_exercise", "sell_to_cover",
  "subscription", "conversion", "inheritance", "gift_in", "gift_out",
  "transfer_in", "transfer_out", "pledge_or_security", "derivative_transaction", "other",
];

type ActiveForm = "confirm" | "reject" | "classify" | "note" | null;

interface Props {
  selectedTx: ExtendedReviewTransaction | null;
  selectedFiling: ReviewFiling | null;
  selectedIssuer: UnmatchedIssuer | null;
  onAction: (action: string, params?: Record<string, unknown>) => Promise<void>;
  busy: boolean;
  actionError: string | null;
}

export function DetailPanel({
  selectedTx,
  selectedFiling,
  selectedIssuer,
  onAction,
  busy,
  actionError,
}: Props) {
  const [activeForm, setActiveForm] = useState<ActiveForm>(null);
  const [classifyType, setClassifyType] = useState("");
  const [classifyRationale, setClassifyRationale] = useState("");
  const [noteText, setNoteText] = useState("");
  const [issuerIdInput, setIssuerIdInput] = useState("");

  function resetForm() {
    setActiveForm(null);
    setClassifyType("");
    setClassifyRationale("");
    setNoteText("");
    setIssuerIdInput("");
  }

  async function submit(action: string, params?: Record<string, unknown>) {
    await onAction(action, params);
    resetForm();
  }

  if (!selectedTx && !selectedFiling && !selectedIssuer) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 overflow-hidden border-r border-white/[0.07]">
        <span className="text-xs text-muted/40">Seleziona un elemento dalla coda</span>
        <span className="text-[10px] text-muted/25">
          Usa il pannello a sinistra per scegliere una categoria e un elemento
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden border-r border-white/[0.07]">
      {selectedTx ? (
        <TxDetail
          tx={selectedTx}
          activeForm={activeForm}
          setActiveForm={setActiveForm}
          classifyType={classifyType}
          setClassifyType={setClassifyType}
          classifyRationale={classifyRationale}
          setClassifyRationale={setClassifyRationale}
          noteText={noteText}
          setNoteText={setNoteText}
          submit={submit}
          resetForm={resetForm}
          busy={busy}
          actionError={actionError}
        />
      ) : selectedFiling ? (
        <FilingDetail
          filing={selectedFiling}
          submit={submit}
          busy={busy}
          actionError={actionError}
        />
      ) : selectedIssuer ? (
        <IssuerDetail
          issuer={selectedIssuer}
          issuerIdInput={issuerIdInput}
          setIssuerIdInput={setIssuerIdInput}
          submit={submit}
          busy={busy}
          actionError={actionError}
        />
      ) : null}
    </div>
  );
}

// ─── Transaction detail ───────────────────────────────────────────────────────

function TxDetail({
  tx,
  activeForm,
  setActiveForm,
  classifyType,
  setClassifyType,
  classifyRationale,
  setClassifyRationale,
  noteText,
  setNoteText,
  submit,
  resetForm,
  busy,
  actionError,
}: {
  tx: ExtendedReviewTransaction;
  activeForm: ActiveForm;
  setActiveForm: (f: ActiveForm) => void;
  classifyType: string;
  setClassifyType: (v: string) => void;
  classifyRationale: string;
  setClassifyRationale: (v: string) => void;
  noteText: string;
  setNoteText: (v: string) => void;
  submit: (action: string, params?: Record<string, unknown>) => Promise<void>;
  resetForm: () => void;
  busy: boolean;
  actionError: string | null;
}) {
  const confidence = tx.extraction_confidence != null
    ? Math.round(tx.extraction_confidence * 100)
    : null;
  const classConf = tx.classification_confidence != null
    ? Math.round(tx.classification_confidence * 100)
    : null;

  return (
    <>
      {/* Entity header */}
      <div className="shrink-0 border-b border-white/[0.07] px-5 py-3">
        <p className="text-sm font-semibold text-[#E8EDF7]">
          {tx.companies?.name ?? "—"}
        </p>
        <p className="text-xs text-muted">
          {tx.insiders?.full_name ?? "—"}
          {tx.insiders?.role ? ` · ${tx.insiders.role}` : ""}
        </p>
      </div>

      {/* Scrollable fields */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
        {/* Key fields */}
        <section>
          <FieldGrid>
            <Field label="Data transazione" value={formatDate(tx.transaction_date)} />
            {tx.filed_date && <Field label="Data deposito" value={formatDate(tx.filed_date)} />}
            <Field label="ISIN" value={tx.isin ?? "—"} mono />
            <Field label="Direzione"
              value={DIRECTION_IT[tx.direction] ?? tx.direction}
              badge={tx.direction === "buy" ? "buy" : tx.direction === "sell" ? "sell" : undefined}
            />
            <Field label="Tipo transazione"
              value={tx.transaction_type ? (TYPE_IT[tx.transaction_type] ?? tx.transaction_type) : "—"}
            />
            <Field label="Intento economico"
              value={tx.economic_intent ? (INTENT_IT[tx.economic_intent] ?? tx.economic_intent) : "—"}
            />
          </FieldGrid>
        </section>

        {/* Financials */}
        <section>
          <SectionLabel>Dati finanziari</SectionLabel>
          <FieldGrid>
            <Field label="Quantità" value={formatNumber(tx.quantity)} mono />
            <Field label="Prezzo unitario" value={formatPrice(tx.unit_price, tx.currency)} mono />
            <Field label="Valore totale" value={formatCurrency(tx.total_value, tx.currency)} mono />
            <Field label="Valuta" value={tx.currency} />
          </FieldGrid>
        </section>

        {/* Classification */}
        <section>
          <SectionLabel>Classificazione</SectionLabel>
          <FieldGrid>
            {confidence != null && (
              <Field label="Confidenza estrazione" value={`${confidence}%`} mono
                badge={confidence < 60 ? "warn" : undefined}
              />
            )}
            {classConf != null && (
              <Field label="Confidenza classif." value={`${classConf}%`} mono />
            )}
            <Field label="Stato revisione"
              value={STATUS_IT[tx.review_status ?? ""] ?? (tx.review_status ?? "Nessuno")}
            />
            {tx.review_reason && (
              <Field label="Motivo revisione" value={tx.review_reason} mono />
            )}
            {tx.classification_override && (
              <Field label="Corretto manualmente" value="Sì" badge="info" />
            )}
          </FieldGrid>
          {tx.classification_rationale && (
            <div className="mt-2 rounded border border-white/[0.07] bg-navy-900/50 px-3 py-2">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted/40 mb-1">
                Motivazione classificazione
              </p>
              <p className="text-xs text-muted leading-relaxed">{tx.classification_rationale}</p>
            </div>
          )}
        </section>

        {/* Actions */}
        <section>
          <SectionLabel>Azioni</SectionLabel>
          <div className="flex flex-wrap gap-2 mb-3">
            <ActionBtn
              label="Conferma"
              intent="confirm"
              active={activeForm === "confirm"}
              busy={busy}
              onClick={() => setActiveForm(activeForm === "confirm" ? null : "confirm")}
            />
            <ActionBtn
              label="Rifiuta"
              intent="reject"
              active={activeForm === "reject"}
              busy={busy}
              onClick={() => setActiveForm(activeForm === "reject" ? null : "reject")}
            />
            <ActionBtn
              label="Riclassifica"
              intent="neutral"
              active={activeForm === "classify"}
              busy={busy}
              onClick={() => setActiveForm(activeForm === "classify" ? null : "classify")}
            />
            <ActionBtn
              label="Aggiungi nota"
              intent="neutral"
              active={activeForm === "note"}
              busy={busy}
              onClick={() => setActiveForm(activeForm === "note" ? null : "note")}
            />
          </div>

          {activeForm === "confirm" && (
            <InlineCard>
              <p className="text-xs text-muted mb-3">
                Confermare questa transazione come verificata?
              </p>
              <div className="flex gap-2">
                <SubmitBtn label="Sì, conferma" intent="confirm" busy={busy}
                  onClick={() => submit("confirm")} />
                <CancelBtn onClick={resetForm} />
              </div>
            </InlineCard>
          )}

          {activeForm === "reject" && (
            <InlineCard>
              <p className="text-xs text-muted mb-3">
                Rifiutare questa transazione? Non verrà inclusa nei segnali.
              </p>
              <div className="flex gap-2">
                <SubmitBtn label="Sì, rifiuta" intent="reject" busy={busy}
                  onClick={() => submit("reject")} />
                <CancelBtn onClick={resetForm} />
              </div>
            </InlineCard>
          )}

          {activeForm === "classify" && (
            <InlineCard>
              <div className="space-y-2 mb-3">
                <div>
                  <label className="text-[10px] uppercase tracking-wider text-muted/50 mb-1 block">
                    Nuovo tipo
                  </label>
                  <select
                    value={classifyType}
                    onChange={(e) => setClassifyType(e.target.value)}
                    className="w-full rounded border border-white/[0.12] bg-navy-900 px-2 py-1.5 text-xs text-[#E8EDF7] focus:border-brand-blue/50 focus:outline-none"
                  >
                    <option value="">— Seleziona tipo —</option>
                    {ALL_TYPES.map((t) => (
                      <option key={t} value={t}>{TYPE_IT[t] ?? t}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] uppercase tracking-wider text-muted/50 mb-1 block">
                    Motivazione (richiesta)
                  </label>
                  <textarea
                    rows={2}
                    value={classifyRationale}
                    onChange={(e) => setClassifyRationale(e.target.value)}
                    placeholder="Motivo della riclassificazione…"
                    className="w-full resize-none rounded border border-white/[0.12] bg-navy-900 px-2 py-1.5 text-xs text-[#E8EDF7] placeholder:text-muted/30 focus:border-brand-blue/50 focus:outline-none"
                  />
                </div>
              </div>
              <div className="flex gap-2">
                <SubmitBtn
                  label="Riclassifica"
                  intent="neutral"
                  busy={busy}
                  disabled={!classifyType || !classifyRationale.trim()}
                  onClick={() =>
                    submit("classify", {
                      transaction_type: classifyType,
                      rationale: classifyRationale,
                    })
                  }
                />
                <CancelBtn onClick={resetForm} />
              </div>
            </InlineCard>
          )}

          {activeForm === "note" && (
            <InlineCard>
              <div className="mb-3">
                <label className="text-[10px] uppercase tracking-wider text-muted/50 mb-1 block">
                  Nota
                </label>
                <textarea
                  rows={3}
                  value={noteText}
                  onChange={(e) => setNoteText(e.target.value)}
                  placeholder="Aggiungi una nota di revisione…"
                  className="w-full resize-none rounded border border-white/[0.12] bg-navy-900 px-2 py-1.5 text-xs text-[#E8EDF7] placeholder:text-muted/30 focus:border-brand-blue/50 focus:outline-none"
                />
              </div>
              <div className="flex gap-2">
                <SubmitBtn
                  label="Salva nota"
                  intent="neutral"
                  busy={busy}
                  disabled={!noteText.trim()}
                  onClick={() => submit("note", { note: noteText })}
                />
                <CancelBtn onClick={resetForm} />
              </div>
            </InlineCard>
          )}

          {actionError && (
            <p className="mt-2 rounded bg-sell/10 px-3 py-2 text-xs text-sell border border-sell/20">
              {actionError}
            </p>
          )}
        </section>
      </div>
    </>
  );
}

// ─── Filing detail ────────────────────────────────────────────────────────────

function FilingDetail({
  filing,
  submit,
  busy,
  actionError,
}: {
  filing: ReviewFiling;
  submit: (action: string, params?: Record<string, unknown>) => Promise<void>;
  busy: boolean;
  actionError: string | null;
}) {
  return (
    <>
      <div className="shrink-0 border-b border-white/[0.07] px-5 py-3">
        <p className="text-sm font-semibold text-[#E8EDF7]">
          {filing.company_name ?? `Filing #${filing.id}`}
        </p>
        <p className="text-xs text-muted">Filing #{filing.id} · stato: {filing.status}</p>
      </div>
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        <section>
          <FieldGrid>
            <Field label="Data filing" value={formatDate(filing.filing_date)} />
            <Field label="Tentativi" value={String(filing.attempt_count)} mono />
            <Field label="Ultimo tentativo"
              value={filing.last_attempted_at
                ? formatDate(filing.last_attempted_at.split("T")[0])
                : "—"}
            />
            <Field label="Scraper version" value={filing.scraper_version ?? "—"} mono />
            <Field label="Tx inserite" value={String(filing.transactions_inserted)} mono />
          </FieldGrid>
          {filing.last_error && (
            <div className="mt-3 rounded border border-sell/20 bg-sell/[0.06] px-3 py-2">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-sell/60 mb-1">
                Ultimo errore
              </p>
              <p className="font-mono text-xs text-sell/80 break-all leading-relaxed">
                {filing.last_error}
              </p>
            </div>
          )}
        </section>
        <section>
          <SectionLabel>Azioni</SectionLabel>
          <button
            type="button"
            disabled={busy}
            onClick={() => submit("retry")}
            className="rounded bg-brand-blue/20 px-3 py-1.5 text-xs font-medium text-brand-blue transition-colors hover:bg-brand-blue/30 disabled:opacity-50"
          >
            {busy ? "In corso…" : "Riprova elaborazione"}
          </button>
          {actionError && (
            <p className="mt-2 rounded bg-sell/10 px-3 py-2 text-xs text-sell border border-sell/20">
              {actionError}
            </p>
          )}
        </section>
      </div>
    </>
  );
}

// ─── Issuer detail ────────────────────────────────────────────────────────────

function IssuerDetail({
  issuer,
  issuerIdInput,
  setIssuerIdInput,
  submit,
  busy,
  actionError,
}: {
  issuer: UnmatchedIssuer;
  issuerIdInput: string;
  setIssuerIdInput: (v: string) => void;
  submit: (action: string, params?: Record<string, unknown>) => Promise<void>;
  busy: boolean;
  actionError: string | null;
}) {
  return (
    <>
      <div className="shrink-0 border-b border-white/[0.07] px-5 py-3">
        <p className="text-sm font-semibold text-[#E8EDF7]">{issuer.raw_name}</p>
        <p className="text-xs text-muted">Emittente non abbinato · #{issuer.id}</p>
      </div>
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        <section>
          <FieldGrid>
            {issuer.raw_isin && <Field label="ISIN grezzo" value={issuer.raw_isin} mono />}
            <Field label="Scoperto" value={formatDate(issuer.discovered_at.split("T")[0])} />
            {issuer.suggestion_issuer_id && (
              <Field label="Suggerimento" value={`Emittente #${issuer.suggestion_issuer_id}`} />
            )}
          </FieldGrid>
        </section>
        <section>
          <SectionLabel>Azioni</SectionLabel>
          <div className="space-y-3">
            <div>
              <label className="text-[10px] uppercase tracking-wider text-muted/50 mb-1 block">
                Collega a emittente (ID)
              </label>
              <div className="flex gap-2">
                <input
                  type="number"
                  value={issuerIdInput}
                  onChange={(e) => setIssuerIdInput(e.target.value)}
                  placeholder={issuer.suggestion_issuer_id
                    ? String(issuer.suggestion_issuer_id)
                    : "ID emittente…"}
                  className="w-28 rounded border border-white/[0.12] bg-navy-900 px-2 py-1.5 text-xs text-[#E8EDF7] placeholder:text-muted/30 focus:border-brand-blue/50 focus:outline-none"
                />
                <button
                  type="button"
                  disabled={busy || !issuerIdInput.trim()}
                  onClick={() =>
                    submit("resolve", {
                      action: "resolve",
                      issuer_id: parseInt(issuerIdInput, 10),
                    })
                  }
                  className="rounded bg-brand-emerald/20 px-3 py-1.5 text-xs font-medium text-brand-emerald transition-colors hover:bg-brand-emerald/30 disabled:opacity-50"
                >
                  Collega
                </button>
              </div>
            </div>
            <button
              type="button"
              disabled={busy}
              onClick={() => submit("reject", { action: "reject" })}
              className="rounded bg-white/[0.06] px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:bg-white/[0.10] hover:text-[#E8EDF7] disabled:opacity-50"
            >
              Scarta (rumore)
            </button>
          </div>
          {actionError && (
            <p className="mt-2 rounded bg-sell/10 px-3 py-2 text-xs text-sell border border-sell/20">
              {actionError}
            </p>
          )}
        </section>
      </div>
    </>
  );
}

// ─── Primitives ───────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted/40">
      {children}
    </p>
  );
}

function FieldGrid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-x-4 gap-y-2">{children}</div>;
}

function Field({
  label,
  value,
  mono = false,
  badge,
}: {
  label: string;
  value: string;
  mono?: boolean;
  badge?: "buy" | "sell" | "warn" | "info";
}) {
  const BADGE = {
    buy:  "rounded px-1 py-0.5 bg-buy/10 text-buy text-[10px] font-medium",
    sell: "rounded px-1 py-0.5 bg-sell/10 text-sell text-[10px] font-medium",
    warn: "rounded px-1 py-0.5 bg-signal/10 text-signal text-[10px] font-medium",
    info: "rounded px-1 py-0.5 bg-brand-blue/10 text-brand-blue text-[10px] font-medium",
  };
  return (
    <div className="min-w-0">
      <p className="text-[10px] text-muted/50 mb-0.5">{label}</p>
      {badge ? (
        <span className={BADGE[badge]}>{value}</span>
      ) : (
        <p className={`truncate text-xs text-[#E8EDF7] ${mono ? "font-mono" : ""}`}>{value}</p>
      )}
    </div>
  );
}

function InlineCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-white/[0.08] bg-navy-900/60 px-4 py-3">
      {children}
    </div>
  );
}

function ActionBtn({
  label,
  intent,
  active,
  busy,
  disabled,
  onClick,
}: {
  label: string;
  intent: "confirm" | "reject" | "neutral";
  active?: boolean;
  busy?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  const BASE = "rounded px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50";
  const STYLE = {
    confirm: active
      ? "bg-brand-emerald/30 text-brand-emerald ring-1 ring-brand-emerald/40"
      : "bg-brand-emerald/10 text-brand-emerald hover:bg-brand-emerald/20",
    reject: active
      ? "bg-sell/30 text-sell ring-1 ring-sell/40"
      : "bg-sell/10 text-sell hover:bg-sell/20",
    neutral: active
      ? "bg-white/[0.12] text-[#E8EDF7] ring-1 ring-white/20"
      : "bg-white/[0.06] text-muted hover:bg-white/[0.10] hover:text-[#E8EDF7]",
  };
  return (
    <button type="button" disabled={busy || disabled} onClick={onClick}
      className={`${BASE} ${STYLE[intent]}`}
    >
      {label}
    </button>
  );
}

function SubmitBtn({
  label,
  intent,
  busy,
  disabled,
  onClick,
}: {
  label: string;
  intent: "confirm" | "reject" | "neutral";
  busy: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <ActionBtn
      label={busy ? "In corso…" : label}
      intent={intent}
      busy={busy}
      disabled={disabled}
      onClick={onClick}
    />
  );
}

function CancelBtn({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded px-3 py-1.5 text-xs text-muted/60 transition-colors hover:text-muted"
    >
      Annulla
    </button>
  );
}
