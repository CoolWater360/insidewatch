/**
 * Authentication gate for /internal/* and /api/internal/*.
 *
 * Model: shared-admin (temporary)
 *   A single INTERNAL_SECRET is shared across all operators with console access.
 *   The acting identity is recorded separately via INTERNAL_ACTOR_LABEL in every
 *   audit row.  This is NOT individual user authentication.  Before expanding
 *   access beyond a single operator, replace with per-user identity (e.g. Supabase
 *   Auth or an external IdP) and issue per-user audit identifiers.
 *
 * CSRF protection lives in each route handler (lib/internal-audit.ts checkOrigin),
 * not here, so that API routes remain independently safe even if this middleware
 * is misconfigured or bypassed.
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { checkBasicAuth } from "./lib/internal-audit";

export function middleware(request: NextRequest): NextResponse {
  const secret = process.env.INTERNAL_SECRET ?? null;
  const authHeader = request.headers.get("authorization");

  const result = checkBasicAuth(authHeader, secret);

  if (result === "no-secret") {
    return new NextResponse("Internal console disabled: INTERNAL_SECRET not set.", {
      status: 503,
      headers: { "Content-Type": "text/plain" },
    });
  }

  if (result === "no-auth") {
    return new NextResponse("Authentication required.", {
      status: 401,
      headers: {
        "WWW-Authenticate": 'Basic realm="InsideWatch Internal"',
        "Content-Type": "text/plain",
      },
    });
  }

  if (result === "wrong-password") {
    return new NextResponse("Unauthorized.", {
      status: 401,
      headers: {
        "WWW-Authenticate": 'Basic realm="InsideWatch Internal"',
        "Content-Type": "text/plain",
      },
    });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/internal/:path*", "/api/internal/:path*"],
};
