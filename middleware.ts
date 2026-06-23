import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Protects /internal/* and /api/internal/* with HTTP Basic Auth.
// Set INTERNAL_SECRET in environment variables.  Username is ignored;
// only the password is checked.
export function middleware(request: NextRequest): NextResponse {
  const secret = process.env.INTERNAL_SECRET;

  if (!secret) {
    return new NextResponse("Internal console disabled: INTERNAL_SECRET not set.", {
      status: 503,
      headers: { "Content-Type": "text/plain" },
    });
  }

  const authHeader = request.headers.get("authorization") ?? "";

  if (!authHeader.startsWith("Basic ")) {
    return new NextResponse("Authentication required.", {
      status: 401,
      headers: {
        "WWW-Authenticate": 'Basic realm="InsideWatch Internal"',
        "Content-Type": "text/plain",
      },
    });
  }

  // Edge Runtime: atob is available globally.
  const decoded = atob(authHeader.slice(6));
  const colonIdx = decoded.indexOf(":");
  const password = colonIdx === -1 ? decoded : decoded.slice(colonIdx + 1);

  if (password !== secret) {
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
