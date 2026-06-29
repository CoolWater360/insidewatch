# Phase 17B — CONSOB Ownership & Control Source Assessment

Phase 17B Preparation · Schema readiness + source validation  
Status: **UPDATED — open questions resolved; go/no-go issued**  
Date: 2026-06-29 (research pass) · Updated: 2026-06-29 (source validation)

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

## 5. Open questions — confirmed findings

All six open questions have been investigated by fetching live CONSOB pages.
Evidence sources are listed per question. Conclusions replace the prior
"action required" column.

---

### Q1 — `partecipazioni.consob.it` robots.txt: does it restrict automated access?

**Status: RESOLVED — domain does not exist; main-domain robots.txt is permissive
with a 3-second Crawl-delay.**

**Evidence:**

- `https://partecipazioni.consob.it/` — ECONNREFUSED on two independent attempts.
  The subdomain `partecipazioni.consob.it` does not resolve. The reference to it
  in the Phase 17B design document (and in context-data-model.md Q6) is incorrect.
  The actual portal is hosted on `www.consob.it`.

- `https://www.consob.it/robots.txt` (fetched 2026-06-29):
  ```
  User-agent: *
  Crawl-delay: 3
  ```
  No `Disallow` or `Allow` directives. This means no URL path is technically
  restricted for automated access, but the 3-second Crawl-delay is a mandatory
  courtesy that the pilot collector must respect.

- `https://www.consob.it/web/consob/privacy` (fetched 2026-06-29):
  > "è vietata la raccolta massiva dei dati personali presenti nel sito istituzionale
  > della Consob allo scopo di addestrare modelli di intelligenza artificiale"
  > *(the massive collection of personal data from the CONSOB institutional website
  > for the purpose of training artificial intelligence models is prohibited)*

  This is the most substantive restriction found. It applies explicitly to:
  1. **Bulk/massive collection** of personal data
  2. **AI training** as the stated purpose

  InsideWatch's pilot (targeted per-issuer retrieval for financial intelligence)
  is categorically different from both prohibited uses, provided collection is
  not bulk and the purpose is financial analysis, not AI model training.

- `https://www.consob.it/web/consob/informazioni-legali` (fetched 2026-06-29):
  > "È consentita la consultazione, la stampa, il download e il riutilizzo dei
  > contenuti per finalità di studio, ricerca, informazione e documentazione,
  > con obbligo di citarne la fonte."
  > *(Consultation, printing, download, and reuse of content is permitted for
  > study, research, information and documentation purposes, with mandatory
  > attribution of the source.)*

  This is the operative permitted-use clause. Financial intelligence and
  market integrity analysis falls within "study, research, information and
  documentation." Source attribution is required in all uses.

**Conclusion:**

| Test | Result |
|------|--------|
| Technical access restriction (robots.txt Disallow) | None |
| Crawl-delay directive | 3 seconds (mandatory) |
| Bulk/AI-training prohibition | Yes — applies to massive personal-data collection |
| Permitted uses (legal notice) | Research/information/documentation with attribution |
| Pilot-scale targeted collection | Consistent with permitted uses |
| Bulk sweep of all issuers | Inconsistent with privacy notice; NO-GO |

---

### Q2 — Exact URL pattern for per-issuer search by ISIN

**Status: RESOLVED — no ISIN-based search URL exists. All access is either
slug-based (current state) or date-based (archive).**

**Evidence (fetched 2026-06-29):**

**Current shareholder state (azionariato attuale):**
```
https://www.consob.it/web/area-pubblica/w/{company-slug}-azionariato-{N}
```
Example confirmed:
- `https://www.consob.it/web/area-pubblica/w/brunello-cucinelli-spa-azionariato-1` → HTTP 200
- `https://www.consob.it/web/area-pubblica/w/de-longhi-spa-azionariato-1` → HTTP 200
- `https://www.consob.it/web/area-pubblica/w/italgas-spa-azionariato-1` → HTTP 200
- `https://www.consob.it/web/area-pubblica/w/mediobanca-spa-azionariato-1` → HTTP **404**

The slug format is `{canonical-name-lowercased-hyphenated}-azionariato-{version}`.
No version is predictable — some companies are on version 1, others 2 or higher.
The version suffix must be discovered by browsing the alphabetical list at:
`https://www.consob.it/web/area-pubblica/quotate/azionariati-attuali`

**URL parameter tests (both returned unfiltered results):**
```
partecipazioni-r-elenco?_it_consob_QuotateAssettiPortlet_emittente=IT0003115950 → no filter applied
partecipazioni-r-elenco?_it_consob_QuotateAssettiPortlet_isin=IT0003115950 → no filter applied
```
Neither URL parameter activates a filter. The notifications list ignores all
appended query parameters.

