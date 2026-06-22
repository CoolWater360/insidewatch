"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Company } from "@/lib/types";

interface Props {
  companies: Company[];
  current: {
    company?: number;
    direction?: string;
    from?: string;
    to?: string;
  };
}

export function Filters({ companies, current }: Props) {
  const router = useRouter();
  const [company,   setCompany]   = useState(current.company ? String(current.company) : "");
  const [direction, setDirection] = useState(current.direction ?? "");
  const [from,      setFrom]      = useState(current.from ?? "");
  const [to,        setTo]        = useState(current.to ?? "");

  function apply() {
    const sp = new URLSearchParams();
    if (company)   sp.set("company",   company);
    if (direction) sp.set("direction", direction);
    if (from)      sp.set("from",      from);
    if (to)        sp.set("to",        to);
    const qs = sp.toString();
    router.push(qs ? `/?${qs}` : "/");
  }

  function reset() {
    setCompany(""); setDirection(""); setFrom(""); setTo("");
    router.push("/");
  }

  return (
    <div className="glass-card rounded-xl p-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <FilterLabel label="Company">
          <div className="relative">
            <select
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              className="pill-input w-full pr-7"
            >
              <option value="">All companies</option>
              {companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <Chevron />
          </div>
        </FilterLabel>

        <FilterLabel label="Direction">
          <div className="relative">
            <select
              value={direction}
              onChange={(e) => setDirection(e.target.value)}
              className="pill-input w-full pr-7"
            >
              <option value="">All</option>
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
              <option value="unknown">Other</option>
            </select>
            <Chevron />
          </div>
        </FilterLabel>

        <FilterLabel label="From">
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="pill-input w-full [color-scheme:dark]"
          />
        </FilterLabel>

        <FilterLabel label="To">
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            className="pill-input w-full [color-scheme:dark]"
          />
        </FilterLabel>

        <div className="col-span-2 flex items-center gap-2 pt-1 sm:col-span-1 sm:items-end sm:pt-4">
          <button onClick={apply} className="btn-primary flex-1">
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}

function FilterLabel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">
        {label}
      </span>
      {children}
    </label>
  );
}

function Chevron() {
  return (
    <svg
      className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-muted"
      width="12" height="12" viewBox="0 0 12 12" fill="currentColor"
    >
      <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
    </svg>
  );
}
