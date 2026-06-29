import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { checkOrigin, getActor } from "@/lib/internal-audit";
import { isEntityType } from "@/lib/ownership-review";
import {
  reviewEntity,
  setEntityType,
  reviewOwnershipEvent,
  reviewRelationship,
  type ActionResult,
} from "@/lib/ownership-review-actions";

/**
 * Internal Ownership Review mutation endpoint (Phase 17B.6).
 *
 * Auth: gated by middleware (HTTP Basic Auth on /api/internal/*).
 * CSRF: cross-origin browser POSTs rejected via Origin check.
 * Scope: review-status changes + operator-approved entity_type changes only.
 *
 * Body:
 *   { kind: "entity",       id, action: "approve" | "reject" }
 *   { kind: "entity",       id, action: "set_type", entity_type }
 *   { kind: "event",        id, action: "approve" | "reject" }
 *   { kind: "relationship", id, action: "approve" | "reject" }
 */
export async function POST(request: NextRequest) {
  if (!checkOrigin(request.headers.get("origin"), request.headers.get("host") ?? "")) {
    return NextResponse.json({ error: "Cross-origin request rejected." }, { status: 403 });
  }

  let body: {
    kind?: string;
    id?: number;
    action?: string;
    entity_type?: string;
  };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const { kind, id, action, entity_type } = body;
  const recordId = typeof id === "number" ? id : parseInt(String(id), 10);
  if (!recordId || isNaN(recordId)) {
    return NextResponse.json({ error: "Valid numeric id is required." }, { status: 400 });
  }
  if (!kind || !["entity", "event", "relationship"].includes(kind)) {
    return NextResponse.json(
      { error: "kind must be one of: entity, event, relationship." },
      { status: 400 }
    );
  }
  if (!action || !["approve", "reject", "set_type"].includes(action)) {
    return NextResponse.json(
      { error: "action must be one of: approve, reject, set_type." },
      { status: 400 }
    );
  }
  if (action === "set_type") {
    if (kind !== "entity") {
      return NextResponse.json(
        { error: "set_type is only valid for kind=entity." },
        { status: 400 }
      );
    }
    if (!entity_type || !isEntityType(entity_type)) {
      return NextResponse.json(
        { error: "set_type requires a valid entity_type." },
        { status: 400 }
      );
    }
  }

  const db = getSupabaseServer();
  const actor = getActor();
  const decision = action === "reject" ? "reject" : "approve";

  let result: ActionResult;
  if (kind === "entity") {
    result =
      action === "set_type"
        ? await setEntityType(db, recordId, entity_type as string, actor)
        : await reviewEntity(db, recordId, decision, actor);
  } else if (kind === "event") {
    result = await reviewOwnershipEvent(db, recordId, decision, actor);
  } else {
    result = await reviewRelationship(db, recordId, decision, actor);
  }

  if (!result.ok) {
    return NextResponse.json({ error: result.error }, { status: 500 });
  }
  return NextResponse.json({ ok: true });
}
