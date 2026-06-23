/**
 * Shared helpers for /api/v1/* route responses.
 */

import { NextResponse } from "next/server";
import type { RateLimitResult } from "./api-rate-limit";

export const API_VERSION = "v1";

/** Standard error response. */
export function apiError(
  message: string,
  status: number,
  extra?: Record<string, unknown>
): NextResponse {
  return NextResponse.json(
    { error: message, ...extra },
    { status }
  );
}

/** Standard paginated JSON response. */
export function apiJson<T>(
  data: T[],
  pagination: { page: number; page_size: number; total: number; total_pages: number },
  rateLimit?: RateLimitResult
): NextResponse {
  const headers: Record<string, string> = {
    "X-API-Version": API_VERSION,
  };
  if (rateLimit) {
    headers["X-RateLimit-Limit"] = String(rateLimit.limit);
    headers["X-RateLimit-Remaining"] = String(rateLimit.remaining);
    headers["X-RateLimit-Reset"] = rateLimit.resetAt;
  }

  return NextResponse.json(
    {
      data,
      pagination,
      meta: { version: API_VERSION, generated_at: new Date().toISOString() },
    },
    { status: 200, headers }
  );
}

/** Convert an array of objects to CSV. */
export function toCsv(rows: Record<string, unknown>[]): string {
  if (rows.length === 0) return "";
  const headers = Object.keys(rows[0]);
  const escape = (v: unknown): string => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return s.includes(",") || s.includes('"') || s.includes("\n")
      ? `"${s.replace(/"/g, '""')}"`
      : s;
  };
  const lines = [
    headers.join(","),
    ...rows.map((r) => headers.map((h) => escape(r[h])).join(",")),
  ];
  return lines.join("\r\n");
}

/** CSV download response. */
export function apiCsv(rows: Record<string, unknown>[], filename: string): NextResponse {
  const csv = toCsv(rows);
  return new NextResponse(csv, {
    status: 200,
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${filename}"`,
      "X-API-Version": API_VERSION,
    },
  });
}

/** Parse and clamp pagination query params. */
export function parsePagination(searchParams: URLSearchParams): {
  page: number;
  pageSize: number;
  offset: number;
} {
  const page = Math.max(1, parseInt(searchParams.get("page") ?? "1", 10) || 1);
  const pageSize = Math.min(
    100,
    Math.max(1, parseInt(searchParams.get("page_size") ?? "25", 10) || 25)
  );
  return { page, pageSize, offset: (page - 1) * pageSize };
}
