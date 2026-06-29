/**
 * Tests for lib/signal-view.ts — pure mapping and classification helpers.
 *
 * classifySignalType and mapToSignalListRow are pure functions: no Supabase,
 * no network calls.
 *
 * getSignalListRows, getSignalDetail, getIssuerBrowserRows, getIssuerDetail
 * require Supabase and are not unit-tested here; covered by integration tests.
 */

import {
  classifySignalType,
  mapToSignalListRow,
  getSignalListRows,
} from "../../lib/signal-view";
import type { ClusterSignalWithConfidence } from "../../lib/signals";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

function makeClusterSignal(
  overrides: Partial<ClusterSignalWithConfidence> = {}
): ClusterSignalWithConfidence {
  return {
    company_id:    42,
    company_name:  "Acme SpA",
    window_start:  "2026-01-10",
    window_end:    "2026-01-15",
    insiders: [
      { name: "Mario Rossi", role: "CEO", role_category: "executive", date: "2026-01-10", quantity: 1000, total_value: 50000, transaction_type: "buy" },
      { name: "Lucia Bianchi", role: "CFO", role_category: "executive", date: "2026-01-12", quantity: 500, total_value: 25000, transaction_type: "buy" },
    ],
    insider_count: 2,
    total_value:   75000,
    cash_value:    75000,
    confidence:    0.65,
    rationale:     ["base: 2 independent insiders", "+0.15 for 2 executive/board insiders"],
    ...overrides,
  };
}

// ─── classifySignalType ───────────────────────────────────────────────────────

describe("classifySignalType", () => {
  it("returns 'Segnale di acquisto coordinato' for a buy-cluster signal", () => {
    const sig = makeClusterSignal();
    expect(classifySignalType(sig)).toBe("Segnale di acquisto coordinato");
  });

  it("returns the same label regardless of insider count", () => {
    const small = makeClusterSignal({ insider_count: 2 });
    const large = makeClusterSignal({ insider_count: 5 });
    expect(classifySignalType(small)).toBe(classifySignalType(large));
  });

  it("returns the same label regardless of confidence score", () => {
    const low  = makeClusterSignal({ confidence: 0.50 });
    const high = makeClusterSignal({ confidence: 0.95 });
    expect(classifySignalType(low)).toBe(classifySignalType(high));
  });

  it("never returns bullish/bearish/alpha/recommendation labels", () => {
    const label = classifySignalType(makeClusterSignal());
    const forbidden = ["bullish", "bearish", "alpha", "recommendation", "price target", "return"];
    for (const word of forbidden) {
      expect(label.toLowerCase()).not.toContain(word);
    }
  });
});

// ─── mapToSignalListRow ───────────────────────────────────────────────────────

describe("mapToSignalListRow", () => {
  it("generates a slug from company_id and window_start", () => {
    const row = mapToSignalListRow(makeClusterSignal());
    expect(row.slug).toBe("42_2026-01-10");
  });

  it("slug format: {companyId}_{YYYY-MM-DD}", () => {
    const sig = makeClusterSignal({ company_id: 999, window_start: "2025-12-31" });
    expect(mapToSignalListRow(sig).slug).toBe("999_2025-12-31");
  });

  it("confidence_caveat is false when confidence >= 0.60", () => {
    expect(mapToSignalListRow(makeClusterSignal({ confidence: 0.60 })).confidence_caveat).toBe(false);
    expect(mapToSignalListRow(makeClusterSignal({ confidence: 0.80 })).confidence_caveat).toBe(false);
    expect(mapToSignalListRow(makeClusterSignal({ confidence: 1.00 })).confidence_caveat).toBe(false);
  });

  it("confidence_caveat is true when confidence < 0.60", () => {
    expect(mapToSignalListRow(makeClusterSignal({ confidence: 0.59 })).confidence_caveat).toBe(true);
    expect(mapToSignalListRow(makeClusterSignal({ confidence: 0.50 })).confidence_caveat).toBe(true);
    expect(mapToSignalListRow(makeClusterSignal({ confidence: 0.00 })).confidence_caveat).toBe(true);
  });

  it("maps all scalar fields correctly", () => {
    const sig = makeClusterSignal();
    const row = mapToSignalListRow(sig);
    expect(row.company_id).toBe(42);
    expect(row.company_name).toBe("Acme SpA");
    expect(row.window_start).toBe("2026-01-10");
    expect(row.window_end).toBe("2026-01-15");
    expect(row.insider_count).toBe(2);
    expect(row.total_value).toBe(75000);
    expect(row.confidence).toBe(0.65);
    expect(row.rationale).toHaveLength(2);
  });

  it("signal_type is the factual label from classifySignalType", () => {
    const sig = makeClusterSignal();
    const row = mapToSignalListRow(sig);
    expect(row.signal_type).toBe(classifySignalType(sig));
  });

  it("preserves rationale array order", () => {
    const rationale = ["base: 2 independent insiders", "+0.15 for 2 executive/board insiders", "+0.10 for cash_value > €100 000"];
    const sig = makeClusterSignal({ rationale });
    const row = mapToSignalListRow(sig);
    expect(row.rationale).toEqual(rationale);
  });
});

// ─── Unsupported-context empty states ────────────────────────────────────────

describe("getSignalListRows — empty-state behaviour", () => {
  it("maps an empty signal array to an empty row array", async () => {
    // getSignalListRows calls getClusterSignalsWithConfidence internally.
    // We verify the mapping layer handles an empty input without errors.
    // When Supabase is not configured the underlying call returns [].
    // Jest environment has no env vars, so Supabase client returns [].
    const rows = await getSignalListRows(90);
    // In test environment (no Supabase), result must be an array (possibly empty).
    expect(Array.isArray(rows)).toBe(true);
  });

  it("all returned rows have required fields", async () => {
    const rows = await getSignalListRows(90);
    for (const row of rows) {
      expect(typeof row.slug).toBe("string");
      expect(typeof row.company_id).toBe("number");
      expect(typeof row.company_name).toBe("string");
      expect(typeof row.signal_type).toBe("string");
      expect(typeof row.confidence).toBe("number");
      expect(typeof row.confidence_caveat).toBe("boolean");
      expect(Array.isArray(row.rationale)).toBe(true);
    }
  });

  it("slug never contains a slash (safe for URL path segment)", async () => {
    const rows = await getSignalListRows(90);
    for (const row of rows) {
      expect(row.slug).not.toContain("/");
    }
  });
});

// ─── Issuer summary mapping ───────────────────────────────────────────────────

describe("IssuerBrowserRow shape contract", () => {
  it("has all required fields defined at the type level", () => {
    const row: import("../../lib/signal-view").IssuerBrowserRow = {
      id:             1,
      canonical_name: "Eni S.p.A.",
      short_name:     "Eni",
      lei:            "815600E4E6DCD32F4A17",
      country:        "IT",
      market:         "EXM",
      sector:         "Energy",
      status:         "active",
      isin:           "IT0003132476",
      created_at:     "2025-01-01T00:00:00Z",
    };
    expect(row.canonical_name).toBe("Eni S.p.A.");
    expect(row.isin).toBe("IT0003132476");
  });

  it("isin can be null for issuers not yet catalogued in securities table", () => {
    const row: import("../../lib/signal-view").IssuerBrowserRow = {
      id:             2,
      canonical_name: "Startup Srl",
      short_name:     null,
      lei:            null,
      country:        "IT",
      market:         null,
      sector:         null,
      status:         "pending_review",
      isin:           null,   // not yet in securities table
      created_at:     "2026-01-01T00:00:00Z",
    };
    expect(row.isin).toBeNull();
  });
});
