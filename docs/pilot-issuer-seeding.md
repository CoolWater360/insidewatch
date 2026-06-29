# Phase 17B.3 — Pilot Issuer-Master Seeding

Controlled issuer-master seeding for the CONSOB ownership pilot.
Status: **seed data prepared; live seed applied via `import_issuer_list`**
Date: 2026-06-29

---

## 1. Purpose

The ownership-pilot dry run ([docs/ownership-source-assessment.md](ownership-source-assessment.md) §7)
correctly **blocked** all three records because the pilot issuers did not exist
in the `issuers` master, and `ownership_events.issuer_id` is `NOT NULL`.

This phase adds **only these three issuers** so the dry run can resolve them by
**exact** match (ISIN / alias / canonical name — no fuzzy matching). No other
issuers are seeded; no broad backfill is performed.

The seed lives in a dedicated file, `data/seed_pilot_issuers.csv`, imported with
the existing idempotent loader:

```bash
python3 -m scraper.import_issuer_list --file data/seed_pilot_issuers.csv --dry-run
python3 -m scraper.import_issuer_list --file data/seed_pilot_issuers.csv
```

The loader (`scraper/import_issuer_list.py`) is idempotent: it skips issuers
that already exist by `canonical_name` and only adds missing aliases/securities.

---

## 2. Why a dedicated 3-row seed (not the full `seed_issuers_italy.csv`)

`data/seed_issuers_italy.csv` contains ~12 issuers. Importing it would be a
**broad backfill**, which this phase explicitly forbids. A dedicated 3-row file
guarantees only the pilot issuers are written. It is also more auditable: the
pilot seed is self-contained and reviewable in one place.

Note: `seed_issuers_italy.csv` already contained a Mediobanca row, but with
aliases (`Mediobanca SpA|MEDIOBANCA SPA`) that do **not** include the exact
CONSOB notice form `MEDIOBANCA - BANCA DI CREDITO FINANZIARIO S.P.A.`. The pilot
seed uses the same `canonical_name` (`Mediobanca S.p.A.`) for consistency and
adds the source-observed alias required for exact resolution.

---

## 3. Source table (authoritative / primary sources only)

| Issuer | Canonical legal name | ISIN | Ticker | Market | Country | Source (verbatim-checked) |
|--------|---------------------|------|--------|--------|---------|---------------------------|
| Brunello Cucinelli | Brunello Cucinelli S.p.A. | `IT0004764699` | BC | Euronext Milan | IT | Borsa Italiana scheda `IT0004764699-MTAA` |
| Mediobanca | Mediobanca S.p.A. (legal: *Mediobanca – Banca di Credito Finanziario S.p.A.*) | `IT0000062957` | MB | Euronext Milan | IT | Borsa Italiana scheda `IT0000062957` |
| Italmobiliare | Italmobiliare S.p.A. | `IT0005253205` | ITM | Euronext STAR Milan | IT | Borsa Italiana scheda `IT0005253205-MTAA` |

**Authoritative source URLs:**

- Brunello Cucinelli — `https://www.borsaitaliana.it/borsa/azioni/scheda/IT0004764699-MTAA.html?lang=en`
  (ISIN `IT0004764699`, ticker `BC`, Euronext Milan, verbatim). Investor relations:
  `https://investor.brunellocucinelli.com/` · Website: `https://www.brunellocucinelli.com/`
- Mediobanca — `https://www.borsaitaliana.it/borsa/azioni/scheda/IT0000062957.html`
  (ISIN `IT0000062957`, ticker `MB`, Euronext Milan, verbatim). Investor relations:
  `https://www.mediobanca.com/en/investor-relations.html` · Website: `https://www.mediobanca.com/`
- Italmobiliare — `https://www.borsaitaliana.it/borsa/azioni/scheda/IT0005253205-MTAA.html?lang=en`
  (ISIN `IT0005253205`, ticker `ITM`, Euronext STAR Milan, verbatim). Investor relations:
  `https://www.italmobiliare.it/en/investor-relations/` · Website: `https://www.italmobiliare.it/`

**Not seeded (deliberately conservative):**

- **LEIs** are omitted. They were not independently verified against GLEIF in
  this pass, and the phase rules prohibit inferring LEIs without a primary
  source. LEI can be backfilled later from GLEIF if needed; it is not required
  for exact resolution (ISIN + name aliases suffice).
- No parent/subsidiary, beneficial-owner, or control relationships are recorded.

---

## 4. Exact canonical / alias values added

`create_issuer` automatically registers the `canonical_name` as a primary
`name` alias, the `isin` as a `securities` row + `isin` alias, and the `ticker`
as a `ticker` alias. The `aliases` column below adds the **exact source-observed
CONSOB notice form** required so the archive-HTML records (which carry no ISIN)
resolve by name.

| Issuer | `canonical_name` | `isin` | `ticker` | Added `name` alias (source-observed) | Where observed |
|--------|------------------|--------|----------|--------------------------------------|----------------|
| Brunello Cucinelli | `Brunello Cucinelli S.p.A.` | `IT0004764699` | `BC` | `BRUNELLO CUCINELLI SPA` | CONSOB notice 2026-05-06 |
| Mediobanca | `Mediobanca S.p.A.` | `IT0000062957` | `MB` | `MEDIOBANCA - BANCA DI CREDITO FINANZIARIO S.P.A.` | CONSOB notice 2025-09-23 |
| Italmobiliare | `Italmobiliare S.p.A.` | `IT0005253205` | `ITM` | `ITALMOBILIARE SPA` | CONSOB notice 2026-06-03 |

The "where observed" notices are the operator-supplied CONSOB archive pages:

- `https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2026-05-06`
- `https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2025-09-23`
- `https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2026-06-03`

Aliases are limited strictly to these source-observed variants (plus the
auto-registered canonical name, ISIN, and ticker). No fuzzy or speculative
variants were added.

---

## 5. How resolution will succeed after seeding

`scraper/ownership/resolver.resolve_issuer_exact` matches, in order:

1. **ISIN** (`securities.isin`) — used by future TR-1 PDF records (which carry ISIN).
2. **Alias** (`issuer_aliases.alias`, case-insensitive equality, no wildcard).
3. **Canonical name** (`issuers.canonical_name`, case-insensitive equality).

The pilot archive-HTML records carry no ISIN, so they resolve via step 2 against
the source-observed `name` aliases above (case-insensitive: the uppercase CONSOB
form matches the stored alias).

---

## 6. Important: seeding does not unblock `--apply`

Seeding only resolves the issuer FK. It does **not** authorize ownership
ingestion. The ownership collector remains dry-run only. In particular, the
Brunello Cucinelli record stays `event_type = other` / `review_status =
pending_review` because its prior notification carries an ambiguous `a)/b)`
split (direction undetermined) — seeding does not change that.
