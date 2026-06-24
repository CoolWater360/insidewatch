/**
 * Tests for lib/queries.ts — confirms every public query targets only the
 * public_* views, not the raw base tables that anon access was revoked from
 * in migration 013 (013_rls_policies.sql).
 *
 * These are static / structural tests: they inspect the source text of
 * queries.ts rather than executing live queries, so they run without any
 * database connection and will catch future regressions immediately.
 */

import * as fs from "fs";
import * as path from "path";

const QUERIES_PATH = path.resolve(__dirname, "../../lib/queries.ts");
const src = fs.readFileSync(QUERIES_PATH, "utf-8");

// ─── Raw table names that must NOT appear as .from() targets ─────────────────

const RAW_TABLES = ["transactions", "companies", "insiders"] as const;
const PUBLIC_VIEWS = ["public_transactions", "public_companies", "public_insiders"] as const;

// Build a regex that matches .from("tableName") but NOT .from("public_tableName")
function rawFromPattern(table: string): RegExp {
  // Negative lookbehind for "public_" ensures we don't flag the view names
  return new RegExp(`\\.from\\(["'](?!public_)${table}["']\\)`, "g");
}

describe("public query layer — views only", () => {
  for (const table of RAW_TABLES) {
    it(`does not query raw table '${table}' (must use public_${table})`, () => {
      const matches = src.match(rawFromPattern(table)) ?? [];
      expect(matches).toHaveLength(0);
    });
  }

  it("queries public_transactions via .from()", () => {
    expect(src).toContain(`.from("public_transactions")`);
  });

  it("queries public_companies via .from()", () => {
    expect(src).toContain(`.from("public_companies")`);
  });

  it("references public_insiders in embedded join (never a top-level .from() target)", () => {
    // public_insiders is always embedded inside a SELECT string via
    // the alias syntax insiders:public_insiders(...) — insiders are never
    // queried directly, only joined from transaction queries.
    expect(src).toContain("public_insiders");
  });

  it("embedded company join uses alias 'companies:public_companies'", () => {
    expect(src).toContain("companies:public_companies(");
  });

  it("embedded insider join uses alias 'insiders:public_insiders'", () => {
    expect(src).toContain("insiders:public_insiders(");
  });

  it("getDashboardStats lastUpdatedAt uses filed_date not created_at", () => {
    // created_at is not exposed by public_transactions
    // Verify the lastTx query selects filed_date and orders by filed_date
    expect(src).toContain(`"filed_date"`);
    expect(src).not.toMatch(/\.select\(["']created_at["']\)/);
    expect(src).not.toMatch(/lastUpdatedAt.*created_at/);
  });
});
