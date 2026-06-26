/**
 * Regression tests for getClusterSignals() in lib/queries.ts.
 *
 * Proves two things:
 *   1. A newly inserted eligible cluster transaction (2+ distinct insiders
 *      buying at the same company within 7 days) DOES appear in the output.
 *   2. noStore() is called so the function never serves a stale cached result.
 *
 * These are unit tests — the Supabase client is mocked.  The cluster
 * detection algorithm runs against controlled fixture data.
 */

import * as fs from "fs";
import * as path from "path";

// ─── Source-level test: noStore() must be called ──────────────────────────────

describe("getClusterSignals — cache opt-out", () => {
  const QUERIES_PATH = path.resolve(__dirname, "../../lib/queries.ts");
  const src = fs.readFileSync(QUERIES_PATH, "utf-8");

  it("imports unstable_noStore from next/cache", () => {
    expect(src).toContain(`from "next/cache"`);
    expect(src).toContain("unstable_noStore");
  });

  it("calls noStore() inside getClusterSignals", () => {
    // Extract just the getClusterSignals function body
    const fnStart = src.indexOf("export async function getClusterSignals");
    expect(fnStart).toBeGreaterThan(-1);
    const fnBody = src.slice(fnStart, fnStart + 600);
    expect(fnBody).toContain("noStore()");
  });
});

// ─── Mock Supabase client ─────────────────────────────────────────────────────

// We need to mock before importing getClusterSignals
jest.mock("next/cache", () => ({
  unstable_noStore: jest.fn(),
}));

let _mockData: unknown[] = [];
let _mockError: { message: string } | null = null;

const mockChain = {
  select: jest.fn(),
  eq: jest.fn(),
  gte: jest.fn(),
  order: jest.fn(),
};
// Every method returns mockChain (fluent), except order (last) returns thenable
Object.values(mockChain).forEach((fn) => (fn as jest.Mock).mockReturnValue(mockChain));

// Make the last .order() call thenable so await works
(mockChain.order as jest.Mock).mockImplementation(() => ({
  ...mockChain,
  then: (
    resolve: (v: { data: unknown[]; error: unknown }) => void,
    reject?: (e: unknown) => void
  ) =>
    Promise.resolve({ data: _mockData, error: _mockError }).then(resolve, reject),
}));

const mockSupabase = { from: jest.fn(() => mockChain) };

jest.mock("../../lib/supabase", () => ({
  isSupabaseConfigured: true,
  getSupabase: jest.fn(() => mockSupabase),
}));

function setMock(rows: unknown[], err: { message: string } | null = null): void {
  _mockData = rows;
  _mockError = err;
  (mockChain.order as jest.Mock).mockImplementation(() => ({
    ...mockChain,
    then: (
      resolve: (v: { data: unknown[]; error: unknown }) => void,
      reject?: (e: unknown) => void
    ) =>
      Promise.resolve({ data: _mockData, error: _mockError }).then(resolve, reject),
  }));
}

// ─── Cluster detection tests ──────────────────────────────────────────────────

import { getClusterSignals } from "../../lib/queries";

function makeTx(
  companyId: number,
  companyName: string,
  insiderName: string,
  date: string,
  value: number,
  transactionType: string | null = "buy"
) {
  return {
    company_id: companyId,
    transaction_date: date,
    quantity: 1000,
    total_value: value,
    transaction_type: transactionType,
    companies: { id: companyId, name: companyName },
    insiders: { full_name: insiderName, role: "Director" },
  };
}

describe("getClusterSignals — cluster detection", () => {
  it("returns empty when no buy transactions exist", async () => {
    setMock([]);
    const signals = await getClusterSignals(90);
    expect(signals).toHaveLength(0);
  });

  it("returns empty when only one insider bought at a company", async () => {
    setMock([
      makeTx(1, "Acme SpA", "Mario Rossi", "2026-06-20", 50_000),
    ]);
    const signals = await getClusterSignals(90);
    expect(signals).toHaveLength(0);
  });

  it("detects a cluster when 2 distinct insiders buy within 7 days — regression for new cluster transaction", async () => {
    // Simulate a newly completed filing that contributes the second insider buy.
    // Before this transaction was ingested, only Mario had bought (no cluster).
    // After ingestion, Giulia's buy on day +3 completes the cluster.
    setMock([
      makeTx(1, "Acme SpA", "Mario Rossi",  "2026-06-20", 80_000),
      makeTx(1, "Acme SpA", "Giulia Bianchi", "2026-06-23", 60_000), // newly ingested
    ]);
    const signals = await getClusterSignals(90);
    expect(signals).toHaveLength(1);
    expect(signals[0].company_name).toBe("Acme SpA");
    expect(signals[0].insiders).toHaveLength(2);
    expect(signals[0].total_value).toBe(140_000);
  });

  it("does not form a cluster when 2 insiders buy more than 7 days apart", async () => {
    setMock([
      makeTx(1, "Acme SpA", "Mario Rossi",    "2026-06-01", 80_000),
      makeTx(1, "Acme SpA", "Giulia Bianchi", "2026-06-10", 60_000), // 9 days apart
    ]);
    const signals = await getClusterSignals(90);
    expect(signals).toHaveLength(0);
  });

  it("excludes grant and option_exercise from cluster detection (non-cash)", async () => {
    setMock([
      makeTx(1, "Acme SpA", "Mario Rossi",    "2026-06-20", 80_000, "grant"),
      makeTx(1, "Acme SpA", "Giulia Bianchi", "2026-06-21", 60_000, "option_exercise"),
    ]);
    const signals = await getClusterSignals(90);
    expect(signals).toHaveLength(0);
  });

  it("includes a transaction with null transaction_type (legacy) in cluster detection", async () => {
    setMock([
      makeTx(1, "Acme SpA", "Mario Rossi",    "2026-06-20", 80_000, null),
      makeTx(1, "Acme SpA", "Giulia Bianchi", "2026-06-22", 60_000, null),
    ]);
    const signals = await getClusterSignals(90);
    expect(signals).toHaveLength(1);
  });

  it("returns signals sorted by window_end descending (newest cluster first)", async () => {
    setMock([
      // Older cluster: company 1
      makeTx(1, "Old Corp",  "Mario Rossi",    "2026-04-01", 100_000),
      makeTx(1, "Old Corp",  "Giulia Bianchi", "2026-04-03", 90_000),
      // Newer cluster: company 2
      makeTx(2, "New Corp",  "Anna Verdi",     "2026-06-20", 50_000),
      makeTx(2, "New Corp",  "Luca Neri",      "2026-06-22", 70_000),
    ]);
    const signals = await getClusterSignals(90);
    expect(signals).toHaveLength(2);
    expect(signals[0].company_name).toBe("New Corp");
    expect(signals[1].company_name).toBe("Old Corp");
  });

  it("returns empty and logs error on Supabase query error", async () => {
    setMock([], { message: "permission denied for table transactions" });
    const signals = await getClusterSignals(90);
    expect(signals).toHaveLength(0);
  });

  it("noStore() is called on every invocation", async () => {
    const { unstable_noStore } = jest.requireMock("next/cache") as {
      unstable_noStore: jest.Mock;
    };
    unstable_noStore.mockClear();
    setMock([]);
    await getClusterSignals(90);
    expect(unstable_noStore).toHaveBeenCalledTimes(1);
  });
});
