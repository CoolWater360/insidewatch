const LOCALE = "it-IT";

/** Format a whole-euro value, e.g. "€ 296.277". Returns "—" for null. */
export function formatCurrency(value: number | null | undefined, currency = "EUR"): string {
  if (value == null) return "—";
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency: currency || "EUR",
    maximumFractionDigits: 0,
  }).format(value);
}

/** Format a per-unit price with up to 4 decimals, e.g. "€ 23,8875". */
export function formatPrice(value: number | null | undefined, currency = "EUR"): string {
  if (value == null) return "—";
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency: currency || "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(value);
}

/** Format an integer quantity with thousands separators, e.g. "1.100.000". */
export function formatNumber(value: number | null | undefined): string {
  if (value == null) return "—";
  return new Intl.NumberFormat(LOCALE).format(value);
}

/** Format an ISO date (YYYY-MM-DD) as "17 giu 2026". Returns "—" for null. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(`${value}T00:00:00`);
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat(LOCALE, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(d);
}
