/**
 * Tests for Phase 10 — /api/v1/* route handlers and helpers.
 *
 * Scope:
 *   - lib/api-rate-limit.ts  checkRateLimit behaviour
 *   - lib/api-response.ts    toCsv, parsePagination
 *   - lib/api-guard.ts       apiGuard auth + rate-limit rejection
 *   - Route handlers         auth rejection (401/503), happy-path shape
 *
 * The Supabase client is mocked throughout.  We never hit the network.
 */

import { NextRequest } from "next/server";

// ─── helpers ──────────────────────────────────────────────────────────────────

function makeRequest(
  path: string,
  opts: { auth?: string; search?: Record<string, string> } = {}
): NextRequest {
  const url = new URL(`http://localhost:3000${path}`);
  if (opts.search) {
    for (const [k, v] of Object.entries(opts.search)) url.searchParams.set(k, v);
  }
  const headers: Record<string, string> = {};
  if (opts.auth !== undefined) headers["authorization"] = opts.auth;
  return new NextRequest(url, { headers });
}

// ─── lib/api-response.ts ──────────────────────────────────────────────────────

import { toCsv, parsePagination, apiError, apiJson } from "../../lib/api-response";

describe("toCsv", () => {
  it("returns empty string for empty array", () => {
    expect(toCsv([])).toBe("");
  });

  it("produces header row + data rows", () => {
    const result = toCsv([{ a: 1, b: 2 }]);
    const lines = result.split("\r\n");
    expect(lines[0]).toBe("a,b");
    expect(lines[1]).toBe("1,2");
  });

  it("quotes values containing commas", () => {
    const result = toCsv([{ name: "Smith, John" }]);
    expect(result).toContain('"Smith, John"');
  });

  it("escapes double-quotes inside quoted values", () => {
    const result = toCsv([{ note: 'say "hello"' }]);
    expect(result).toContain('"say ""hello"""');
  });

  it("quotes values containing newlines", () => {
    const result = toCsv([{ note: "line1\nline2" }]);
    expect(result).toContain('"line1\nline2"');
  });

  it("renders null/undefined as empty string", () => {
    const result = toCsv([{ a: null, b: undefined }]);
    const lines = result.split("\r\n");
    expect(lines[1]).toBe(",");
  });
});

describe("parsePagination", () => {
  it("defaults to page 1, pageSize 25", () => {
    const r = parsePagination(new URLSearchParams());
    expect(r).toEqual({ page: 1, pageSize: 25, offset: 0 });
  });

  it("clamps page_size to 100", () => {
    const r = parsePagination(new URLSearchParams("page_size=999"));
    expect(r.pageSize).toBe(100);
  });

  it("clamps page to minimum 1", () => {
    const r = parsePagination(new URLSearchParams("page=0"));
    expect(r.page).toBe(1);
  });

  it("calculates offset correctly", () => {
    const r = parsePagination(new URLSearchParams("page=3&page_size=10"));
    expect(r.offset).toBe(20);
  });

  it("handles non-numeric page gracefully", () => {
    const r = parsePagination(new URLSearchParams("page=abc"));
    expect(r.page).toBe(1);
  });
});

describe("apiError", () => {
  it("returns correct status", async () => {
    const res = apiError("Not found.", 404);
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body.error).toBe("Not found.");
  });

  it("merges extra fields into body", async () => {
    const res = apiError("Rate limited.", 429, { retry_after_seconds: 60 });
    const body = await res.json();
    expect(body.retry_after_seconds).toBe(60);
  });
});

describe("apiJson", () => {
  it("wraps data in envelope", async () => {
    const res = apiJson([{ id: 1 }], { page: 1, page_size: 25, total: 1, total_pages: 1 });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.data).toEqual([{ id: 1 }]);
    expect(body.pagination.total).toBe(1);
    expect(body.meta.version).toBe("v1");
    expect(body.meta.generated_at).toBeDefined();
  });

  it("sets X-API-Version header", () => {
    const res = apiJson([], { page: 1, page_size: 25, total: 0, total_pages: 0 });
    expect(res.headers.get("x-api-version")).toBe("v1");
  });
});

