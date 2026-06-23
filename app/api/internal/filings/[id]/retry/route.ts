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
  const filingId = parseInt(id, 10);
  if (!filingId || isNaN(filingId)) {
    return NextResponse.json({ error: "Invalid filing ID." }, { status: 400 });
  }

  const db = getSupabaseServer();
  const actor = getActor();

  const { data: filing, error: fetchErr } = await db
    .from("filings")
    .select("id, status, attempt_count, max_attempts")
    .eq("id", filingId)
    .single();

  if (fetchErr || !filing) {
    return NextResponse.json({ error: "Filing not found." }, { status: 404 });
  }

  const { error } = await db
    .from("filings")
    .update({
      status:             "pending",
      claim_token:        null,
      next_attempt_after: null,
      last_error:         null,
    })
    .eq("id", filingId);

  if (error) {
    console.error("retry filing error:", error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  await logAudit(db, {
    actionType:   "retry_filing",
    entityType:   "filing",
    entityId:     filingId,
    actor,
    beforeValues: { status: filing.status, attempt_count: filing.attempt_count },
    afterValues:  { status: "pending" },
  });

  return NextResponse.json({ ok: true });
}
