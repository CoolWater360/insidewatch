# Phase 17B — CONSOB Ownership & Control Source Assessment

Phase 17B Preparation · Schema readiness + source validation  
Status: **DRAFT — research pass, pre-implementation**  
Date: 2026-06-29

---

## 1. Schema readiness verdict

Migrations 016–019 define a complete, well-constrained schema for the Phase 17
context data layer. The tables are present in Supabase (confirmed empty in the
previous session). The following properties are confirmed ready for pilot
ingestion.

### 1.1 Tables confirmed schema-ready

| Table | Created by | Pilot target? |
|-------|-----------|--------------|
| `context_sources` | 016 | Yes — every ownership event needs a source row |
| `entities` | 016 | Yes — declarant + holding vehicle |
| `entity_relationships` | 017 | Deferred to Phase 17C |
| `ownership_events` | 018 | Yes — primary pilot target |
| `governance_events` | 018 | No — separate phase |
| `buyback_events` | 018 | No — separate phase |
| `corporate_events` | 018 | No — separate phase |
| `context_event_links` | 019 | Deferred — link to transactions after pilot data exists |

### 1.2 Write-path safety

- `assert_source_provenance()` in `scraper/context_db.py` enforces `source_id`
  not-null and valid `confidence` before any DB write — this must be called
  before every INSERT on an event table.
- `upsert_context_source()` is idempotent on `source_url` (UNIQUE constraint).
  Re-running the collector for the same filing URL is safe.
- `supersede_context_event()` mirrors the pattern in `supersede_transaction()`
  — corrections create a new row and mark the old `is_current=FALSE`, preserving
  full audit history.
- The pilot collector must use the **service-role key** (`SUPABASE_SERVICE_ROLE_KEY`)
  in env — the same key used by the existing scraper (see `scraper/db.py`).

### 1.3 Design notes with pilot implications

**`ownership_events.event_type` vocabulary (migration 018):**

The migration uses a different vocabulary than the design document (context-data-model.md §6.4).
The migration's CHECK constraint is authoritative:

```
threshold_crossing_up | threshold_crossing_down | initial_disclosure |
cancellation | change_in_nature | pledge | pledge_release | other
```

The design document vocabulary (`stake_increase`, `stake_decrease`, etc.) is
**not** what is in the DB. The pilot collector must use the migration vocabulary.

**`ownership_events.raw_entity_name`:**

The column is `TEXT` without `NOT NULL`. CONSOB notifications always carry the
declarant name, so the pilot collector should always populate `raw_entity_name`
from the filing, regardless of whether `entity_id` is resolved.

**No `threshold_pct` column on `ownership_events`:**

The migration 018 table does not include `threshold_pct` (the design document
added it, but it was not carried through). The percentage values captured are
`stake_pct_before` and `stake_pct_after`. The crossed threshold is implied by
comparing these to the Italian regulatory thresholds (3, 5, 10, 15, 20, 25,
30, 50, 66.6, 90%). The pilot should compute and log the crossed threshold
in `evidence_text` rather than expecting a dedicated column.

---

## 2. Source catalogue

### 2.1 CONSOB Partecipazioni Rilevanti — Primary Source

**Publisher:** Commissione Nazionale per le Società e la Borsa (CONSOB)  
**Legal basis:** TUF (D.Lgs. 58/1998) Art. 120; CONSOB Reg. Emittenti 11971/1999
Art. 117–121 (as amended); EU Transparency Directive 2004/109/EC (TD),
2013/50/EU (amending TD); MAR (Reg. 596/2014) recital-level references.

**What it contains:** Every mandatory major-shareholding notification filed
with CONSOB for companies listed on Italian regulated markets (Euronext Milan
and Euronext Growth Milan). Covers:

- Threshold crossings (both up and down) for 3%, 5%, 10%, 15%, 20%, 25%,
  30%, 50%, 66.6%, 90% (PMI-specific thresholds differ at bottom end).
- Initial disclosures when a holding that was already above threshold is
  first declared.
- Corrections and cancellations of prior notifications.
- Changes in nature of holding (direct ↔ indirect).

**Portal URL:**  
`https://partecipazioni.consob.it/`

This is the searchable public database of notifications. The same data is
also accessible from the main CONSOB site under:
`https://www.consob.it/web/area-pubblica/partecipazioni-rilevanti`

