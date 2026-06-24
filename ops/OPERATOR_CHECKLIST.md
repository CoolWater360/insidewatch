# InsideWatch — Operator Review Checklist

Run these reviews daily (or after any batch reprocessing).  Each section
includes the query to find the cases, the decision criteria, and the action.

All queries run in the Supabase SQL Editor or via `psql`.

---

## 1 — Unknown / low-confidence classifications

**When:** daily, and after every reprocessing run.

### 1a — Unknown transaction type

```sql
SELECT
    t.id,
    t.transaction_date,
    t.insider_name,
    c.name  AS company,
    t.direction,
    t.raw_nature_text,
    t.classification_rationale,
    t.classification_confidence,
    f.source_url
FROM transactions t
JOIN companies c ON c.id = t.company_id
LEFT JOIN filings f ON f.id = t.source_filing_id
WHERE t.transaction_type = 'unknown'
  AND t.is_current = true
ORDER BY t.transaction_date DESC
LIMIT 50;
```

**Decision criteria:**

| Scenario | Action |
|---|---|
| `raw_nature_text` is blank | Check the PDF at `source_url`; re-classify manually via internal console |
| Nature text is in an unusual language or format | Note in review; classify manually |
| Direction extracted but type still unknown | Check if a new keyword is needed in the classifier |

---

### 1b — Low-confidence classifications (confidence < 0.65)

```sql
SELECT
    t.id,
    t.transaction_date,
    t.insider_name,
    c.name  AS company,
    t.transaction_type,
    t.classification_confidence,
    t.classification_rationale,
    t.needs_review,
    t.raw_nature_text
FROM transactions t
JOIN companies c ON c.id = t.company_id
WHERE t.classification_confidence < 0.65
  AND t.is_current = true
  AND t.created_at > now() - interval '30 days'
ORDER BY t.classification_confidence ASC, t.transaction_date DESC
LIMIT 100;
```

**Decision criteria:**

| Confidence range | Meaning | Action |
|---|---|---|
| 0.55 | Vehicle/trust context detected | See Section 3 |
| 0.40 | Direction inferred from weak SI/YES flag | Verify against PDF |
| 0.35 | Vehicle context + unknown direction | Check PDF; may need manual classify |
| 0.30 | Fully undetermined | Must check PDF before any use in signals |

---

### 1c — Needs-review queue summary

```sql
SELECT
    transaction_type,
    COUNT(*)             AS count,
    ROUND(AVG(classification_confidence)::numeric, 3) AS avg_conf
FROM transactions
WHERE needs_review = true
  AND is_current   = true
  AND created_at > now() - interval '30 days'
GROUP BY transaction_type
ORDER BY count DESC;
```

**Target:** zero `unknown` rows; `review_rate_pct` < 20 % overall.

---

## 2 — Corporate-action cases

**When:** after any batch that includes FUSIONE, SCISSIONE, or CONVERSIONE filings.

```sql
SELECT
    t.id,
    t.transaction_date,
    t.insider_name,
    c.name  AS company,
    t.transaction_type,
    t.classification_confidence,
    t.classification_rationale,
    t.raw_nature_text,
    f.source_url
FROM transactions t
JOIN companies c ON c.id = t.company_id
LEFT JOIN filings f ON f.id = t.source_filing_id
WHERE t.is_current = true
  AND t.needs_review = true
  AND t.transaction_type = 'conversion'
  AND t.created_at > now() - interval '30 days'
ORDER BY t.transaction_date DESC;
```

**Decision criteria for each row:**

1. Open the PDF at `source_url`.
2. Identify the corporate event type:

| Event | Typical nature text | Correct type | Notes |
|---|---|---|---|
| Share-for-share merger | FUSIONE PER INCORPORAZIONE | `conversion` | Confirm exchange ratio in filing |
| Cash merger | FUSIONE CON CORRISPETTIVO IN DENARO | `sell` or `conversion` | Cash out → economic intent = discretionary; flag |
| Demerger (shares received) | SCISSIONE PROPORZIONALE | `conversion` | Passive receipt → `needs_review` is correct |
| Instrument conversion | CONVERSIONE DI OBBLIGAZIONI | `conversion` | Can clear `needs_review` if ratio explicit |
| Capital increase subscription | SOTTOSCRIZIONE | `subscription` | Reclassify if CONVERSIONE was wrong |

3. If the classification is confirmed: use the internal console to set
   `review_status = 'confirmed'` on the transaction.
4. If the classification needs correction: use the override endpoint.

---

## 3 — Vehicle / trust / fiduciary cases

**When:** after any batch; daily.

```sql
SELECT
    t.id,
    t.transaction_date,
    t.insider_name,
    c.name  AS company,
    t.transaction_type,
    t.economic_intent,
    t.classification_confidence,
    t.classification_rationale,
    t.raw_nature_text,
    f.source_url
FROM transactions t
JOIN companies c ON c.id = t.company_id
LEFT JOIN filings f ON f.id = t.source_filing_id
WHERE t.is_current = true
  AND t.classification_rationale LIKE '%vehicle_context%'
  AND t.created_at > now() - interval '30 days'
ORDER BY t.transaction_date DESC;
```

