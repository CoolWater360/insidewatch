import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { checkOrigin, getActor } from "@/lib/internal-audit";
import { resolveIssuer, rejectIssuer } from "@/lib/internal-actions";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
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

  const { error: findErr } = await db
    .from("unmatched_issuers")
    .select("id")
    .eq("id", unmatchedId)
    .single();
  if (findErr) {
    return NextResponse.json({ error: "Unmatched issuer not found." }, { status: 404 });
  }

  if (action === "reject") {
    const result = await rejectIssuer(db, unmatchedId, actor);
    if (!result.ok) return NextResponse.json({ error: result.error }, { status: 500 });
    return NextResponse.json({ ok: true });
  }

  // resolve
  const { issuer_id } = body;
  if (!issuer_id || isNaN(issuer_id)) {
    return NextResponse.json({ error: "issuer_id is required." }, { status: 400 });
  }

  const result = await resolveIssuer(db, unmatchedId, issuer_id, actor);
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: 500 });
  return NextResponse.json({ ok: true });
}
