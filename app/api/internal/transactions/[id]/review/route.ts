import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { checkOrigin, getActor } from "@/lib/internal-audit";
import {
  confirmTransaction,
  rejectTransaction,
  reclassifyTransaction,
} from "@/lib/internal-actions";

const VALID_TYPES = new Set([
  "buy", "sell", "grant", "option_exercise", "sell_to_cover",
  "subscription", "conversion", "inheritance",
  "gift_in", "gift_out", "transfer_in", "transfer_out",
  "pledge_or_security", "derivative_transaction",
  "other", "unknown",
]);

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

  let body: { action?: string; transaction_type?: string; rationale?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const { action } = body;
  if (!action || !["confirm", "reject", "classify"].includes(action)) {
    return NextResponse.json(
      { error: "action must be one of: confirm, reject, classify." },
      { status: 400 }
    );
  }

  const db = getSupabaseServer();
  const actor = getActor();

  // Cheap existence check — returns 404 before reaching the RPC.
  const { error: findErr } = await db
    .from("transactions")
    .select("id")
    .eq("id", transactionId)
    .single();
  if (findErr) {
    return NextResponse.json({ error: "Transaction not found." }, { status: 404 });
  }

  if (action === "confirm") {
    const result = await confirmTransaction(db, transactionId, actor);
    if (!result.ok) return NextResponse.json({ error: result.error }, { status: 500 });
    return NextResponse.json({ ok: true });
  }

  if (action === "reject") {
    const result = await rejectTransaction(db, transactionId, actor);
    if (!result.ok) return NextResponse.json({ error: result.error }, { status: 500 });
    return NextResponse.json({ ok: true });
  }

  // classify
  const { transaction_type, rationale } = body;
  if (!transaction_type || !VALID_TYPES.has(transaction_type)) {
    return NextResponse.json(
      { error: `transaction_type must be one of: ${[...VALID_TYPES].join(", ")}.` },
      { status: 400 }
    );
  }
  if (!rationale?.trim()) {
    return NextResponse.json({ error: "rationale is required." }, { status: 400 });
  }

  const result = await reclassifyTransaction(
    db, transactionId, transaction_type, rationale.trim(), actor
  );
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: 500 });
  return NextResponse.json({ ok: true });
}
