import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { checkOrigin, getActor } from "@/lib/internal-audit";
import { addReviewNote } from "@/lib/internal-actions";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
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

  const { error: findErr } = await db
    .from("transactions")
    .select("id")
    .eq("id", transactionId)
    .single();
  if (findErr) {
    return NextResponse.json({ error: "Transaction not found." }, { status: 404 });
  }

  const result = await addReviewNote(db, transactionId, note, actor);
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: 500 });
  return NextResponse.json({ ok: true });
}