**Recent notifications (post-15 June 2026, new TR-1 system):**
```
https://www.consob.it/web/area-pubblica/quotate/partecipazioni-r-elenco
```
This single page shows only the most recent ~14 notifications across ALL issuers,
unfiltered. No ISIN or issuer-name filter is available.

**Archive notifications (pre-15 June 2026):**
```
https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-{YYYY-MM-DD}
```
Date-based only. Each date page aggregates all notifications published that day
across all issuers. No ISIN or issuer-name filter exists. To collect all
notifications for a specific issuer from the archive, the entire archive must
be paged through and issuer names matched in HTML.

**Notification document URL pattern (confirmed from listing):**
```
https://www.consob.it/documents/4980288/11150556/{CONSOB_ISSUER_ID}_{PROTOCOL_REF}-{YY}_{ISSUER_LEI}.pdf/{UUID}?t={TIMESTAMP}&download=true
```
Example (BlackRock → Leonardo, 26/06/2026):
```
https://www.consob.it/documents/4980288/11150556/30154_0066194-26_529900X4EEX1U9LN3U39.pdf/50e10d5c-986b-dd2c-f1d0-a9110ac7b1b1?t=1782488935515&download=true
```
Components:
- `30154` — CONSOB internal numeric issuer ID (Leonardo)
- `0066194-26` — CONSOB protocol number and year (YY = 2026 → `26`)
- `529900X4EEX1U9LN3U39` — **issuer's LEI** (not the declarant's LEI)
- UUID and timestamp are Liferay CMS file identifiers; not guessable

**Critical implication:** Document URLs cannot be constructed from an ISIN alone.
They require the issuer's CONSOB numeric ID, which is only discoverable by
navigating the azionariato listing pages.

---

### Q3 — Is there an unofficial structured API or JSON/XML feed?

**Status: RESOLVED — no API exists. Website is Liferay CMS with static HTML and
document storage. No structured data feed is available.**

**Evidence:**

- `https://www.consob.it/web/consob/opendata` → HTTP 404
- No API documentation, data download section, JSON/XML/CSV export, or OData
  endpoint is present anywhere on the portal (verified across five different
  section pages).
- The site is a Liferay CMS. Pages use Portlet URL parameters
  (`p_p_id=it_consob_QuotateAssettiPortlet`) but these serve page-state
  (pagination) rather than data filtering.
- The Liferay document storage (`/documents/4980288/11150556/...`) uses UUID-
  based routing that is not discoverable without the HTML listing page.
- Appending URL parameters for ISIN or keyword filtering to any listing page
  returns the unfiltered default result — no server-side filtering is activated.
- New notifications (post-15/06/2026) use the ESMA TR-1/MJSHLD PDF format, which
  contains structured XML metadata embedded in PDF object headers. This embedded
  metadata includes submitting entity LEI, submission type, issuer LEI, and
  creation date. Extracting it requires a PDF parser — it is not exposed as
  a separate API endpoint.

**Conclusion:** The only access paths are:
1. HTML page scraping (listing pages + per-date archive pages)
2. PDF download and parsing for full notification content

---

### Q4 — PDF vs. HTML notification format; is structured HTML always available?

**Status: RESOLVED — two distinct systems exist, split at 15 June 2026.**

**Pre-15 June 2026 (archive system):**
- Notifications are published as date-based HTML aggregate pages
- Each dated page contains structured text summaries for all notifications of that day
- **No individual PDF download links** appear in the archive HTML
- The HTML text provides: declarant name, issuer name, percentage before/after,
  nature of holding, event date
- Example from `/w/comunicazioni-relative-a-partecipazioni-rilevanti-2026-06-03`:
  > ATHENA FH SPA → CAREL INDUSTRIES SPA · event date 21/05/2026 · 18.690%
  > DIRETTA PROPRIETA' · previous 24.580% (14/12/2023)
- Archive HTML is parseable as structured text without PDF download

**Post-15 June 2026 (new TR-1 system):**
- The listing at `partecipazioni-r-elenco` shows recent notifications with
  direct PDF download links (no HTML alternative)
- PDF format: ESMA TR-1/MJSHLD, 10-page A4, FlateDecode compressed, ~250 KB
- Metadata extractable from PDF headers without full decompression: submitting
  entity LEI, issuer LEI, submission type (CORR / ORIG), creation date
- Full content (percentages, dates, holding structure) requires PDF decompression
  and text extraction (pdfplumber or equivalent)
- HTML listing provides: publication date, issuer name, declarant name, PDF link
  (no percentage, no event date in HTML)

**Practical implication for pilot:**

| Period | Format | PDF required? | Event date in HTML? |
|--------|--------|--------------|---------------------|
| Pre-15 Jun 2026 (archive) | HTML text | No | Yes |
| Post-15 Jun 2026 (new) | PDF (MJSHLD) | Yes | No (HTML only has pub date) |

