"use client";

import { useState } from "react";

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
      <circle cx="11" cy="11" r="8" /><path d="M21 21l-4.35-4.35" />
    </svg>
  );
}

function BellIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
      <path d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  );
}

type Tab = "feed" | "queue";

export function TopBar() {
  const [activeTab, setActiveTab] = useState<Tab>("queue");

  return (
    <header className="flex h-9 shrink-0 items-center justify-between gap-4 border-b border-white/[0.07] bg-navy-900 px-4">
      {/* Left: tabs */}
      <div className="flex items-center gap-0">
        <button
          onClick={() => setActiveTab("feed")}
          className={`flex h-9 items-center border-b-2 px-3 text-[11px] font-medium transition-colors ${
            activeTab === "feed"
              ? "border-brand-blue text-brand-blue"
              : "border-transparent text-muted/60 hover:text-muted"
          }`}
        >
          Real-Time Feed
        </button>
        <button
          onClick={() => setActiveTab("queue")}
          className={`flex h-9 items-center border-b-2 px-3 text-[11px] font-medium transition-colors ${
            activeTab === "queue"
              ? "border-brand-blue text-brand-blue"
              : "border-transparent text-muted/60 hover:text-muted"
          }`}
        >
          Priority Queue
        </button>
      </div>

      {/* Center: search */}
      <div className="flex max-w-xs flex-1 items-center gap-2 rounded-md border border-white/[0.07] bg-white/[0.03] px-2.5 py-1.5">
        <SearchIcon />
        <span className="text-[11px] text-muted/40">Global Search...</span>
        <span className="ml-auto text-[10px] font-mono text-muted/30">⌘K</span>
      </div>

      {/* Right: actions */}
      <div className="flex items-center gap-2">
        <button className="relative flex h-7 w-7 items-center justify-center rounded-md text-muted/60 transition-colors hover:bg-white/[0.05] hover:text-muted">
          <BellIcon />
          <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-signal" />
        </button>
        <button className="flex h-7 w-7 items-center justify-center rounded-md text-muted/60 transition-colors hover:bg-white/[0.05] hover:text-muted">
          <GearIcon />
        </button>
        <div className="flex h-6 w-6 items-center justify-center rounded-full bg-navy-700 text-[10px] font-semibold text-brand-blue ring-1 ring-brand-blue/30">
          A
        </div>
      </div>
    </header>
  );
}
