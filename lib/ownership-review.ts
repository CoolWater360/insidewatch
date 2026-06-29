/**
 * Phase 17B.6 — server-side helpers for the internal Ownership Review Queue.
 *
 * Read-only listings of PENDING ownership-pilot records (entities, ownership
 * events, explicit entity relationships) for /internal/review/ownership.
 *
 * All queries use getSupabaseServer() (service-role key) and are safe ONLY in
 * server components / route handlers. Never import from a client component.
 *
 * Source evidence: the official CONSOB source_url is exposed. The internal-only
 * columns context_sources.storage_path and context_sources.raw_text are NEVER
 * selected here.
 *
 * Pure helpers (recommendEntityType, label/format) are exported for testing.
 */

import { unstable_noStore as noStore } from "next/cache";
import { getSupabaseServer } from "./supabase-server";

// ─── Vocabulary ────────────────────────────────────────────────────────────────

// entities.entity_type CHECK vocabulary (migration 016).
export const ENTITY_TYPES = [
  "natural_person", "company", "holding_company", "trust",
  "fiduciary", "foundation", "fund", "nominee", "other",
] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];

export function isEntityType(v: string): v is EntityType {
  return (ENTITY_TYPES as readonly string[]).includes(v);
}

// Organisation indicators used ONLY to flag a 'natural_person' that is clearly
// an organisation, mirroring scraper/ownership/review.py. Produces a
// recommendation + reason; never mutates data.
const ORG_NAME_MARKERS = [
  "BANK", "BANCA", "GROUP", "HOLDING", "CAPITAL", "PARTNERS", "SECURITIES",
  "INVESTMENT", "ASSET MANAGEMENT", "MANAGEMENT", "ADVISORS", "ADVISERS",
  "INTERNATIONAL", "GLOBAL", "FUND", "FUNDS", "SICAV", "SGR", "TRUST",
  "ASSICURAZIONI", "INSURANCE", "FINANCIAL", "FINANCE", "GOLDMAN SACHS",
  "MORGAN STANLEY", "BLACKROCK", "JPMORGAN", "BARCLAYS", "VANGUARD",
  "& CO", "AND CO", "CO.", "COMPANY", "CORP", "INCORPORATED",
];

export interface EntityTypeRecommendation {
  proposedType: EntityType;
  reason: string;
}

/**
 * Pure. Returns a recommendation when a 'natural_person' entity name clearly
 * denotes an organisation, else null. Mirrors the Python review logic.
 */
export function recommendEntityType(
  legalName: string,
  currentType: string
): EntityTypeRecommendation | null {
  if (currentType !== "natural_person") return null;
  const up = ` ${(legalName ?? "").toUpperCase()} `;
  for (const marker of ORG_NAME_MARKERS) {
    if (up.includes(marker)) {
      return {
        proposedType: "company",
        reason:
          `legal name contains organisation indicator "${marker.trim()}" ` +
          `but is tagged "natural_person" (ingestion suffix-heuristic miss); ` +
          `operator to confirm exact organisation type`,
      };
    }
  }
  return null;
}

// ─── Row shapes for the UI ─────────────────────────────────────────────────────

export interface PendingEntityRow {
  id: number;
  legal_name: string;
  entity_type: string;
  review_status: string;
  recommendation: EntityTypeRecommendation | null;
}

export interface PendingEventRow {
  id: number;
  issuer_name: string;
  raw_entity_name: string;
  entity_id: number | null;
  voting_pct_after: number | null;
  voting_pct_before: number | null;
  event_type: string;
  event_date: string | null;
  publication_date: string | null;
  direct_or_indirect: string | null;
  raw_vehicle_name: string | null;
  review_status: string;
  confidence: string;
  source_url: string | null;
  /** True when direction/threshold cannot be determined from the source. */
  directionUndetermined: boolean;
  ambiguityNote: string | null;
}

export interface PendingRelationshipRow {
  id: number;
  subject_name: string;
  relationship_type: string;
  object_name: string;
  direct_or_indirect: string | null;
  review_status: string;
  confidence: string;
  source_url: string | null;
}

export interface OwnershipReviewData {
  entities: PendingEntityRow[];
  events: PendingEventRow[];
  relationships: PendingRelationshipRow[];
}

// ─── Listing (service-role; read-only) ─────────────────────────────────────────

async function mapById(
  db: ReturnType<typeof getSupabaseServer>,
  table: string,
  col: string,
  ids: number[]
): Promise<Map<number, Record<string, unknown>>> {
  const out = new Map<number, Record<string, unknown>>();
  const unique = [...new Set(ids.filter((x) => x != null))];
  if (unique.length === 0) return out;
  const { data } = await db.from(table).select(col).in("id", unique);
  for (const r of (data ?? []) as unknown as Record<string, unknown>[]) {
    out.set(r.id as number, r);
  }
  return out;
}