---

### Q5 — Publication date vs. event date: which dates are in the HTML listing?

**Status: RESOLVED — diverges by system.**

**New notifications listing (`partecipazioni-r-elenco`) — post-15/06/2026:**
- Table has **one date column only**: `data pubblicazione` (publication date)
- Event date (the date the threshold was actually crossed) is NOT in the HTML
- Event date is inside the PDF only; extraction requires PDF parsing

**Archive HTML pages — pre-15/06/2026:**
- Both publication date AND event date are present in the HTML text body
- Confirmed from the June 3, 2026 archive page:
  > "Event Date: 26/05/2026 | Previous: 12/05/2026" (for BFF Bank / JPMorgan notification)
- The archive HTML also shows the date of the prior notification (not prior event)
- Percentage before and after are both present in the HTML text

**Consequence for `ownership_events` mapping:**
- For archive records: `event_date` can be extracted from HTML text directly;
  `context_sources.publication_timestamp` is derived from the dated page URL
- For new records: `event_date` requires PDF parsing;
  `context_sources.publication_timestamp` is in the HTML listing

---

### Q6 — Which issuers in the InsideWatch DB have CONSOB partecipazioni history?

**Status: PARTIALLY RESOLVED — requires an internal DB query for full answer;
CONSOB evidence confirms several well-known issuers active in 2025–2026.**

**Cannot be answered from public CONSOB sources alone.** This question requires:
```sql
SELECT canonical_name, lei, id
FROM issuers
ORDER BY canonical_name;
```
Run against the InsideWatch Supabase project using the service-role key.
This is an internal step to be executed at pilot start, not a public source.

**What CONSOB evidence confirms (issuers with notifications in May–June 2026):**

| Issuer | Last notification (approx.) | Notes |
|--------|----------------------------|-------|
| AVIO SPA | Jun 2026 | Barclays recent activity |
| BFF BANK SPA | Jun 2026 | JPMorgan recent activity |
| BF SPA | Jun 2026 | Vecchioni Federico |
| BRUNELLO CUCINELLI SPA | Jun 2025 | Trust structure (Foro delle Arti S.r.l.) |
| CAREL INDUSTRIES SPA | May 2026 | Athena FH stake change |
| ENI SPA | Jun 2026 | BlackRock activity |
| FIDIA SPA | Jun 2026 | Multiple declarants |
| ITALMOBILIARE SPA | Frequent | Morgan Stanley (repeated, multiple recent) |
| LEONARDO — SOCIETA' PER AZIONI | Jun 2026 | BlackRock correction |
| LOTTOMATICA GROUP SPA | Nov 2025 | JPMorgan |
| OPS ECOM SPA | Jun 2026 | Giovanni De Angelis |
| RECORDATI SPA | May/Jun 2026 | PARJOINTCO SA |
| SAIPEM SPA | Jun 2026 | BlackRock |
| TELECOM ITALIA SPA | May 2026 | Morgan Stanley |
| TREVI FINANZIARIA INDUSTRIALE SPA | Jun 2026 | Anima SGR, Praude AM |

**Revised pilot issuer recommendations** (based on evidence of recent activity
and URL slug availability):

