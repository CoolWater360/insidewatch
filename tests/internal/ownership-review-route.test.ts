/**
 * Authorization-boundary + input-validation tests for the Ownership Review
 * mutation endpoint (app/api/internal/ownership-review/route).
 *
 * The action module and Supabase client are mocked so we exercise only the
 * route's CSRF/origin guard, validation, and dispatch — no DB, no network.
 * (Basic-Auth gating is enforced by middleware and covered by api-auth tests.)
 */

import { NextRequest } from "next/server";

jest.mock("../../lib/supabase-server", () => ({
  getSupabaseServer: jest.fn(() => ({})),
}));

jest.mock("../../lib/ownership-review-actions", () => ({
  __esModule: true,
  reviewEntity: jest.fn().mockResolvedValue({ ok: true }),
  setEntityType: jest.fn().mockResolvedValue({ ok: true }),
  reviewOwnershipEvent: jest.fn().mockResolvedValue({ ok: true }),
  reviewRelationship: jest.fn().mockResolvedValue({ ok: true }),
}));

import { POST } from "../../app/api/internal/ownership-review/route";
import * as actions from "../../lib/ownership-review-actions";

const mocks = actions as unknown as {
  reviewEntity: jest.Mock;
  setEntityType: jest.Mock;
  reviewOwnershipEvent: jest.Mock;
  reviewRelationship: jest.Mock;
};

function makeReq(
  body: unknown,
  opts: { origin?: string; host?: string } = {}
): NextRequest {
  const host = opts.host ?? "localhost";
  const headers = new Headers({ "content-type": "application/json", host });
  if (opts.origin !== undefined) headers.set("origin", opts.origin);
  return new NextRequest("http://localhost/api/internal/ownership-review", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

describe("POST /api/internal/ownership-review", () => {
  beforeEach(() => {
    mocks.reviewEntity.mockClear();
    mocks.setEntityType.mockClear();
    mocks.reviewOwnershipEvent.mockClear();
    mocks.reviewRelationship.mockClear();
  });

  it("rejects cross-origin requests (CSRF) with 403", async () => {
    const res = await POST(
      makeReq({ kind: "entity", id: 1, action: "approve" }, {
        origin: "http://evil.example",
        host: "localhost",
      })
    );
    expect(res.status).toBe(403);
    expect(mocks.reviewEntity).not.toHaveBeenCalled();
  });

  it("allows same-origin and dispatches approve→entity", async () => {
    const res = await POST(
      makeReq({ kind: "entity", id: 1, action: "approve" }, {
        origin: "http://localhost",
        host: "localhost",
      })
    );
    expect(res.status).toBe(200);
    expect(mocks.reviewEntity).toHaveBeenCalledTimes(1);
  });

  it("allows non-browser requests with no Origin header", async () => {
    const res = await POST(makeReq({ kind: "entity", id: 1, action: "approve" }));
    expect(res.status).toBe(200);
  });

  it("rejects an invalid action with 400", async () => {
    const res = await POST(makeReq({ kind: "entity", id: 1, action: "delete" }));
    expect(res.status).toBe(400);
  });

  it("rejects an invalid kind with 400", async () => {
    const res = await POST(makeReq({ kind: "issuer", id: 1, action: "approve" }));
    expect(res.status).toBe(400);
  });

  it("rejects set_type on a non-entity kind with 400", async () => {
    const res = await POST(
      makeReq({ kind: "event", id: 1, action: "set_type", entity_type: "company" })
    );
    expect(res.status).toBe(400);
    expect(mocks.reviewOwnershipEvent).not.toHaveBeenCalled();
  });

  it("rejects set_type with an invalid entity_type with 400", async () => {
    const res = await POST(
      makeReq({ kind: "entity", id: 1, action: "set_type", entity_type: "bogus" })
    );
    expect(res.status).toBe(400);
    expect(mocks.setEntityType).not.toHaveBeenCalled();
  });

  it("dispatches a valid set_type to setEntityType", async () => {
    const res = await POST(
      makeReq({ kind: "entity", id: 3, action: "set_type", entity_type: "company" })
    );
    expect(res.status).toBe(200);
    expect(mocks.setEntityType).toHaveBeenCalledWith(
      expect.anything(), 3, "company", expect.any(String)
    );
  });

  it("rejects a missing/invalid id with 400", async () => {
    const res = await POST(makeReq({ kind: "entity", action: "approve" }));
    expect(res.status).toBe(400);
  });

  it("dispatches event and relationship kinds", async () => {
    await POST(makeReq({ kind: "event", id: 2, action: "approve" }));
    await POST(makeReq({ kind: "relationship", id: 3, action: "reject" }));
    expect(mocks.reviewOwnershipEvent).toHaveBeenCalledTimes(1);
    expect(mocks.reviewRelationship).toHaveBeenCalledTimes(1);
  });
});