**For each row, confirm two things:**

**A — Beneficial owner identity.**
The filing should disclose that the named insider is the beneficial owner of
the vehicle.  If not disclosed, add a note and escalate.

**B — Economic substance.**
Determine whether the transaction is a true open-market trade or an
intra-group reorganisation:

| Scenario | Action |
|---|---|
| Insider bought shares on market via trust | Current type (buy/sell) is correct; set `review_status = 'confirmed'` |
| Shares moved between insider-controlled entities only | Reclassify to `transfer_in` or `transfer_out`; set `economic_intent = 'mechanical'` |
| Nominee relationship not disclosed | Flag for compliance review; do not use in signals |

**Rationale:** vehicle-context transactions are preserved as buy/sell
(confidence 0.55) rather than transfer, because most vehicle transactions
are genuine open-market trades.  The operator must confirm the few that are
not.

---

## 4 — DIRITTI DI OPZIONE (subscription rights) cases

```sql
SELECT
    t.id,
    t.transaction_date,
    t.insider_name,
    c.name  AS company,
    t.transaction_type,
    t.classification_rationale,
    t.raw_nature_text,
    f.source_url
FROM transactions t
JOIN companies c ON c.id = t.company_id
LEFT JOIN filings f ON f.id = t.source_filing_id
WHERE t.is_current = true
  AND t.classification_rationale LIKE '%DIRITTI DI OPZIONE without exercise verb%'
ORDER BY t.transaction_date DESC;
```

**Decision criteria:**

| Context | Correct type | How to distinguish |
|---|---|---|
| Capital increase — subscription rights | `subscription` (keep as-is) | Filing references a rights issue (`aumento di capitale`) |
| Employee stock option plan | `option_exercise` | Rationale mentions incentive plan; nature says "piano di incentivazione" |
| Ambiguous | Keep `subscription + needs_review` until PDF reviewed | — |

---

## 5 — Issuer-resolution queue

```sql
SELECT
    ui.id,
    ui.raw_name,
    ui.isin,
    ui.source_filing_id,
    ui.created_at,
    f.source_url
FROM unmatched_issuers ui
LEFT JOIN filings f ON f.id = ui.source_filing_id
WHERE ui.resolved_at IS NULL
ORDER BY ui.created_at DESC
LIMIT 50;
```

**For each unresolved issuer:**

1. Search the CONSOB database or Borsa Italiana for the company name.
2. If found, insert into `securities` (ISIN-level) or `issuer_aliases`:

```sql
-- Insert a new alias
INSERT INTO issuer_aliases (raw_name, issuer_id, source)
VALUES ('RAWNAME SPA', <issuer_id>, 'operator');

-- Mark resolved
UPDATE unmatched_issuers
SET resolved_at = now(), resolved_by = 'operator'
WHERE id = <ui.id>;
```

3. If the company is a legitimate but tiny issuer with no ISIN: insert into
   `companies` manually and mark the unmatched entry resolved.
4. If it looks like a parsing error (garbled text): check the PDF; if the
   filing is unreadable, set `status = 'failed'` on the filing and record a
   note.

---

## 6 — After reprocessing — diff review

After running `python3 -m scraper.reprocess_historical`, the script prints a
JSON summary.  The key fields to review:

| Field | Concern if... |
|---|---|
| `transactions_versioned` | > 10 % of total → parser change was substantial; review changed rows |
| `transactions_inserted` | > 0 for old filings → previously missing transactions found; good signal |
| Confidence distribution shift | Average confidence dropped → new rules fire more conservatively |

Query to find the versioned transactions from the latest reprocessing run:

```sql
SELECT
    t.id,
    t.transaction_date,
    t.insider_name,
    c.name    AS company,
    t.transaction_type,
    t.classification_confidence,
    v.snapshot->>'transaction_type'              AS old_type,
    (v.snapshot->>'classification_confidence')::float AS old_conf,
    t.classification_rationale                   AS new_rationale
FROM transactions t
JOIN companies c ON c.id = t.company_id
JOIN transaction_versions v ON v.transaction_id = t.id
WHERE v.changed_by = '1.2.0'
ORDER BY v.created_at DESC
LIMIT 100;
```

---

## 7 — Signal quality gates (before external use)

Do not publish or share any signal output until all of the following are
confirmed:

- [ ] `review_rate_pct` < 20 % (run `quality_snapshot --compare`)
- [ ] `unknown_direction_pct` < 5 %
- [ ] Zero `unknown` transaction types in the most recent 7 days
- [ ] All corporate-action cases reviewed (Section 2)
- [ ] All vehicle-context cases reviewed (Section 3)
- [ ] Issuer-resolution queue < 5 outstanding entries
- [ ] P95 end-to-end latency < 3 600 s (from the daily ops report)
- [ ] At least 7 days of stable metrics with no alert threshold breaches
