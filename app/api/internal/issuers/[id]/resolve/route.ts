import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { checkOrigin, getActor, logAudit } from "@/lib/internal-audit";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  // ── Route-level CSRF guard ────────────────────────────────────────────────
  if (!checkOrigin(request.headers.get("origin"), request.headers.get("host") ?? "")) {
    return NextResponse.json({ error: "Cross-origin request rejected." }, { status: 403 });
  }

  const { id } = await params;
  const unmatchedId = parseInt(id, 10);
  if (!unmatchedId || isNaN(unmatchedId)) {
    return NextResponse.json({ error: "Invalid unmatched issuer ID." }, { status: 400 });
  }

  let body: { action?: string; issuer_id?: number };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const { action } = body;
  if (!action || !["resolve", "reject"].includes(action)) {
    return NextResponse.json(
      { error: "action must be one of: resolve, reject." },
      { status: 400 }
    );
  }

  const db = getSupabaseServer();
  const actor = getActor();

  // Fetch before-state for audit log (needed for both actions).
  const { data: unmatched, error: fetchErr } = await db
    .from("unmatched_issuers")
    .select("id, raw_name, isin, status")
    .eq("id", unmatchedId)
    .single();

  if (fetchErr || !unmatched) {
    return NextResponse.json({ error: "Unmatched issuer not found." }, { status: 404 });
  }

  // ── reject ────────────────────────────────────────────────────────────────
  if (action === "reject") {
    const { error } = await db
      .from("unmatched_issuers")
      .update({ status: "rejected" })
      .eq("id", unmatchedId);
    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }
    await logAudit(db, {
      actionType:   "reject_issuer",
      entityType:   "unmatched_issuer",
      entityId:     unmatchedId,
      actor,
      beforeValues: { status: unmatched.status, raw_name: unmatched.raw_name },
      afterValues:  { status: "rejected" },
    });
    return NextResponse.json({ ok: true });
  }

  // ── resolve ───────────────────────────────────────────────────────────────
  const { issuer_id } = body;
  if (!issuer_id || isNaN(issuer_id)) {
    return NextResponse.json({ error: "issuer_id is required." }, { status: 400 });
  }

  const { error: resolveErr } = await db
    .from("unmatched_issuers")
    .update({ status: "resolved", resolved_issuer_id: issuer_id })
    .eq("id", unmatchedId);

  if (resolveErr) {
    return NextResponse.json({ error: resolveErr.message }, { status: 500 });
  }

  // Backfill companies.issuer_id for all companies with this raw name.
  const { error: backfillErr } = await db
    .from("companies")
    .update({ issuer_id })
    .ilike("name", unmatched.raw_name)
    .is("issuer_id", null);

  if (backfillErr) {
    console.warn("backfill companies.issuer_id failed:", backfillErr.message);
  }

  await logAudit(db, {
    actionType:   "resolve_issuer",
    entityType:   "unmatched_issuer",
    entityId:     unmatchedId,
    actor,
    beforeValues: { status: unmatched.status, raw_name: unmatched.raw_name, isin: unmatched.isin },
    afterValues:  { status: "resolved", issuer_id },
  });

  return NextResponse.json({ ok: true });
}