The original Phase 17B recommendations (De' Longhi, Mediobanca, Italgas) need
revision in light of findings:

| Original | Issue | Replacement recommendation |
|---------|-------|--------------------------|
| De' Longhi S.p.A. | Last notification September 2022 — 4 years stale; no recent archive entries found | **Brunello Cucinelli S.p.A.** — URL slug confirmed, last notification June 2025, trust structure (Foro delle Arti / Spafid Trust), clear and relatively simple |
| Mediobanca S.p.A. | URL slug at `mediobanca-spa-azionariato-1` returns 404; actual slug unknown | Mediobanca can remain if slug is found via alphabetical browse; otherwise replace with **Recordati S.p.A.** which has recent PARJOINTCO SA notifications |
| Italgas S.p.A. | Last major notification August 2019 — 7 years stale | **Italmobiliare S.p.A.** — highest notification frequency of any issuer observed (Morgan Stanley cross-threshold activity, multiple recent archive entries) |

Revised pilot trio (recommendation):
1. **Brunello Cucinelli S.p.A.** — simple family/trust structure, URL confirmed, recent notifications
2. **Mediobanca S.p.A.** (if slug resolves) or **Recordati Industria Chimica e Farmaceutica S.p.A.** — multi-shareholder, recent activity
3. **Italmobiliare S.p.A.** — highest notification frequency, best for pipeline testing

---

## 6. Go / No-Go decision for automated three-issuer pilot

**Decision date:** 2026-06-29  
**Based on:** Live CONSOB portal inspection (six pages fetched)

### 6.1 What is permitted

| Action | Assessment | Basis |
|--------|-----------|-------|
| Targeted per-issuer HTML page fetch for current shareholding state | **GO** | Public page, research purpose, 3s crawl delay |
| Targeted fetch of specific dated archive pages matching pilot issuers | **GO** | Same basis; targeted, not bulk |
| PDF download for pilot-issuer notifications from `partecipazioni-r-elenco` | **GO** | Direct PDF link in HTML; document is a regulatory public record |
| Recording `source_url`, `document_hash`, key fields in `context_sources` | **GO** | Research use with attribution; no modification of source |
| Automated text extraction from archive HTML for structured fields | **GO** | Equivalent to download + structured reading; same legal basis |
| Automated PDF text extraction (pdfplumber) for pilot notifications | **GO** | Same basis as PDF download |

### 6.2 What is not permitted

| Action | Assessment | Basis |
|--------|-----------|-------|
| Bulk sweep of all archive pages across all issuers | **NO-GO** | Constitutes "massive collection" under CONSOB privacy notice |
| Any collection framed as AI model training data | **NO-GO** | Explicitly prohibited by CONSOB privacy policy |
| Use of data for purposes other than financial intelligence / transparency | **NO-GO** | Prohibited under CONSOB legal notice (purpose limitation) |
| Guessing document URLs without fetching the listing page | **NO-GO** | URLs contain UUIDs that are not constructable from ISIN alone |
| Rate below 3-second crawl delay between requests | **NO-GO** | robots.txt Crawl-delay: 3 is mandatory |

### 6.3 Recommended collection method

**Manual-assisted retrieval with scripted recording** is the correct approach
for the pilot, for two reasons:

1. The archive system (pre-15/06/2026) has **no PDF links** — only HTML text
   summaries per dated page. A human must navigate the date-based archive,
   identify which pages contain the pilot issuers' notifications, and trigger
   data extraction for those specific pages. This is manual work, not automation.

2. The new system (post-15/06/2026) provides direct PDF links in the listing,
   but the listing is unfiltered — a collector must fetch the page, match issuer
   names against the pilot issuers, and download only matching PDFs.

**Recommended workflow for each pilot issuer:**

```
Step 1 (manual): Identify the correct azionariato URL slug by browsing:
  https://www.consob.it/web/area-pubblica/quotate/azionariati-attuali
  → Record the slug: {company-slug}-azionariato-{N}

Step 2 (manual): Fetch the current azionariato page to confirm the issuer is
  tracked by CONSOB and to note the date of the most recent notification.

Step 3 (manual): Identify relevant archive date pages by browsing:
  https://www.consob.it/web/area-pubblica/archivio-comunicazioni-delle-partecipazioni-rilevanti
  → For each dated page, visually confirm whether the pilot issuer appears.
  → Note the specific dated page URLs containing pilot-issuer notifications.

Step 4 (scripted): For each identified archive dated page URL, fetch and
  extract the structured text fields (declarant, issuer, percentages, date,
  nature) using a parser. Call upsert_context_source() with the dated page URL.

Step 5 (scripted): Fetch the current listing page (partecipazioni-r-elenco)
  on a recurring basis (rate: 3s crawl delay). For each notification, match
  issuer name against pilot issuers. If match: download PDF, compute SHA-256,
  call upsert_context_source(), insert ownership_events row.
```

### 6.4 Pilot go/no-go verdict

**GO — with the following conditions:**

1. Only the three pilot issuers (revised: Brunello Cucinelli, Mediobanca or
   Recordati, Italmobiliare) are targeted. No other issuers.
2. Crawl-delay of at minimum 3 seconds between all HTTP requests.
3. All source documents stored with source attribution (`publisher = 'CONSOB'`,
   `source_url` populated with the canonical CONSOB page URL).
4. No data used for AI model training.
5. All collection run in `DRY_RUN=True` mode first; operator reviews output
   before any DB write.
6. Step 3 (archive browsing) is performed manually, not by automated sweep.
7. Total number of archive pages fetched programmatically is bounded:
   maximum 30 date pages per pilot run (covers approximately 6 weeks of
   notifications — sufficient for recent pilot data).

**Next implementation step (Phase 17B.2):**

Write `scraper/ownership/collector.py` with:
- Archive HTML parser for pre-15/06/2026 dated pages
- New TR-1 listing parser for post-15/06/2026 PDFs
- Issuer name matching against pilot issuers list
- Integration with `context_db.upsert_context_source()` and
  `context_db.assert_source_provenance()`
- Dry-run mode (default True)
- 3-second crawl delay enforced

Do not write the collector until the operator has:
- Confirmed the revised pilot issuers
- Manually identified at least one archive notification per pilot issuer
- Confirmed the URL slug for Mediobanca (or substituted Recordati)
