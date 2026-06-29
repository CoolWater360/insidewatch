import { getOwnershipReviewData } from "@/lib/ownership-review";
import { OwnershipReviewControls } from "@/components/internal/OwnershipReviewControls";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Ownership Review Queue — InsideWatch Internal",
};

function pct(v: number | null): string {
  return v == null ? "—" : `${v}%`;
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-muted/50">
      {children}
    </th>
  );
}

function Td({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-3 py-2 align-top text-[12px] text-[#E8EDF7] ${className}`}>{children}</td>;
}

export default async function OwnershipReviewPage() {
  const { entities, events, relationships } = await getOwnershipReviewData();

  return (
    <div className="mx-auto max-w-6xl">
      {/* Header */}
      <div className="mb-4">
        <h1 className="text-[18px] font-medium text-[#E8EDF7]">Ownership Review Queue</h1>
        <p className="mt-1 text-[12px] text-muted/60">
          Internal review of pending ownership-pilot records. Approve or reject
          each record. Raw source facts are not editable here.
        </p>
      </div>

      {/* Pilot / partial-coverage notice */}
      <div className="mb-6 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-300">
        <strong>Pilot — partial coverage.</strong> This queue contains only the
        three manually-approved CONSOB pilot records. It is not a complete
        ownership dataset and shows no ownership signals or investment
        interpretation. Source data is preserved; only review status (and
        operator-approved entity types) can change.
      </div>

      {/* ── 1. Entities ─────────────────────────────────────────────────── */}
      <section className="mb-8">
        <h2 className="mb-2 text-[13px] font-semibold text-[#E8EDF7]">
          Entities pending review{" "}
          <span className="text-muted/50">({entities.length})</span>
        </h2>
        <div className="overflow-hidden rounded-lg border border-white/[0.06] bg-navy-800">
          <table className="w-full border-collapse">
            <thead className="bg-navy-700/40">
              <tr>
                <Th>Legal name</Th>
                <Th>Current type</Th>
                <Th>Proposed type</Th>
                <Th>Reason / evidence</Th>
                <Th>Status</Th>
                <Th>Action</Th>
              </tr>
            </thead>
            <tbody>
              {entities.length === 0 && (
                <tr><Td className="text-muted/50">No entities pending review.</Td></tr>
              )}
              {entities.map((e) => (
                <tr key={e.id} className="border-t border-white/[0.05]">
                  <Td className="font-medium">{e.legal_name}</Td>
                  <Td><code className="text-[11px] text-muted/80">{e.entity_type}</code></Td>
                  <Td>
                    {e.recommendation ? (
                      <code className="text-[11px] text-brand-blue">{e.recommendation.proposedType}</code>
                    ) : (
                      <span className="text-muted/40">—</span>
                    )}
                  </Td>
                  <Td className="max-w-xs text-[11px] text-muted/70">
                    {e.recommendation ? e.recommendation.reason : "no change recommended"}
                  </Td>
                  <Td><code className="text-[11px] text-muted/70">{e.review_status}</code></Td>
                  <Td>
                    <OwnershipReviewControls
                      kind="entity"
                      id={e.id}
                      proposedType={e.recommendation?.proposedType ?? null}
                    />
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── 2. Ownership events ─────────────────────────────────────────── */}
      <section className="mb-8">
        <h2 className="mb-2 text-[13px] font-semibold text-[#E8EDF7]">
          Ownership events pending review{" "}
          <span className="text-muted/50">({events.length})</span>
        </h2>
        <div className="overflow-hidden rounded-lg border border-white/[0.06] bg-navy-800">
          <table className="w-full border-collapse">
            <thead className="bg-navy-700/40">
              <tr>
                <Th>Issuer</Th>
                <Th>Declarant</Th>
                <Th>Voting %</Th>
                <Th>Event type</Th>
                <Th>Event / Pub date</Th>
                <Th>Direct/Indirect</Th>
                <Th>Source</Th>
                <Th>Status</Th>
                <Th>Action</Th>
              </tr>
            </thead>
            <tbody>
              {events.length === 0 && (
                <tr><Td className="text-muted/50">No ownership events pending review.</Td></tr>
              )}
              {events.map((ev) => (
                <tr key={ev.id} className="border-t border-white/[0.05]">
                  <Td className="font-medium">{ev.issuer_name}</Td>
                  <Td>
                    {ev.raw_entity_name}
                    {ev.raw_vehicle_name && (
                      <div className="text-[10px] text-muted/50">via {ev.raw_vehicle_name}</div>
                    )}
                  </Td>
                  <Td>{pct(ev.voting_pct_after)}</Td>
                  <Td>
                    <code className="text-[11px] text-muted/80">{ev.event_type}</code>
                    {ev.directionUndetermined && (
                      <div className="mt-0.5 inline-block rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">
                        {ev.ambiguityNote}
                      </div>
                    )}
                  </Td>
                  <Td className="text-[11px] text-muted/70">
                    {ev.event_date ?? "—"}
                    <div className="text-muted/40">pub {ev.publication_date ?? "—"}</div>
                  </Td>
                  <Td><code className="text-[11px] text-muted/70">{ev.direct_or_indirect ?? "—"}</code></Td>
                  <Td>
                    {ev.source_url ? (
                      <a
                        href={ev.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[11px] text-brand-blue underline decoration-dotted hover:text-brand-blue/80"
                      >
                        CONSOB source
                      </a>
                    ) : (
                      <span className="text-muted/40">—</span>
                    )}
                  </Td>
                  <Td><code className="text-[11px] text-muted/70">{ev.review_status}</code></Td>
                  <Td><OwnershipReviewControls kind="event" id={ev.id} /></Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── 3. Relationships ────────────────────────────────────────────── */}
      <section className="mb-8">
        <h2 className="mb-2 text-[13px] font-semibold text-[#E8EDF7]">
          Relationships pending review{" "}
          <span className="text-muted/50">({relationships.length})</span>
        </h2>
        <div className="overflow-hidden rounded-lg border border-white/[0.06] bg-navy-800">
          <table className="w-full border-collapse">
            <thead className="bg-navy-700/40">
              <tr>
                <Th>Subject entity</Th>
                <Th>Relationship</Th>
                <Th>Object entity</Th>
                <Th>Source</Th>
                <Th>Status</Th>
                <Th>Action</Th>
              </tr>
            </thead>
            <tbody>
              {relationships.length === 0 && (
                <tr><Td className="text-muted/50">No relationships pending review.</Td></tr>
              )}
              {relationships.map((r) => (
                <tr key={r.id} className="border-t border-white/[0.05]">
                  <Td className="font-medium">{r.subject_name}</Td>
                  <Td>
                    <code className="text-[11px] text-muted/80">{r.relationship_type}</code>
                    {r.direct_or_indirect && (
                      <span className="ml-1 text-[10px] text-muted/50">({r.direct_or_indirect})</span>
                    )}
                  </Td>
                  <Td>{r.object_name}</Td>
                  <Td>
                    {r.source_url ? (
                      <a
                        href={r.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[11px] text-brand-blue underline decoration-dotted hover:text-brand-blue/80"
                      >
                        CONSOB source
                      </a>
                    ) : (
                      <span className="text-muted/40">—</span>
                    )}
                  </Td>
                  <Td><code className="text-[11px] text-muted/70">{r.review_status}</code></Td>
                  <Td><OwnershipReviewControls kind="relationship" id={r.id} /></Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <p className="text-[11px] text-muted/40">
        Explicitly stated relationships only. No beneficial-owner, control-chain,
        or concert-party inference. Internal use only.
      </p>
    </div>
  );
}
