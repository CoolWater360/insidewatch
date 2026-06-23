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
  const transactionId = parseInt(id, 10);
  if (!transactionId || isNaN(transactionId)) {
    return NextResponse.json({ error: "Invalid transaction ID." }, { status: 400 });
  }

  let body: { note?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const note = body.note?.trim() ?? "";
  if (!note) {
    return NextResponse.json({ error: "note must not be empty." }, { status: 400 });
  }

  const db = getSupabaseServer();
  const actor = getActor();
  const now = new Date().toISOString();

  // Fetch current note for before_values.
  const { data: current } = await db
    .from("transactions")
    .select("review_notes")
    .eq("id", transactionId)
    .single();

  const { error } = await db
    .from("transactions")
    .update({ review_notes: note, updated_at: now })
    .eq("id", transactionId);

  if (error) {
    console.error("note update error:", error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  await logAudit(db, {
    actionType:   "add_review_note",
    entityType:   "transaction",
    entityId:     transactionId,
    actor,
    beforeValues: { review_notes: current?.review_notes ?? null },
    afterValues:  { review_notes: note },
  });

  return NextResponse.json({ ok: true });
}
