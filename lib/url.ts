export type QueryValue = string | number | undefined | null;

/**
 * Build an href from a base path plus query params, dropping empty values.
 * Used to preserve filters/sort across sortable headers and pagination links.
 */
export function buildHref(base: string, params: Record<string, QueryValue>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") {
      sp.set(k, String(v));
    }
  }
  const qs = sp.toString();
  return qs ? `${base}?${qs}` : base;
}

/** Normalize a possibly-array searchParam into a single string. */
export function firstParam(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}
