# InsideWatch API v1

Private institutional REST API for programmatic access to Italian insider-dealing data.

## Authentication

All endpoints require a `Bearer` token:

```
Authorization: Bearer <INSIDEWATCH_API_KEY>
```

The key is configured via the `INSIDEWATCH_API_KEY` environment variable on the server. Contact the operator to obtain a key.

| Status | Meaning |
|--------|---------|
| `401`  | Missing or invalid token |
| `503`  | API key not configured on the server |

## Rate Limiting

60 requests per minute per API key (configurable via `RATE_LIMIT_RPM`). Every authenticated request is logged to `api_audit_log`; rate limiting is enforced against a 60-second rolling window.

Response headers on allowed requests:

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Requests allowed per window |
| `X-RateLimit-Remaining` | Requests remaining in current window |
| `X-RateLimit-Reset` | ISO 8601 timestamp when window resets |

When the limit is exceeded, the server returns `429 Too Many Requests` with body:
```json
{ "error": "Rate limit exceeded. Try again later.", "retry_after_seconds": 60 }
```

## Response Envelope

All JSON responses follow this shape:

```json
{
  "data": [...],
  "pagination": {
    "page": 1,
    "page_size": 25,
    "total": 142,
    "total_pages": 6
  },
  "meta": {
    "version": "v1",
    "generated_at": "2026-06-24T09:00:00.000Z"
  }
}
```

Single-resource endpoints (`/transactions/:id`, `/filings/:id`, `/issuers/:id`) return `data` as a one-element array.

## Pagination

| Param | Default | Max |
|-------|---------|-----|
| `page` | `1` | — |
| `page_size` | `25` | `100` |

## CSV Export

Endpoints that support `format=csv` return a `text/csv` download with `Content-Disposition: attachment`. The CSV uses RFC 4180 quoting (values containing commas, double-quotes, or newlines are quoted; embedded double-quotes are doubled).

---

## Endpoints

### GET /api/v1/health

Operational health check. Does **not** count against your rate limit (no `api_audit_log` write).

**Response**
```json
{
  "status": "ok",
  "version": "v1",
  "db_ok": true,
  "scraper_last_run": "2026-06-24T06:00:00Z",
  "last_filing_at": "2026-06-24T07:12:00Z",
  "generated_at": "2026-06-24T09:00:00Z"
}
```

`status` values: `ok` | `degraded` (scraper stale > 26 h) | `error` (DB unreachable).

---

### GET /api/v1/transactions

Returns a paginated list of insider transactions.

**Query Parameters**

| Param | Type | Description |
|-------|------|-------------|
| `company_id` | integer | Filter by company |
| `direction` | `buy` \| `sell` \| `unknown` | Filter by direction |
| `date_from` | `YYYY-MM-DD` | Inclusive lower bound on `transaction_date` |
| `date_to` | `YYYY-MM-DD` | Inclusive upper bound on `transaction_date` |
| `transaction_type` | string | Filter by exact type (see taxonomy below) |
| `cash_only` | `true` \| `false` | Exclude grants and option exercises (default: `true`) |
| `sort` | `transaction_date` \| `filed_date` \| `total_value` \| `quantity` | Sort column (default: `transaction_date`) |
| `order` | `asc` \| `desc` | Sort direction (default: `desc`) |
| `page` | integer | Page number (default: `1`) |
| `page_size` | integer | Results per page, max 100 (default: `25`) |
| `format` | `json` \| `csv` | Response format (default: `json`) |

**Transaction type taxonomy**

Discretionary: `buy`, `sell`, `subscription`  
Mechanical: `grant`, `option_exercise`, `sell_to_cover`, `conversion`, `inheritance`, `gift_in`, `gift_out`, `transfer_in`, `transfer_out`, `pledge_or_security`, `derivative_transaction`  
Unclear: `other`, `unknown`

**Example**
```
GET /api/v1/transactions?company_id=12&direction=buy&date_from=2026-01-01&page_size=50
```

**Data fields** (each element of `data`)

```json
{
  "id": 4821,
  "transaction_date": "2026-06-20",
  "filed_date": "2026-06-21",
  "company_id": 12,
  "insider_id": 33,
  "direction": "buy",
  "transaction_type": "buy",
  "economic_intent": "discretionary",
  "quantity": 10000,
  "unit_price": 14.52,
  "total_value": 145200,
  "currency": "EUR",
  "instrument_type": "ordinary share",
  "isin": "IT0000072618",
  "review_status": "confirmed",
  "source_url": "https://...",
  "companies": { "id": 12, "name": "Acme S.p.A.", "ticker": "ACM", "sector": "Energy" },
  "insiders": { "full_name": "Mario Rossi", "role": "CEO" }
}
```

---

### GET /api/v1/transactions/:id

Returns a single transaction with relations.

