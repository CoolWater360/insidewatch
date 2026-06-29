/**
 * Tests for the Phase 17B.6 Ownership Review Queue:
 *   - pure recommendation logic (lib/ownership-review)
 *   - server-side mutation actions (lib/ownership-review-actions)
 *
 * (Route authorization/validation is covered in ownership-review-route.test.ts,
 * which mocks the action module — kept separate so these tests exercise the
 * REAL action functions.)
 *
 * The Supabase client is mocked throughout — no network, no DB.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import {
  recommendEntityType,
  isEntityType,
  ENTITY_TYPES,
} from "../../lib/ownership-review";
import {
  reviewEntity,
  setEntityType,
  reviewOwnershipEvent,
  reviewRelationship,
} from "../../lib/ownership-review-actions";

// ─── Pure recommendation logic ─────────────────────────────────────────────────

describe("recommendEntityType", () => {
  it("flags an organisation tagged natural_person", () => {
    const rec = recommendEntityType("GOLDMAN SACHS INTERNATIONAL", "natural_person");
    expect(rec).not.toBeNull();
    expect(rec!.proposedType).toBe("company");
    expect(rec!.reason).toContain("INTERNATIONAL");
  });

  it("flags MORGAN STANLEY", () => {
    expect(recommendEntityType("MORGAN STANLEY", "natural_person")?.proposedType).toBe("company");
  });

  it("does not flag a real person name", () => {
    expect(recommendEntityType("Mario Rossi", "natural_person")).toBeNull();
    expect(recommendEntityType("Brunello Cucinelli", "natural_person")).toBeNull();
  });

  it("never touches an entity already typed company", () => {
    expect(recommendEntityType("GOLDMAN SACHS BANK EUROPE SE", "company")).toBeNull();
  });

  it("never re-types a non-person", () => {
    expect(recommendEntityType("ANYTHING INTERNATIONAL", "holding_company")).toBeNull();
  });
});

describe("isEntityType", () => {
  it("accepts the valid vocabulary", () => {
    for (const t of ENTITY_TYPES) expect(isEntityType(t)).toBe(true);
  });
  it("rejects invalid values", () => {
    expect(isEntityType("person")).toBe(false);
    expect(isEntityType("")).toBe(false);
    expect(isEntityType("DROP TABLE")).toBe(false);
  });
});

// ─── Mutation actions ──────────────────────────────────────────────────────────

interface Capture {
  table?: string;
  payload?: Record<string, unknown>;
  eqs: Array<[string, unknown]>;
}

function captureDb(error: { message: string } | null = null): {
  db: SupabaseClient;
  cap: Capture;
} {
  const cap: Capture = { eqs: [] };
  const chain: Record<string, unknown> = {};
  chain.update = jest.fn((payload: Record<string, unknown>) => {
    cap.payload = payload;
    return chain;
  });
  chain.eq = jest.fn((col: string, val: unknown) => {
    cap.eqs.push([col, val]);
    return chain;
  });
  // Make the chain awaitable (resolves like a PostgREST builder).
  (chain as { then: unknown }).then = (
    resolve: (v: { error: unknown }) => void
  ) => Promise.resolve({ error }).then(resolve);
  const db = {
    from: jest.fn((t: string) => {
      cap.table = t;
      return chain;
    }),
  } as unknown as SupabaseClient;
  return { db, cap };
}

describe("reviewEntity", () => {
  it("approve → review_status confirmed on entities", async () => {
    const { db, cap } = captureDb();
    const r = await reviewEntity(db, 1, "approve", "tester");
    expect(r.ok).toBe(true);
    expect(cap.table).toBe("entities");
    expect(cap.payload!.review_status).toBe("confirmed");
    expect(cap.payload!.entity_type).toBeUndefined(); // raw fact untouched
    expect(cap.eqs).toContainEqual(["id", 1]);
  });

  it("reject → review_status rejected", async () => {
    const { db, cap } = captureDb();
    await reviewEntity(db, 2, "reject", "tester");
    expect(cap.payload!.review_status).toBe("rejected");
  });

  it("surfaces DB errors", async () => {
    const { db } = captureDb({ message: "boom" });
    const r = await reviewEntity(db, 1, "approve", "tester");
    expect(r).toEqual({ ok: false, error: "boom" });
  });
});

describe("setEntityType", () => {
  it("rejects an invalid type without touching the DB", async () => {
    const { db, cap } = captureDb();
    const r = await setEntityType(db, 1, "not-a-type", "tester");
    expect(r.ok).toBe(false);
    expect(cap.table).toBeUndefined();
  });

  it("applies a valid type and marks confirmed", async () => {
    const { db, cap } = captureDb();
    const r = await setEntityType(db, 3, "company", "tester");
    expect(r.ok).toBe(true);
    expect(cap.table).toBe("entities");
    expect(cap.payload!.entity_type).toBe("company");
    expect(cap.payload!.review_status).toBe("confirmed");
  });
});

describe("reviewOwnershipEvent", () => {
  it("writes reviewed_by/reviewed_at and only touches current rows", async () => {
    const { db, cap } = captureDb();
    const r = await reviewOwnershipEvent(db, 7, "approve", "tester");
    expect(r.ok).toBe(true);
    expect(cap.table).toBe("ownership_events");
    expect(cap.payload!.review_status).toBe("confirmed");
    expect(cap.payload!.reviewed_by).toBe("tester");
    expect(cap.payload!.reviewed_at).toBeDefined();
    expect(cap.eqs).toContainEqual(["id", 7]);
    expect(cap.eqs).toContainEqual(["is_current", true]);
    // raw facts not edited
    expect(cap.payload!.voting_pct_after).toBeUndefined();
    expect(cap.payload!.event_type).toBeUndefined();
  });
});

describe("reviewRelationship", () => {
  it("writes reviewed_by/reviewed_at on entity_relationships", async () => {
    const { db, cap } = captureDb();
    await reviewRelationship(db, 9, "reject", "tester");
    expect(cap.table).toBe("entity_relationships");
    expect(cap.payload!.review_status).toBe("rejected");
    expect(cap.payload!.reviewed_by).toBe("tester");
    expect(cap.eqs).toContainEqual(["is_current", true]);
  });
});
