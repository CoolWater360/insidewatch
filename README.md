# Italian Internal Dealing Tracker (MVP)

A web app that scrapes publicly disclosed "internal dealing" transactions (insider/executive share trades) from Italian listed companies and presents them in a searchable dashboard with alerting for clustered insider buying.

**All source data is PUBLIC** (Borsa Italiana and CONSOB disclosures under MAR Art. 19). This tool only aggregates and displays public information — it does NOT execute trades, give investment advice, or manage anyone's assets.

---

## Tech Stack

- **Backend + Frontend**: Next.js (App Router, TypeScript) — Phase 3
- **Database**: Supabase (Postgres)
- **Scraper**: Python (requests, beautifulsoup4, pdfplumber)
- **Styling**: Tailwind CSS — Phase 3
- **Deployment**: Vercel (frontend/API) + Supabase (DB) + GitHub Actions (scraper cron)

---

## Setup

### Prerequisites

- Python 3.9+
- Supabase account (free tier OK) — [supabase.com](https://supabase.com)
- Node.js 18+ (Phase 3, when building the dashboard)

### 1. Clone & Install Dependencies

```bash
cd "Italian Internal Dealing Tracker"
pip install -r requirements.txt
```

### 2. Set Up Supabase Database

1. Create a new Supabase project at [supabase.com](https://supabase.com)
2. In the Supabase dashboard, open the SQL Editor and run the contents of `db/schema.sql` to create tables and indexes
3. Copy your project URL and anon key from **Settings → API**
4. Create `.env.local` in the project root:

```bash
cp .env.local.example .env.local
```

Then edit `.env.local` with your Supabase credentials:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-public-key
```

---

## Running the Scraper

### Phase 1: Test with One Company

Scrape 5 PDFs for ENI and print results to console (no database writes):

```bash
python3 -m scraper.run_phase1 --company "Eni" --max-pdfs 5
```

### Phase 2: Full Crawl with Supabase Storage

Crawl all FTSE MIB companies and upsert to Supabase (with deduplication):

```bash
# Crawl all companies (takes ~10 min for ~40 companies × 10 PDFs each)
python3 -m scraper.run_phase2

# Crawl only first 5 companies
python3 -m scraper.run_phase2 --limit 5

# Crawl only one company
python3 -m scraper.run_phase2 --company "Eni"

# Crawl with custom settings
python3 -m scraper.run_phase2 --limit 10 --max-pdfs 5 --delay 1.0
```

Each run produces a summary report:
- Companies crawled
- PDFs downloaded
- Transactions parsed & inserted
- Deduplication skip count
- Any errors

---

## Data Model

### companies

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | Primary key |
| ticker | text | e.g. "ENI" (unique) |
| isin | text | e.g. "IT0003132476" (unique) |
| name | text | Full company name |
| sector | text | e.g. "Energy", "Banking" |
| ir_internal_dealing_url | text | Link to IR internal dealing page |

### insiders

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | Primary key |
| full_name | text | e.g. "Paolo Zambelli" |
| role | text | e.g. "Amministratore" |
| company_id | bigint | FK to companies |

### transactions

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | Primary key |
| insider_id | bigint | FK to insiders |
| company_id | bigint | FK to companies |
| transaction_date | date | YYYY-MM-DD |
| filed_date | date | Date filed with Borsa Italiana |
| direction | enum | 'buy' \| 'sell' \| 'unknown' |
| instrument_type | text | e.g. "Azioni Ordinarie" |
| isin | text | Instrument ISIN |
| quantity | numeric | Number of shares/units |
| unit_price | numeric | Price per share (EUR) |
| total_value | numeric | Total value (EUR) |
| currency | text | 'EUR' (default) |
| source_url | text | Direct link to PDF on Borsa Italiana |
| raw_hash | text | SHA-256(insider+company+date+qty+price) — UNIQUE for deduplication |

---

## How the Scraper Works

### Phase 1: PDF Parsing (Complete ✅)

1. Fetches the Borsa Italiana listing page: `https://www.borsaitaliana.it/borsa/documenti/societa-quotate/internal-dealing.html`
2. Extracts PDF URLs for each company's internal dealing disclosures
3. Downloads PDFs directly from `nisavvsource/pdf/{year}/{id}.pdf`
4. Parses ESMA MAR Art 19 bilingual forms (handles 3 distinct layouts)
5. Extracts structured data: insider, role, company, direction, quantity, price, date
6. Computes `raw_hash` for deduplication
7. Prints results to console

**Verified on real data:**
- Eni (Layout 2): Bond buy, equity sells ✅
- Emak (Layout 1): Large equity buy, sell ✅
- Eph Invest (Layout 3): Compact form ✅
- Assicurazioni Generali: Free share grants, pledge releases ✅

### Phase 2: Storage & Multi-Company Crawl (In Progress)

1. Loads FTSE MIB company seed data from `db/seed_companies.csv`
2. Iterates each company, fetching up to N PDFs
3. Upserts transactions to Supabase with `raw_hash` deduplication
4. Rate-limits requests (default 1.5s between PDFs)
5. Logs per-run summary: inserted, skipped, errors

### Phase 3: Dashboard (Upcoming)

- Next.js app with paginated transaction table
- Filters: by company, direction, date range
- Per-company detail page with full history
- Mobile-responsive, minimal design

### Phase 4: Cluster Buying Signals (Upcoming)

- Flag when 2+ distinct insiders at the same company buy within 7 days
- Show grouped view of these "unusual activity" clusters
- Link to underlying transactions

---

## Caveats & Limitations

### PDF Parsing Fragility

Borsa Italiana PDFs follow the ESMA MAR Art 19 standard form, but:
- **Three distinct layouts** exist (bilingual expansive forms + compact 2016/523 form)
- Page structure varies slightly across companies
- OCR-heavy PDFs (scans) may fail text extraction

**Mitigation**: Parser gracefully skips malformed rows and logs warnings. A PDF with 1 good transaction and 1 bad one will still yield the good data.

### Limited Company Coverage

Starting with FTSE MIB (~40 companies). Expansion to smaller indices (FTSE Mid Cap, FTSE Micro Cap, AIM Italia) requires:
1. Updating `db/seed_companies.csv`
2. Verifying that companies have Borsa Italiana internal dealing disclosures
3. Re-running Phase 2 crawl

### No Real-Time Updates

Crawler runs on a schedule (e.g., daily via GitHub Actions cron). Data lag is ~24–48 hours behind actual filings.

### Direction Classification

- **BUY**: Acquisto, Purchase, Sottoscrizione, Esercizio, Assegnazione
- **SELL**: Cessione, Vendita, Sale, Trasferimento
- **UNKNOWN**: Any other transaction type (pledge releases, etc.) — flagged with warning

---

## Development

### File Structure

```
.
├── requirements.txt              # Python dependencies
├── .env.local.example            # Template for environment variables
├── db/
│   ├── schema.sql               # Supabase table definitions
│   └── seed_companies.csv       # FTSE MIB company list
├── scraper/
│   ├── __init__.py
│   ├── models.py                # Data classes
│   ├── fetcher.py               # Borsa Italiana HTTP client
│   ├── parser.py                # PDF text extraction & parsing
│   ├── db.py                    # Supabase operations
│   ├── run_phase1.py            # CLI: test one company
│   └── run_phase2.py            # CLI: crawl all, upsert DB
└── .claude/
    ├── launch.json              # Dev server config
    └── settings.local.json      # Claude Code settings
```

### Adding a New Company

1. Edit `db/seed_companies.csv` and add a row with ticker, ISIN, name, sector
2. Re-run Phase 2:
   ```bash
   python3 -m scraper.run_phase2 --company "NewCompany"
   ```

### Updating Parsing Logic

Edit `scraper/parser.py`:
- `_parse_insider_name()` — extract person's name
- `_parse_direction()` — classify buy/sell
- `_parse_price_volume()` — extract price & quantity
- `_parse_transaction_date()` — convert date formats

Test with Phase 1 first:
```bash
python3 -m scraper.run_phase1 --company "TestCo" --max-pdfs 2
```

---

## Deployment

### Running the Scraper on Schedule

GitHub Actions workflow (`.github/workflows/scrape.yml`):
- Runs daily at 9 AM UTC
- Crawls all companies
- Stores results in production Supabase

### Hosting the Dashboard

Phase 3 (Next.js app) will be deployed to Vercel:
1. Push code to GitHub
2. Vercel auto-deploys on push
3. Connected to Supabase for data

---

## Disclaimer

This tool is for **informational and research purposes only**. It does NOT:
- Execute trades or manage assets
- Provide investment advice
- Guarantee data accuracy
- Assume liability for trading decisions based on this data

See the dashboard footer for the full legal disclaimer.

---

## License

MIT (see LICENSE file if present)

---

## Next Steps (Phase 3 & 4)

- [ ] Build Next.js dashboard (transaction table, filters, per-company detail)
- [ ] Implement cluster-buying signal detection (2+ buys within 7 days)
- [ ] Deploy to Vercel + set up GitHub Actions cron
- [ ] Write integration tests for parser across PDF layouts
- [ ] Monitor Borsa Italiana for page structure changes