// ─── lib/api-rate-limit.ts ────────────────────────────────────────────────────

import { checkRateLimit } from "../../lib/api-rate-limit";
import type { SupabaseClient } from "@supabase/supabase-js";

/** Build a thenable that synchronously resolves to `value`. */
function thenable<T>(value: T) {
  return {
    then(resolve: (v: T) => unknown, _reject?: (e: unknown) => unknown) {
      return resolve(value);
    },
  };
}

function mockDb(countValue: number): SupabaseClient {
  const selectChain = {
    eq:  jest.fn().mockReturnThis(),
    gte: jest.fn().mockReturnThis(),
    then(resolve: (v: unknown) => unknown) { return resolve({ count: countValue, error: null }); },
  };
  const insertChain = {
    then(resolve: ((v: unknown) => unknown) | null, _reject?: (e: unknown) => unknown) {
      if (typeof resolve === "function") return resolve({ data: null, error: null });
    },
  };
  const fromFn = jest.fn(() => ({
    select: jest.fn().mockReturnValue(selectChain),
    insert: jest.fn().mockReturnValue(insertChain),
  }));
  return { from: fromFn } as unknown as SupabaseClient;
}

describe("checkRateLimit", () => {
  const originalEnv = process.env;
  beforeEach(() => { process.env = { ...originalEnv, RATE_LIMIT_RPM: "5" }; });
  afterEach(() => { process.env = originalEnv; });

  it("allows request when under the limit", async () => {
    const db = mockDb(3);
    const result = await checkRateLimit(db, "hash123", "/api/v1/test", performance.now());
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(1); // 5 - 3 - 1
    expect(result.limit).toBe(5);
  });

  it("blocks request when at limit", async () => {
    const db = mockDb(5);
    const result = await checkRateLimit(db, "hash123", "/api/v1/test", performance.now());
    expect(result.allowed).toBe(false);
    expect(result.remaining).toBe(0);
  });

  it("blocks request when over limit", async () => {
    const db = mockDb(10);
    const result = await checkRateLimit(db, "hash123", "/api/v1/test", performance.now());
    expect(result.allowed).toBe(false);
  });

  it("uses custom window via opts.windowSeconds", async () => {
    const db = mockDb(0);
    const result = await checkRateLimit(db, "hash123", "/api/v1/test", performance.now(), {
      windowSeconds: 120,
    });
    expect(result.allowed).toBe(true);
  });

  it("exposes resetAt as ISO 8601 string", async () => {
    const db = mockDb(0);
    const result = await checkRateLimit(db, "hash123", "/api/v1/test", performance.now());
    expect(() => new Date(result.resetAt)).not.toThrow();
    expect(result.resetAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });
});

// ─── lib/api-guard.ts — auth rejection paths ──────────────────────────────────

import { apiGuard } from "../../lib/api-guard";

function makeDbForGuard(countValue = 0): SupabaseClient {
  return mockDb(countValue);
}

describe("apiGuard", () => {
  const originalEnv = process.env;

  afterEach(() => { process.env = originalEnv; });

  it("returns 503 when INSIDEWATCH_API_KEY is not set", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: undefined };
    const req = makeRequest("/api/v1/transactions");
    const db = makeDbForGuard();
    const result = await apiGuard(req, db, "/api/v1/transactions");
    expect(result.error).not.toBeNull();
    expect(result.error!.status).toBe(503);
  });

  it("returns 401 when Authorization header is missing", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "secret" };
    const req = makeRequest("/api/v1/transactions"); // no auth header
    const db = makeDbForGuard();
    const result = await apiGuard(req, db, "/api/v1/transactions");
    expect(result.error).not.toBeNull();
    expect(result.error!.status).toBe(401);
  });

  it("returns 401 when token is wrong", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "correct-secret" };
    const req = makeRequest("/api/v1/transactions", { auth: "Bearer wrong-token" });
    const db = makeDbForGuard();
    const result = await apiGuard(req, db, "/api/v1/transactions");
    expect(result.error).not.toBeNull();
    expect(result.error!.status).toBe(401);
  });

  it("returns ctx when auth is valid and under rate limit", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "my-api-key", RATE_LIMIT_RPM: "60" };
    const req = makeRequest("/api/v1/transactions", { auth: "Bearer my-api-key" });
    const db = makeDbForGuard(0);
    const result = await apiGuard(req, db, "/api/v1/transactions");
    expect(result.error).toBeNull();
    expect(result.ctx).not.toBeNull();
    expect(result.ctx!.apiKeyHash).toHaveLength(64);
  });

  it("returns 429 when rate limit is exceeded", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "my-api-key", RATE_LIMIT_RPM: "1" };
    const req = makeRequest("/api/v1/transactions", { auth: "Bearer my-api-key" });
    const db = makeDbForGuard(5); // already 5 requests, limit is 1
    const result = await apiGuard(req, db, "/api/v1/transactions");
    expect(result.error).not.toBeNull();
    expect(result.error!.status).toBe(429);
  });
});

