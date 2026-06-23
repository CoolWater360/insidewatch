/**
 * Tests for lib/internal-audit.ts — getActor helper.
 *
 * Audit write logic moved to Postgres RPC functions (009_internal_rpc.sql).
 * Atomicity and action-level tests live in atomic-audit.test.ts.
 */

import { getActor } from "../../lib/internal-audit";

describe("getActor", () => {
  const ORIGINAL = process.env.INTERNAL_ACTOR_LABEL;

  afterEach(() => {
    if (ORIGINAL === undefined) delete process.env.INTERNAL_ACTOR_LABEL;
    else process.env.INTERNAL_ACTOR_LABEL = ORIGINAL;
  });

  it("returns 'shared-admin' when INTERNAL_ACTOR_LABEL is not set", () => {
    delete process.env.INTERNAL_ACTOR_LABEL;
    expect(getActor()).toBe("shared-admin");
  });

  it("returns the configured label when INTERNAL_ACTOR_LABEL is set", () => {
    process.env.INTERNAL_ACTOR_LABEL = "ops-team-london";
    expect(getActor()).toBe("ops-team-london");
  });
});
