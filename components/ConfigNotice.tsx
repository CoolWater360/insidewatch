export function ConfigNotice() {
  return (
    <div className="glass-card-signal rounded-xl p-6">
      <h2 className="text-base font-semibold text-signal">Supabase not configured</h2>
      <p className="mt-2 text-sm text-muted">
        Create <code className="rounded bg-white/5 px-1.5 py-0.5 text-xs font-mono text-[#E8EDF7]">.env.local</code>{" "}
        with your Supabase credentials:
      </p>
      <pre className="mt-3 overflow-x-auto rounded-lg bg-black/30 p-4 text-xs font-mono text-muted/80 border border-white/5">
{`SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-public-key`}
      </pre>
      <p className="mt-3 text-sm text-muted">
        Run <code className="rounded bg-white/5 px-1.5 py-0.5 text-xs font-mono text-[#E8EDF7]">db/schema.sql</code>{" "}
        in the Supabase SQL editor, then restart the dev server.
      </p>
    </div>
  );
}