// ─── Route handlers — auth rejection ─────────────────────────────────────────
//
// We mock getSupabaseServer so routes don't need real env vars for the DB
// connection.  We set INSIDEWATCH_API_KEY to control auth behaviour.

jest.mock("../../lib/supabase-server", () => ({
  getSupabaseServer: jest.fn(),
}));

jest.mock("../../lib/queries", () => ({
  getClusterSignals: jest.fn().mockResolvedValue([]),
}));

import { getSupabaseServer } from "../../lib/supabase-server";
import { GET as getTransactions } from "../../app/api/v1/transactions/route";
import { GET as getTransactionById } from "../../app/api/v1/transactions/[id]/route";
import { GET as getFilingById } from "../../app/api/v1/filings/[id]/route";
import { GET as getIssuers } from "../../app/api/v1/issuers/route";
import { GET as getIssuerById } from "../../app/api/v1/issuers/[id]/route";
import { GET as getClusterBuys } from "../../app/api/v1/signals/cluster-buys/route";

function routeDb(countValue = 0, singleData: unknown = null): SupabaseClient {
  const insertChain = { then: jest.fn().mockReturnThis(), catch: jest.fn() };
  const chain = {
    select: jest.fn().mockReturnThis(),
    eq:     jest.fn().mockReturnThis(),
    gte:    jest.fn().mockReturnThis(),
    or:     jest.fn().mockReturnThis(),
    order:  jest.fn().mockReturnThis(),
    range:  jest.fn().mockResolvedValue({ data: [], count: countValue, error: null }),
    single: jest.fn().mockResolvedValue({ data: singleData, error: singleData ? null : { message: "not found" } }),
    insert: jest.fn().mockReturnValue(insertChain),
  };
  return { from: jest.fn(() => chain) } as unknown as SupabaseClient;
}

beforeEach(() => {
  (getSupabaseServer as jest.Mock).mockReturnValue(routeDb());
});

describe("GET /api/v1/transactions", () => {
  const originalEnv = process.env;
  afterEach(() => { process.env = originalEnv; });

  it("returns 401 when no auth", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key" };
    const req = makeRequest("/api/v1/transactions");
    const res = await getTransactions(req);
    expect(res.status).toBe(401);
  });

  it("returns 200 with envelope on valid auth", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key", RATE_LIMIT_RPM: "60" };
    const req = makeRequest("/api/v1/transactions", { auth: "Bearer key" });
    const res = await getTransactions(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty("data");
    expect(body).toHaveProperty("pagination");
    expect(body).toHaveProperty("meta");
  });
});

