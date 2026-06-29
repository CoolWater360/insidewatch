import type { ReviewTransaction, ReviewFiling, UnmatchedIssuer } from "./review";

export type QueueCategory =
  | "direction_unknown"
  | "type_unknown"
  | "low_confidence"
  | "corporate_action"
  | "vehicle_fiduciary"
  | "issuer_unmatched"
  | "relationship"
  | "failed_filing"
  | "fixture_candidate";

export type CategoryCoverage = "live" | "partial" | "planned";

export interface CategoryMeta {
  id: QueueCategory;
  label: string;
  description: string;
  coverage: CategoryCoverage;
}

export const QUEUE_CATEGORIES: CategoryMeta[] = [
  { id: "direction_unknown",  label: "Direzione sconosciuta",    description: "Direzione ambigua o non determinata",             coverage: "live"    },
  { id: "type_unknown",       label: "Tipo sconosciuto",         description: "Tipo transazione non classificato",               coverage: "live"    },
  { id: "low_confidence",     label: "Bassa confidenza",         description: "Confidenza estrazione < 60% o non disponibile",  coverage: "live"    },
  { id: "corporate_action",   label: "Corporate actions",        description: "Grant, exercise, trasferimento, conversione",     coverage: "live"    },
  { id: "vehicle_fiduciary",  label: "Veicoli / fiduciaria",     description: "Possibile intermediario o veicolo (euristica)",  coverage: "partial" },
  { id: "issuer_unmatched",   label: "Emittenti da risolvere",   description: "Nome azienda senza corrispondenza nel registro", coverage: "live"    },
  { id: "relationship",       label: "Relazioni da verificare",  description: "Relazioni entità — Phase 17A, in sviluppo",      coverage: "planned" },
  { id: "failed_filing",      label: "Filing falliti",           description: "Documenti PDF non elaborati o saltati",          coverage: "live"    },
  { id: "fixture_candidate",  label: "Fixture regressione",      description: "Candidati per test di regressione automatica",   coverage: "planned" },
];

const CORPORATE_TYPES = new Set<string>([
  "grant", "option_exercise", "sell_to_cover", "conversion",
  "inheritance", "gift_in", "gift_out", "transfer_in", "transfer_out",
  "pledge_or_security", "derivative_transaction",
]);

const VEHICLE_RE = /\b(fiduciar|veicol|holding|fondo\s|fund\s|tramite\s+soc|s\.r\.l\.|sicav|sicaf)/i;

/**
 * Assign a single ReviewTransaction to exactly one QueueCategory.
 * Rules are checked in priority order — first match wins.
 */
export function categorizeTransaction(tx: ReviewTransaction): QueueCategory {
  const dir    = (tx.direction         ?? "").toLowerCase();
  const type   = (tx.transaction_type  ?? "").toLowerCase();
  const reason = (tx.review_reason     ?? "").toLowerCase();

  if (dir === "unknown" || dir === "" || reason === "ambiguous_direction" || reason.includes("direction")) {
    return "direction_unknown";
  }
  if (!type || type === "unknown" || reason === "unknown_type" || reason.includes("unknown_type")) {
    return "type_unknown";
  }
  if (CORPORATE_TYPES.has(type)) {
    return "corporate_action";
  }
  if (tx.raw_nature_text && VEHICLE_RE.test(tx.raw_nature_text)) {
    return "vehicle_fiduciary";
  }
  return "low_confidence";
}

/**
 * Partition an array of transactions into per-category buckets.
 * All nine keys are always present; unused categories have empty arrays.
 * Generic so callers with extended types retain their element type.
 */
export function groupTransactions<T extends ReviewTransaction>(
  txs: T[]
): Record<QueueCategory, T[]> {
  const out = {
    direction_unknown:  [] as T[],
    type_unknown:       [] as T[],
    low_confidence:     [] as T[],
    corporate_action:   [] as T[],
    vehicle_fiduciary:  [] as T[],
    issuer_unmatched:   [] as T[],
    relationship:       [] as T[],
    failed_filing:      [] as T[],
    fixture_candidate:  [] as T[],
  } as Record<QueueCategory, T[]>;
  for (const tx of txs) {
    out[categorizeTransaction(tx)].push(tx);
  }
  return out;
}

/**
 * Live count for each category.
 * Transactions are counted from the pre-grouped buckets;
 * filings and issuers are passed in separately.
 * Planned categories always report 0.
 */
export function countsByCategory(
  groups: Record<QueueCategory, readonly unknown[]>,
  filings: ReviewFiling[],
  issuers: UnmatchedIssuer[]
): Record<QueueCategory, number> {
  const counts = {} as Record<QueueCategory, number>;
  for (const { id } of QUEUE_CATEGORIES) {
    counts[id] = groups[id].length;
  }
  counts.issuer_unmatched  = issuers.length;
  counts.failed_filing     = filings.length;
  counts.relationship      = 0;
  counts.fixture_candidate = 0;
  return counts;
}
