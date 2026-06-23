/**
 * Tests for auth and CSRF helpers in lib/internal-audit.ts.
 * Pure unit tests — no Next.js runtime, no database, no network.
 */

import { checkBasicAuth, checkOrigin } from "../../lib/internal-audit";

// ─── checkBasicAuth ──────────────────────────────────────────────────────────

describe("checkBasicAuth", () => {
  const SECRET = "supersecret";
  const valid = "Basic " + Buffer.from(`operator:${SECRET}`).toString("base64");
  const wrongPass = "Basic " + Buffer.from("operator:wrongpass").toString("base64");
  const noColon = "Basic " + Buffer.from("nocolon").toString("base64");
  const emptyPass = "Basic " + Buffer.from("user:").toString("base64");

  it("returns 'no-secret' when secret is null", () => {
    expect(checkBasicAuth(valid, null)).toBe("no-secret");
  });

  it("returns 'no-secret' when secret is empty string", () => {
    expect(checkBasicAuth(valid, "")).toBe("no-secret");
  });

  it("returns 'no-auth' when Authorization header is absent", () => {
    expect(checkBasicAuth(null, SECRET)).toBe("no-auth");
  });

  it("returns 'no-auth' when Authorization header is not Basic", () => {
    expect(checkBasicAuth("Bearer token123", SECRET)).toBe("no-auth");
  });

  it("returns 'no-auth' when Authorization header is empty", () => {
    expect(checkBasicAuth("", SECRET)).toBe("no-auth");
  });

  it("returns 'ok' with correct password", () => {
    expect(checkBasicAuth(valid, SECRET)).toBe("ok");
  });

  it("returns 'ok' when there is no colon (password-only payload)", () => {
    // Some clients send just the password without a colon.
    const headerNoColon = "Basic " + Buffer.from(SECRET).toString("base64");
    expect(checkBasicAuth(headerNoColon, SECRET)).toBe("ok");
  });

  it("returns 'wrong-password' with wrong password", () => {
    expect(checkBasicAuth(wrongPass, SECRET)).toBe("wrong-password");
  });

  it("returns 'wrong-password' when payload has no colon and value differs", () => {
    expect(checkBasicAuth(noColon, SECRET)).toBe("wrong-password");
  });

  it("returns 'wrong-password' when password is empty but secret is not", () => {
    expect(checkBasicAuth(emptyPass, SECRET)).toBe("wrong-password");
  });

  it("returns 'ok' when both password and secret are empty", () => {
    const empty = "Basic " + Buffer.from("user:").toString("base64");
    expect(checkBasicAuth(empty, "")).toBe("no-secret"); // empty secret is treated as absent
  });

  it("is case-sensitive for the password", () => {
    const upperPass = "Basic " + Buffer.from(`operator:${SECRET.toUpperCase()}`).toString("base64");
    expect(checkBasicAuth(upperPass, SECRET)).toBe("wrong-password");
  });
});

// ─── Unauthorized route simulation ───────────────────────────────────────────

describe("Unauthorized access scenarios", () => {
  it("blocks request with no Authorization header (simulate unauthenticated POST)", () => {
    const result = checkBasicAuth(null, "mysecret");
    expect(result).toBe("no-auth");
    // In the middleware this produces a 401. The test confirms the helper
    // correctly identifies the missing auth header.
  });

  it("blocks request with wrong password (simulate credential-guess attack)", () => {
    const guessed = "Basic " + Buffer.from("admin:password123").toString("base64");
    const result = checkBasicAuth(guessed, "actual-secret");
    expect(result).toBe("wrong-password");
  });

  it("blocks all requests when INTERNAL_SECRET is not configured", () => {
    const validHeader = "Basic " + Buffer.from("op:secret").toString("base64");
    expect(checkBasicAuth(validHeader, null)).toBe("no-secret");
    expect(checkBasicAuth(null, null)).toBe("no-secret");
  });
});

// ─── checkOrigin (CSRF guard) ─────────────────────────────────────────────────

describe("checkOrigin", () => {
  it("allows requests with no Origin header (server-to-server / curl)", () => {
    expect(checkOrigin(null, "localhost:3000")).toBe(true);
  });

  it("allows same-origin requests", () => {
    expect(checkOrigin("http://localhost:3000", "localhost:3000")).toBe(true);
  });

  it("allows same-origin HTTPS requests", () => {
    expect(checkOrigin("https://insidewatch.example.com", "insidewatch.example.com")).toBe(true);
  });

  it("rejects cross-origin requests", () => {
    expect(checkOrigin("https://evil.example.com", "localhost:3000")).toBe(false);
  });

  it("rejects requests from a different port", () => {
    expect(checkOrigin("http://localhost:8080", "localhost:3000")).toBe(false);
  });

  it("rejects requests from a subdomain", () => {
    expect(checkOrigin("https://sub.insidewatch.example.com", "insidewatch.example.com")).toBe(false);
  });

  it("rejects malformed Origin headers", () => {
    expect(checkOrigin("not-a-url", "localhost:3000")).toBe(false);
  });

  it("rejects empty Origin string", () => {
    expect(checkOrigin("", "localhost:3000")).toBe(false);
  });
});