describe("GET /api/v1/transactions/:id", () => {
  const originalEnv = process.env;
  afterEach(() => { process.env = originalEnv; });

  it("returns 401 when no auth", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key" };
    const req = makeRequest("/api/v1/transactions/1");
    const res = await getTransactionById(req, { params: Promise.resolve({ id: "1" }) });
    expect(res.status).toBe(401);
  });

  it("returns 400 for non-numeric id", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key", RATE_LIMIT_RPM: "60" };
    const req = makeRequest("/api/v1/transactions/abc", { auth: "Bearer key" });
    const res = await getTransactionById(req, { params: Promise.resolve({ id: "abc" }) });
    expect(res.status).toBe(400);
  });

  it("returns 404 when transaction not found", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key", RATE_LIMIT_RPM: "60" };
    const req = makeRequest("/api/v1/transactions/9999", { auth: "Bearer key" });
    const res = await getTransactionById(req, { params: Promise.resolve({ id: "9999" }) });
    expect(res.status).toBe(404);
  });
});

describe("GET /api/v1/filings/:id", () => {
  const originalEnv = process.env;
  afterEach(() => { process.env = originalEnv; });

  it("returns 401 when no auth", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key" };
    const req = makeRequest("/api/v1/filings/1");
    const res = await getFilingById(req, { params: Promise.resolve({ id: "1" }) });
    expect(res.status).toBe(401);
  });

  it("returns 404 when filing not found", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key", RATE_LIMIT_RPM: "60" };
    const req = makeRequest("/api/v1/filings/9999", { auth: "Bearer key" });
    const res = await getFilingById(req, { params: Promise.resolve({ id: "9999" }) });
    expect(res.status).toBe(404);
  });
});

describe("GET /api/v1/issuers", () => {
  const originalEnv = process.env;
  afterEach(() => { process.env = originalEnv; });

  it("returns 401 when no auth", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key" };
    const req = makeRequest("/api/v1/issuers");
    const res = await getIssuers(req);
    expect(res.status).toBe(401);
  });

  it("returns 400 for invalid status param", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key", RATE_LIMIT_RPM: "60" };
    const req = makeRequest("/api/v1/issuers", { auth: "Bearer key", search: { status: "bogus" } });
    const res = await getIssuers(req);
    expect(res.status).toBe(400);
  });

  it("returns 200 with envelope on valid auth", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key", RATE_LIMIT_RPM: "60" };
    const req = makeRequest("/api/v1/issuers", { auth: "Bearer key" });
    const res = await getIssuers(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty("data");
    expect(body.pagination).toBeDefined();
  });
});

describe("GET /api/v1/issuers/:id", () => {
  const originalEnv = process.env;
  afterEach(() => { process.env = originalEnv; });

  it("returns 401 when no auth", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key" };
    const req = makeRequest("/api/v1/issuers/1");
    const res = await getIssuerById(req, { params: Promise.resolve({ id: "1" }) });
    expect(res.status).toBe(401);
  });

  it("returns 404 when issuer not found", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key", RATE_LIMIT_RPM: "60" };
    const req = makeRequest("/api/v1/issuers/9999", { auth: "Bearer key" });
    const res = await getIssuerById(req, { params: Promise.resolve({ id: "9999" }) });
    expect(res.status).toBe(404);
  });
});

describe("GET /api/v1/signals/cluster-buys", () => {
  const originalEnv = process.env;
  afterEach(() => { process.env = originalEnv; });

  it("returns 401 when no auth", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key" };
    const req = makeRequest("/api/v1/signals/cluster-buys");
    const res = await getClusterBuys(req);
    expect(res.status).toBe(401);
  });

  it("returns 200 with signals array on valid auth", async () => {
    process.env = { ...originalEnv, INSIDEWATCH_API_KEY: "key", RATE_LIMIT_RPM: "60" };
    const req = makeRequest("/api/v1/signals/cluster-buys", { auth: "Bearer key" });
    const res = await getClusterBuys(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(Array.isArray(body.data)).toBe(true);
  });
});
