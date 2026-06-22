import Link from "next/link";
import { buildHref, QueryValue } from "@/lib/url";

interface Props {
  page: number;
  totalPages: number;
  current: Record<string, QueryValue>;
  basePath: string;
}

export function Pagination({ page, totalPages, current, basePath }: Props) {
  if (totalPages <= 1) return null;

  const prev = Math.max(1, page - 1);
  const next = Math.min(totalPages, page + 1);
  const atStart = page <= 1;
  const atEnd   = page >= totalPages;

  const base = "rounded-full border border-white/10 px-4 py-1.5 text-xs font-medium transition-colors";
  const off  = "pointer-events-none opacity-30 text-muted";
  const on   = "text-muted hover:border-white/20 hover:text-[#E8EDF7]";

  return (
    <div className="flex items-center justify-between py-1">
      <Link
        aria-disabled={atStart}
        className={`${base} ${atStart ? off : on}`}
        href={buildHref(basePath, { ...current, page: prev })}
      >
        ← Prev
      </Link>
      <span className="text-xs text-muted">
        {page} / {totalPages}
      </span>
      <Link
        aria-disabled={atEnd}
        className={`${base} ${atEnd ? off : on}`}
        href={buildHref(basePath, { ...current, page: next })}
      >
        Next →
      </Link>
    </div>
  );
}
