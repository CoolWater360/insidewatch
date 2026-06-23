/**
 * Tests for lib/api-auth.ts — Bearer token auth helper.
 * Pure unit tests: no Next.js runtime, no database.
 */

import { checkApiKey, hashApiKey } from "../../lib/api-auth";

const KEY = "test-api-key-abc123";
const validHeader = `Bearer ${KEY}`;

describe("checkApiKey", () => {
  it("returns 'no-key' when INSIDEWATCH_API_KEY is not set", () => {
    expect(checkApiKey(validHeader, null)).toBe("no-key");
  });

  it("returns 'no-key' when apiKey is empty string", () => {
    expect(checkApiKey(validHeader, "")).toBe("no-key");
  });

  it("returns 'no-auth' when Authorization header is absent", () => {
    expect(checkApiKey(null, KEY)).toBe("no-auth");
  });

  it("returns 'no-auth' when Authorization header is not Bearer", () => {
    expect(checkApiKey("Basic dXNlcjpwYXNz", KEY)).toBe("no-auth");
  });

  it("returns 'no-auth' when Bearer token is empty", () => {
    expect(checkApiKey("Bearer ", KEY)).toBe("no-auth");
  });

  it("returns 'ok' with valid token", () => {
    expect(checkApiKey(validHeader, KEY)).toBe("ok");
  });

  it("returns 'invalid' with wrong token", () => {
    expect(checkApiKey("Bearer wrong-token-xyz", KEY)).toBe("invalid");
  });

  it("is case-sensitive", () => {
    expect(checkApiKey(`Bearer ${KEY.toUpperCase()}`, KEY)).toBe("invalid");
  });

  it("returns 'invalid' when token length differs from key", () => {
    expect(checkApiKey(`Bearer ${KEY}extra`, KEY)).toBe("invalid");
  });
});

describe("hashApiKey", () => {
  it("returns a 64-character hex string", () => {
    const hash = hashApiKey(KEY);
    expect(hash).toHaveLength(64);
    expect(hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it("is deterministic", () => {
    expect(hashApiKey(KEY)).toBe(hashApiKey(KEY));
  });

  it("produces different hashes for different keys", () => {
    expect(hashApiKey(KEY)).not.toBe(hashApiKey("different-key"));
  });
});
