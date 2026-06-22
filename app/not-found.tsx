import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <p className="text-6xl font-bold tabular-nums text-white/10">404</p>
      <p className="mt-4 text-sm text-muted">Page not found.</p>
      <Link
        href="/"
        className="mt-6 rounded-full border border-white/10 px-5 py-2 text-sm text-muted hover:text-[#E8EDF7] hover:border-white/20 transition-colors"
      >
        ← Back to transactions
      </Link>
    </div>
  );
}