export async function getOwnershipReviewData(): Promise<OwnershipReviewData> {
  noStore();
  const db = getSupabaseServer();

  const [entRes, evRes, relRes] = await Promise.all([
    db.from("entities").select("id, legal_name, entity_type, review_status")
      .eq("review_status", "pending_review").order("id"),
    db.from("ownership_events")
      .select(
        "id, issuer_id, entity_id, raw_entity_name, voting_pct_after, " +
          "voting_pct_before, event_type, event_date, " +
          "direct_or_indirect, raw_vehicle_name, review_status, confidence, source_id"
      )
      .eq("review_status", "pending_review").eq("is_current", true).order("id"),
    db.from("entity_relationships")
      .select(
        "id, subject_entity_id, object_entity_id, object_issuer_id, " +
          "relationship_type, direct_or_indirect, review_status, confidence, source_id"
      )
      .eq("review_status", "pending_review").eq("is_current", true).order("id"),
  ]);

  const entities = (entRes.data ?? []) as unknown as Record<string, unknown>[];
  const events = (evRes.data ?? []) as unknown as Record<string, unknown>[];
  const rels = (relRes.data ?? []) as unknown as Record<string, unknown>[];

  // Build lookup maps for joined display fields.
  const issuerIds = events.map((e) => e.issuer_id as number);
  const issuerObjIds = rels.map((r) => r.object_issuer_id as number).filter(Boolean);
  const sourceIds = [
    ...events.map((e) => e.source_id as number),
    ...rels.map((r) => r.source_id as number),
  ];
  const relEntityIds = [
    ...rels.map((r) => r.subject_entity_id as number),
    ...rels.map((r) => r.object_entity_id as number),
  ].filter(Boolean) as number[];

  const [issuerMap, issuerObjMap, sourceMap, relEntMap] = await Promise.all([
    mapById(db, "issuers", "id, canonical_name", issuerIds),
    mapById(db, "issuers", "id, canonical_name", issuerObjIds),
    mapById(db, "context_sources", "id, source_url, publication_timestamp", sourceIds),
    mapById(db, "entities", "id, legal_name", relEntityIds),
  ]);

  const entityRows: PendingEntityRow[] = entities.map((e) => ({
    id: e.id as number,
    legal_name: (e.legal_name as string) ?? "",
    entity_type: (e.entity_type as string) ?? "",
    review_status: (e.review_status as string) ?? "",
    recommendation: recommendEntityType(
      (e.legal_name as string) ?? "",
      (e.entity_type as string) ?? ""
    ),
  }));

  const eventRows: PendingEventRow[] = events.map((e) => {
    const undetermined = (e.event_type as string) === "other";
    const src = sourceMap.get(e.source_id as number);
    const pubTs = src?.publication_timestamp as string | undefined;
    return {
      id: e.id as number,
      issuer_name:
        (issuerMap.get(e.issuer_id as number)?.canonical_name as string) ??
        `issuer #${e.issuer_id}`,
      raw_entity_name: (e.raw_entity_name as string) ?? "",
      entity_id: (e.entity_id as number) ?? null,
      voting_pct_after: (e.voting_pct_after as number) ?? null,
      voting_pct_before: (e.voting_pct_before as number) ?? null,
      event_type: (e.event_type as string) ?? "",
      event_date: (e.event_date as string) ?? null,
      publication_date: pubTs ? pubTs.slice(0, 10) : null,
      direct_or_indirect: (e.direct_or_indirect as string) ?? null,
      raw_vehicle_name: (e.raw_vehicle_name as string) ?? null,
      review_status: (e.review_status as string) ?? "",
      confidence: (e.confidence as string) ?? "",
      source_url: (sourceMap.get(e.source_id as number)?.source_url as string) ?? null,
      directionUndetermined: undetermined,
      ambiguityNote: undetermined
        ? "direction not determined from source"
        : null,
    };
  });

  const relRows: PendingRelationshipRow[] = rels.map((r) => ({
    id: r.id as number,
    subject_name:
      (relEntMap.get(r.subject_entity_id as number)?.legal_name as string) ??
      `entity #${r.subject_entity_id}`,
    relationship_type: (r.relationship_type as string) ?? "",
    object_name: r.object_entity_id
      ? ((relEntMap.get(r.object_entity_id as number)?.legal_name as string) ??
        `entity #${r.object_entity_id}`)
      : ((issuerObjMap.get(r.object_issuer_id as number)?.canonical_name as string) ??
        `issuer #${r.object_issuer_id}`),
    direct_or_indirect: (r.direct_or_indirect as string) ?? null,
    review_status: (r.review_status as string) ?? "",
    confidence: (r.confidence as string) ?? "",
    source_url: (sourceMap.get(r.source_id as number)?.source_url as string) ?? null,
  }));

  return { entities: entityRows, events: eventRows, relationships: relRows };
}
