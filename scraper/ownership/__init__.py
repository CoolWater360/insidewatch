"""
Phase 17B.2 — controlled ownership-event pilot.

A narrowly scoped collector that ingests a SMALL set of EXPLICITLY supplied
official CONSOB ownership notifications into the Phase 17A context schema.

Hard boundaries (enforced in code, see collector.py):
  * explicit URL input only — no bulk discovery, no pagination, no archive
    enumeration, no search-page automation;
  * 3-second minimum delay between official-source requests;
  * descriptive User-Agent;
  * dry-run by default — writes require --apply;
  * exact issuer/entity resolution only; ambiguous matches stay unresolved
    and pending_review;
  * no beneficial-owner / control / holding-chain inference;
  * no public UI activation.

Two source formats are supported by SEPARATE adapters that are never assumed
interchangeable:
  archive_html.py — pre-2026-06-15 dated archive HTML notices.
  tr1_pdf.py      — post-2026-06-15 ESMA TR-1 / MJSHLD PDFs.
"""
