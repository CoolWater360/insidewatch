/**
 * GET /api/v1/health
 *
 * Returns the operational health of the InsideWatch pipeline.
 * Requires Bearer token auth via INSIDEWATCH_API_KEY.
 *
 * Response shape:
 *   { status: "ok"|"degraded"|"error", db_ok, scraper_last_run, generated_at, version }
 *
 * HTTP status codes:
 *   200  healthy or degraded (caller should inspect .status)
 *   401  missing or invalid API key
 *   503  API key not configured on the server
 */

import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServer } from "@/lib/supabase-server";
import { checkApiKey } from "@/lib/api-auth";

export async function GET(request: NextRequest) {
  const apiKey = process.env.INSIDEWATCH_API_KEY ?? null;
  const authResult = checkApiKey(request.headers.get("authorization"), apiKey);

  if (authResult === "no-key") {
    return NextResponse.json(
      { error: "API not configured." },
      { status: 503 }
    );
  }
  if (authResult !== "ok") {
    return NextResponse.json(
      { error: "Unauthorized." },
      { status: 401, headers: { "WWW-Authenticate": 'Bearer realm="InsideWatch API"' } }
    );
  }

  const generatedAt = new Date().toISOString();

  // Check DB connectivity and fetch scraper run metadata.
  let dbOk = false;
  let scraperLastRun: string | null = null;
  let lastFilingAt: string | null = null;

  try {
    const db = getSupabaseServer();

    // DB connectivity: a lightweight query that touches the actual tables.
    const [runsResult, filingResult] = await Promise.all([
      db
        .from("scraper_runs")
        .select("last_successful_run, tier")
        .order("last_successful_run", { ascending: false })
        .limit(1),
      db
        .from("filings")
        .select("delivered_utc, completed_at")
        .eq("status", "completed")
        .order("completed_at", { ascending: false })
        .limit(1),
    ]);

    dbOk = !runsResult.error && !filingResult.error;
    scraperLastRun = runsResult.data?.[0]?.last_successful_run ?? null;
    lastFilingAt =
      filingResult.data?.[0]?.delivered_utc ??
      filingResult.data?.[0]?.completed_at ??
      null;
  } catch {
    dbOk = false;
  }

  // Determine overall status.
  let status: "ok" | "degraded" | "error";
  if (!dbOk) {
    status = "error";
  } else if (!scraperLastRun) {
    status = "degraded";
  } else {
    const lastRun = new Date(scraperLastRun).getTime();
    const hoursAgo = (Date.now() - lastRun) / 3_600_000;
    status = hoursAgo < 26 ? "ok" : "degraded";
  }

  return NextResponse.json(
    {
      status,
      version: "v1",
      db_ok: dbOk,
      scraper_last_run: scraperLastRun,
      last_filing_at: lastFilingAt,
      generated_at: generatedAt,
    },
    { status: 200 }
  );
}