**Access method:**  
Web portal with HTML search form. Each notification is typically a PDF
document (Modulo 103A format or equivalent structured filing) with a stable
URL unique to that filing. The data can be fetched per-issuer by ISIN or
name. No officially documented public REST API exists as of the knowledge
cutoff.

**Document URL pattern (to be verified by inspection):**  
Individual notification pages follow patterns like:
```
https://partecipazioni.consob.it/.../<notification_id>
```
The exact pattern must be confirmed by inspection. Do not scrape beyond the
search result pages without verifying the actual URL structure.

**Format:**  
- HTML search result pages listing notifications
- Individual notifications: PDF (most common), occasionally HTML
- The HTML listing typically contains: declarant name, issuer name, ISIN,
  notification date, filing date, percentage after event, link to document

**Identifiers exposed per notification:**
- Issuer name (as listed on Euronext Milan)
- ISIN (reliable identifier for issuer resolution)
- Declarant name (raw, as submitted — may be a legal name variant)
- Notification date (date the form was submitted to CONSOB)
- Event date (date the threshold crossing occurred)
- Previous percentage (stake before event)
- New percentage (stake after event)
- Nature of holding (direct / indirect / both)
- Holding vehicle name (when indirect — not always structured)

**Robots.txt / ToS status:** Must be fetched before scraping. URL:
`https://www.consob.it/robots.txt` and `https://partecipazioni.consob.it/robots.txt`.
CONSOB is a public regulatory body; its publications are public record.
However, the portal terms may restrict automated bulk access. The pilot
must confirm ToS compliance before proceeding beyond three issuers.

**Recommended access pattern for the pilot:**
- Targeted per-issuer requests only (3 issuers, not bulk sweep)
- Use `Crawl-delay` from robots.txt; default to 5s between requests if
  not specified
- Preserve the raw HTML of each search result page in `context_sources`
  (via `raw_text`) for audit purposes
- No session or cookie injection required for public search pages

**Licensing / re-use:**  
CONSOB notifications are mandatory public disclosures under Italian law.
The underlying data (threshold crossing events) is regulatory fact.
The specific form PDFs are government-produced documents. Italian copyright
law (L. 633/1941 as amended) provides reduced protections for official
government documents when used for informational purposes.

**Rate limit estimate:**  
Not officially published. Conservative practice: 3–5 seconds between
page requests; do not issue more than 60 requests per hour in pilot mode.

**Automation appropriateness:**
- Public regulatory data: **appropriate** to collect per-issuer in targeted
  queries
- Bulk sweeps across all issuers: **defer** until legal/ToS review and full
  CONSOB source access confirmed
- No authentication required for search

---

### 2.2 Borsa Italiana / Euronext Milan — Disclosure Notices

**Publisher:** Borsa Italiana S.p.A. (Euronext Milan operator)  
**Legal basis:** Euronext Milan Rule Book; CONSOB market rules.

**What it contains relevant to Phase 17B:**  
- Ownership-related avvisi (notices): change of control applications, crossing
  of 30% threshold triggering mandatory bid, squeeze-out notices
- Corporate governance disclosures (board composition, committees) — relevant
  to Phase 17D governance events

**Portal URL:**  
`https://www.borsaitaliana.it/homepage/homepage.htm`

Avvisi search:
`https://www.borsaitaliana.it/borsa/notizie/comunicati/avvisi/home.html`

**Relevance to Phase 17B:**  
Secondary source for ownership events. The primary source for ownership
threshold crossings is CONSOB (§2.1). Borsa Italiana publishes buyback
avvisi (primary source for Phase 17E) and governance notices. Ownership
disclosures are routed primarily to CONSOB, not Borsa Italiana.

**Format:** HTML + PDF. The existing MAR Art 19 scraper already fetches
from this site — use the same session/robots patterns.

**Automation appropriateness:** Same site already scraped for MAR Art 19
filings. Extend cautiously to avvisi search if needed for Phase 17B corner
cases; do not duplicate existing session patterns.

---

### 2.3 Issuer Investor Relations Pages — Tertiary Source

**Publisher:** Individual listed companies (self-published)  
**Legal basis:** Transparency Directive Art. 19 (issuers must publish on their
websites); CONSOB Reg. Emittenti Art. 65-bis onwards.

