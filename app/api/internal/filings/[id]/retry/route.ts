import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { checkOrigin, getActor } from "@/lib/internal-audit";
import { retryFiling } from "@/lib/internal-actions";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
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

  const { error: findErr } = await db
    .from("filings")
    .select("id")
    .eq("id", filingId)
    .single();
  if (findErr) {
    return NextResponse.json({ error: "Filing not found." }, { status: 404 });
  }

  const result = await retryFiling(db, filingId, actor);
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: 500 });
  return NextResponse.json({ ok: true });
}
