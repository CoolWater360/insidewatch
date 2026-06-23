import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const filingId = parseInt(id, 10);
  if (!filingId || isNaN(filingId)) {
    return NextResponse.json({ error: "Invalid filing ID." }, { status: 400 });
  }

  const db = getSupabaseServer();

  // Confirm the filing exists and is in a retriable state.
  const { data: filing, error: fetchErr } = await db
    .from("filings")
    .select("id, status, attempt_count, max_attempts")
    .eq("id", filingId)
    .single();

  if (fetchErr || !filing) {
    return NextResponse.json({ error: "Filing not found." }, { status: 404 });
  }

  // Reset to pending and clear the claim so the scraper picks it up.
  const { error } = await db
    .from("filings")
    .update({
      status: "pending",
      claim_token: null,
      next_attempt_after: null,
      last_error: null,
    })
    .eq("id", filingId);

  if (error) {
    console.error("retry filing error:", error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