**What they contain relevant to Phase 17B:**
- Re-published CONSOB notifications (required by law to be posted on issuer's website)
- Sometimes formatted better or with additional context vs. CONSOB portal
- Historic notifications may be more reliably archived on issuer sites

**Access:**  
No standard URL structure across issuers. Each issuer's IR section varies.
Typical path: `https://<issuer-domain>/investor-relations/shareholding-structure`

**Automation appropriateness for pilot:**  
Manual reference only. Do not scrape issuer websites in the pilot phase.
Use issuer IR pages as a cross-check when CONSOB notifications are ambiguous.

---

### 2.4 Official Gazette (Gazzetta Ufficiale) — Reference Only

**Publisher:** Italian Ministry of Economy, IPZS  
**URL:** `https://www.gazzettaufficiale.it/`

**Relevance:** Some corporate events (registered office changes, formal
company name changes, certain M&A completions) are published here. Not a
primary source for ownership threshold crossings. Listed for completeness;
not targeted in Phase 17B pilot.

---

## 3. Source taxonomy → `context_sources.source_type` mapping

| Official Source | `source_type` value |
|----------------|-------------------|
| CONSOB partecipazioni notification (any format) | `'regulatory_filing'` |
| Borsa Italiana avviso for governance/ownership | `'exchange_notice'` |
| Issuer investor-relations page (re-publication) | `'press_release'` |
| Gazzetta Ufficiale | `'official_gazette'` |
| Manually entered by operator | `'operator_entry'` |

---

## 4. Data quality and confidence assessment

### 4.1 CONSOB partecipazioni — quality tier: HIGH

| Field | Quality notes |
|-------|--------------|
| ISIN | Authoritative — CONSOB requires the correct ISIN on the notification form |
| Declarant legal name | Verbatim from filing — legal name, not always the trading name |
| Stake percentages | Required field on Modulo 103A — generally reliable |
| Event date | Required field — the date the crossing occurred, not the filing date |
| Filing date | Publication date on CONSOB portal |
| Direct/indirect | Required field — structured vocabulary on the form |
| Holding vehicle name | Free-text — often an abbreviated legal name; needs entity resolution |

### 4.2 Known parsing challenges

- **Indirect chains**: The form discloses the immediate holding vehicle but not
  the full control chain unless the declarant is required to file a complex structure.
  Do not infer the full chain from a single notification; store only what is
  explicitly stated.
- **Concert party notifications**: Multiple filings may arrive for the same
  economic event. Each gets its own `ownership_events` row; the link between
  them is stored in `entity_relationships` (deferred to Phase 17C).
- **Corrections**: CONSOB allows declarants to file correction notifications.
  These map to `event_type = 'cancellation'` on the original row + a new row
  with the corrected data, linked via `superseded_by`.
- **Threshold vocabulary**: Italian notifications state the percentage directly;
  the pilot collector must derive the crossed threshold by comparing
  `stake_pct_before` and `stake_pct_after` against the regulatory threshold list.

### 4.3 Fields NOT collected in the pilot

The following are not reliably available as structured machine-readable fields
from CONSOB notifications and must not be stored as facts without explicit
source text:

- Beneficial ownership beyond the immediate declarant (full ultimate beneficial
  owner) — unless explicitly stated in the filing
- Investment intent or purpose of the stake change
- Relationship between the declarant and the issuer's management
- Implied concert party membership not stated in the filing

---

## 5. Open questions requiring resolution before build

| # | Question | Action required |
|---|----------|----------------|
| Q1 | `partecipazioni.consob.it` robots.txt — does it restrict automated access? | Fetch `robots.txt` at pilot start; read before any scraping |
| Q2 | Exact URL pattern for per-issuer search by ISIN on the CONSOB portal | Inspect portal manually for 1 issuer; note URL parameters |
| Q3 | Is there an unofficial structured API (JSON/XML) behind the CONSOB search portal? | Inspect network requests in browser DevTools on the portal |
| Q4 | PDF vs. HTML notification format: does CONSOB always provide structured HTML alongside the PDF? | Inspect 2–3 notification pages on the portal |
| Q5 | Publication date vs. event date — does the HTML listing expose both, or only the notification date? | Check listing HTML against PDF content for 1 notification |
| Q6 | Which Italian issuers in the InsideWatch DB have CONSOB partecipazioni history? | Run query: `SELECT canonical_name, lei FROM issuers ORDER BY id LIMIT 50;` |