**Path Parameters**

| Param | Description |
|-------|-------------|
| `id` | Transaction ID (integer) |

**HTTP status codes**

| Code | Meaning |
|------|---------|
| `200` | Found |
| `400` | Non-numeric id |
| `404` | Transaction not found |

---

### GET /api/v1/filings/:id

Returns a single filing record including all latency timestamps.

**Path Parameters**

| Param | Description |
|-------|-------------|
| `id` | Filing ID (integer) |

**Data fields**

```json
{
  "id": 1001,
  "pdf_url": "https://...",
  "filing_date": "2026-06-20",
  "company_name": "Acme S.p.A.",
  "status": "completed",
  "attempt_count": 1,
  "source_published_utc": "2026-06-20T09:00:00Z",
  "discovered_utc": "2026-06-20T10:00:00Z",
  "downloaded_utc": "2026-06-20T10:00:05Z",
  "parsed_utc": "2026-06-20T10:00:07Z",
  "validated_utc": "2026-06-20T10:00:07Z",
  "delivered_utc": "2026-06-20T10:00:08Z",
  "completed_at": "2026-06-20T10:00:08Z"
}
```

**HTTP status codes**: `200` | `400` (non-numeric id) | `404` (not found)

---

### GET /api/v1/issuers

Returns the issuer master list.

**Query Parameters**

| Param | Type | Description |
|-------|------|-------------|
| `status` | `active` \| `delisted` \| `suspended` \| `pending_review` | Lifecycle filter (default: `active`) |
| `country` | ISO alpha-2 | Country of incorporation (default: all; most are `IT`) |
| `page` | integer | Page number (default: `1`) |
| `page_size` | integer | Max 100 (default: `50`) |

**Data fields**

```json
{
  "id": 7,
  "canonical_name": "Eni S.p.A.",
  "short_name": "Eni",
  "lei": "549300G1X9H2VY4NWJ69",
  "country": "IT",
  "market": "MTA",
  "sector": "Energy",
  "status": "active",
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

---

### GET /api/v1/issuers/:id

Returns a single issuer with all its aliases.

**Path Parameters**

| Param | Description |
|-------|-------------|
| `id` | Issuer ID (integer) |

**Data fields** — same as list endpoint plus:

```json
{
  "issuer_aliases": [
    { "id": 31, "alias": "ENI", "alias_type": "ticker", "is_primary": true, "source": "manual" },
    { "id": 32, "alias": "Eni S.p.A.", "alias_type": "name",   "is_primary": false, "source": "scraper" }
  ]
}
```

**HTTP status codes**: `200` | `400` (non-numeric id) | `404` (not found)

---

### GET /api/v1/signals/cluster-buys

Returns cluster-buy signals: episodes where 2+ distinct insiders at the same company made discretionary purchases within a 7-day window. Non-cash transactions (grants, option exercises) are excluded.

**Query Parameters**

| Param | Type | Description |
|-------|------|-------------|
| `lookback_days` | integer 7–365 | Lookback window (default: `90`) |
| `format` | `json` \| `csv` | Response format (default: `json`) |

Results are sorted most-recent first by `window_end`.

**Data fields**

```json
{
  "company_id": 12,
  "company_name": "Acme S.p.A.",
  "window_start": "2026-06-10",
  "window_end": "2026-06-15",
  "insiders": [
    {
      "name": "Mario Rossi",
      "role": "CEO",
      "date": "2026-06-10",
      "quantity": 10000,
      "total_value": 145200,
      "transaction_type": "buy"
    },
    {
      "name": "Anna Bianchi",
      "role": "CFO",
      "date": "2026-06-15",
      "quantity": 5000,
      "total_value": 73200,
      "transaction_type": "buy"
    }
  ],
  "total_value": 218400,
  "cash_value": 218400
}
```

**CSV format** — one row per insider per cluster, with cluster-level fields repeated:

| Column | Description |
|--------|-------------|
| `company_id` | |
| `company_name` | |
| `window_start` | |
| `window_end` | |
| `cluster_total_value` | Sum of all insiders in window |
| `cluster_cash_value` | Sum excluding non-cash |
| `insider_name` | |
| `insider_role` | |
| `insider_date` | |
| `insider_quantity` | |
| `insider_total_value` | |
| `transaction_type` | |

---

## Error Responses

All error responses use this shape:

```json
{ "error": "Human-readable message." }
```

| Code | Meaning |
|------|---------|
| `400` | Invalid request parameter |
| `401` | Missing or invalid API key |
| `404` | Resource not found |
| `429` | Rate limit exceeded |
| `500` | Internal server error |
| `503` | API not configured |

---

## Changelog

| Version | Date | Notes |
|---------|------|-------|
| v1 | 2026-06-24 | Initial release — Phase 10 |
