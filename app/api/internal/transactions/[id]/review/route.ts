import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";

const VALID_TYPES = new Set([
  "buy", "sell", "grant", "option_exercise", "sell_to_cover",
  "subscription", "conversion", "inheritance",
  "gift_in", "gift_out", "transfer_in", "transfer_out",
  "pledge_or_security", "derivative_transaction",
  "other", "unknown",
]);

const INTENT_MAP: Record<string, string> = {
  buy: "discretionary", sell: "discretionary", subscription: "discretionary",
  grant: "mechanical", option_exercise: "mechanical", sell_to_cover: "mechanical",
  conversion: "mechanical", inheritance: "mechanical",
  gift_in: "mechanical", gift_out: "mechanical",
  transfer_in: "mechanical", transfer_out: "mechanical",
  pledge_or_security: "mechanical", derivative_transaction: "mechanical",
  other: "unclear", unknown: "unclear",
};

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
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
  const now = new Date().toISOString();

  // ── confirm ──────────────────────────────────────────────────────────────────
  if (action === "confirm") {
    const { error } = await db
      .from("transactions")
      .update({ review_status: "confirmed", updated_at: now })
      .eq("id", transactionId);
    if (error) {
      console.error("confirm error:", error.message);
      return NextResponse.json({ error: error.message }, { status: 500 });
    }
    return NextResponse.json({ ok: true });
  }

  // ── reject ───────────────────────────────────────────────────────────────────
  if (action === "reject") {
    const { error } = await db
      .from("transactions")
      .update({ review_status: "rejected", updated_at: now })
      .eq("id", transactionId);
    if (error) {
      console.error("reject error:", error.message);
      return NextResponse.json({ error: error.message }, { status: 500 });
    }
    return NextResponse.json({ ok: true });
  }

  // ── classify (override) ───────────────────────────────────────────────────────
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

  // Snapshot current row before overriding.
  const { data: current, error: fetchErr } = await db
    .from("transactions")
    .select("*")
    .eq("id", transactionId)
    .single();

  if (fetchErr || !current) {
    return NextResponse.json({ error: "Transaction not found." }, { status: 404 });
  }

  const versionNumber = (current.version_number ?? 1) as number;

  const [snapshotResult, updateResult] = await Promise.all([
    db.from("transaction_versions").insert({
      transaction_id: transactionId,
      version_number: versionNumber,
      snapshot: current,
      changed_by: "operator",
      change_reason: `classification_override: ${rationale}`,
    }),
    db.from("transactions").update({
      transaction_type,
      economic_intent: INTENT_MAP[transaction_type] ?? "unclear",
      classification_rationale: `operator_correction: ${rationale}`,
      classification_override: true,
      classification_overridden_by: "operator",
      classification_overridden_at: now,
      version_number: versionNumber + 1,
      review_status: "corrected",
      updated_at: now,
    }).eq("id", transactionId),
  ]);

  if (snapshotResult.error) {
    console.warn("snapshot insert failed:", snapshotResult.error.message);
  }
  if (updateResult.error) {
    console.error("classify update error:", updateResult.error.message);
    return NextResponse.json({ error: updateResult.error.message }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
