import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { checkOrigin, getActor, logAudit } from "@/lib/internal-audit";

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
  // ── Route-level CSRF guard ────────────────────────────────────────────────
  // Middleware enforces Basic Auth, but a browser with cached credentials
  // could be exploited by a CSRF attack from a different origin.
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
  const now = new Date().toISOString();

  // ── Fetch current state (needed for before_values in all actions) ──────────
  const { data: current, error: fetchErr } = await db
    .from("transactions")
    .select("id, review_status, transaction_type, economic_intent, classification_rationale, direction, needs_review, version_number")
    .eq("id", transactionId)
    .single();

  if (fetchErr || !current) {
    return NextResponse.json({ error: "Transaction not found." }, { status: 404 });
  }

  // ── confirm ───────────────────────────────────────────────────────────────
  if (action === "confirm") {
    const { error } = await db
      .from("transactions")
      .update({ review_status: "confirmed", updated_at: now })
      .eq("id", transactionId);
    if (error) {
      console.error("confirm error:", error.message);
      return NextResponse.json({ error: error.message }, { status: 500 });
    }
    await logAudit(db, {
      actionType:   "confirm",
      entityType:   "transaction",
      entityId:     transactionId,
      actor,
      beforeValues: { review_status: current.review_status },
      afterValues:  { review_status: "confirmed" },
    });
    return NextResponse.json({ ok: true });
  }

  // ── reject ────────────────────────────────────────────────────────────────
  if (action === "reject") {
    const { error } = await db
      .from("transactions")
      .update({ review_status: "rejected", updated_at: now })
      .eq("id", transactionId);
    if (error) {
      console.error("reject error:", error.message);
      return NextResponse.json({ error: error.message }, { status: 500 });
    }
    await logAudit(db, {
      actionType:   "reject",
      entityType:   "transaction",
      entityId:     transactionId,
      actor,
      beforeValues: { review_status: current.review_status },
      afterValues:  { review_status: "rejected" },
    });
    return NextResponse.json({ ok: true });
  }

  // ── classify (override) ───────────────────────────────────────────────────
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

  // Snapshot full row to transaction_versions before overriding.
  const { data: fullRow } = await db
    .from("transactions")
    .select("*")
    .eq("id", transactionId)
    .single();

  const versionNumber = (current.version_number ?? 1) as number;

  const [snapshotResult, updateResult] = await Promise.all([
    db.from("transaction_versions").insert({
      transaction_id: transactionId,
      version_number: versionNumber,
      snapshot:       fullRow ?? current,
      changed_by:     actor,
      change_reason:  `classification_override: ${rationale}`,
    }),
    db.from("transactions").update({
      transaction_type,
      economic_intent:              INTENT_MAP[transaction_type] ?? "unclear",
      classification_rationale:     `operator_correction: ${rationale}`,
      classification_override:      true,
      classification_overridden_by: actor,
      classification_overridden_at: now,
      version_number:               versionNumber + 1,
      review_status:                "corrected",
      updated_at:                   now,
    }).eq("id", transactionId),
  ]);

  if (snapshotResult.error) {
    console.warn("snapshot insert failed:", snapshotResult.error.message);
  }
  if (updateResult.error) {
    console.error("classify update error:", updateResult.error.message);
    return NextResponse.json({ error: updateResult.error.message }, { status: 500 });
  }

  await logAudit(db, {
    actionType:   "reclassify",
    entityType:   "transaction",
    entityId:     transactionId,
    actor,
    beforeValues: {
      transaction_type:         current.transaction_type,
      economic_intent:          current.economic_intent,
      classification_rationale: current.classification_rationale,
    },
    afterValues: {
      transaction_type:         transaction_type,
      economic_intent:          INTENT_MAP[transaction_type] ?? "unclear",
      classification_rationale: `operator_correction: ${rationale}`,
    },
  });

  return NextResponse.json({ ok: true });
}
