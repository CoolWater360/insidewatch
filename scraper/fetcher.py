"""HTTP fetching: listing pages and PDF downloads from Borsa Italiana."""

import logging
import re
import time
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup

from .models import ListingRow

logger = logging.getLogger(__name__)

BASE_URL = "https://www.borsaitaliana.it"
LISTING_URL = f"{BASE_URL}/borsa/documenti/societa-quotate/internal-dealing.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_RETRIES = 3
BACKOFF_SECONDS = (5, 20, 60)


class BlockedError(Exception):
    """Raised when the server returns a CAPTCHA, block, or rate-limit response."""


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _extract_pdf_path(href: str) -> Optional[str]:
    """Extract the /nisavvsource/... path from a dealing.htm?filename= href."""
    m = re.search(r'filename=(/[^\s"&]+\.pdf)', href)
    return m.group(1) if m else None


def _check_for_block(resp: requests.Response) -> None:
    """Raise BlockedError if the response looks like a rate-limit, ban, or CAPTCHA."""
    if resp.status_code == 429:
        raise BlockedError("Rate-limited (HTTP 429)")
    if resp.status_code == 403:
        raise BlockedError("Forbidden (HTTP 403)")
    url_lower = resp.url.lower()
    if any(kw in url_lower for kw in ("captcha", "blocked", "accessdenied", "denied")):
        raise BlockedError(f"Redirected to block page: {resp.url}")
    sample = resp.text[:2000].lower()
    if any(kw in sample for kw in ("captcha", "access denied", "bot detection", "please verify you are")):
        raise BlockedError("CAPTCHA/block content detected in response body")


def _get_with_retry(
    session: requests.Session,
    url: str,
    params: Optional[dict] = None,
    timeout: int = 15,
) -> requests.Response:
    """
    GET with up to MAX_RETRIES attempts and exponential backoff on transient errors.

    BlockedError is re-raised immediately (no retry — backing off won't help a ban).
    Timeout, ConnectionError, and non-block HTTP errors are retried.
    """
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            _check_for_block(resp)
            resp.raise_for_status()
            return resp
        except BlockedError:
            raise
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                delay = BACKOFF_SECONDS[attempt]
                logger.warning(
                    "Attempt %d/%d failed (%s) — retrying in %ds",
                    attempt + 1, MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
            else:
                logger.error("All %d attempts failed for %s: %s", MAX_RETRIES, url, exc)
    raise last_exc


def fetch_listing_page(
    session: requests.Session,
    letter: str = "",
    page: int = 1,
    timeout: int = 15,
) -> list[ListingRow]:
    """
    Fetch one page of the Borsa Italiana internal dealing listing.

    `letter` filters by company name first letter (A-Z). Empty string = all companies.
    Returns a list of ListingRow objects parsed from the HTML fragment.
    Raises BlockedError if the server blocks or rate-limits us.
    Retries up to MAX_RETRIES times on transient network errors.
    """
    params: dict[str, str | int] = {
        "lang": "en",
        "ord": "date",
        "mod": "down",
    }
    if letter:
        params["companyName"] = letter.upper()
    if page > 1:
        params["page"] = page

    resp = _get_with_retry(session, LISTING_URL, params=params, timeout=timeout)

    soup = BeautifulSoup(resp.text, "html.parser")
    rows: list[ListingRow] = []

    for tr in soup.select("table tr"):
        cells = tr.select("td span.t-text")
        if len(cells) < 3:
            continue

        company_name = cells[0].get_text(strip=True)
        filing_date = cells[1].get_text(strip=True)

        link = cells[2].find("a")
        if not link:
            continue

        href = link.get("href", "")
        pdf_path = _extract_pdf_path(href)
        if not pdf_path:
            logger.warning("Could not parse PDF path from href: %s", href)
            continue

        rows.append(ListingRow(
            company_name=company_name,
            filing_date=filing_date,
            pdf_path=pdf_path,
            pdf_url=f"{BASE_URL}{pdf_path}",
        ))

    return rows


def iter_company_listings(
    session: requests.Session,
    letter: str,
    company_name: str,
    max_pdfs: int = 10,
    polite_delay: float = 1.0,
    max_pages: int = 25,
) -> Iterator[ListingRow]:
    """
    Iterate listing rows for a given company name, paginating automatically.

    Stops after `max_pdfs` matching rows, `max_pages` scanned, or when no more
    pages exist. `company_name` is matched case-insensitively; empty string =
    all companies on the given letter page.
    Propagates BlockedError to the caller so it can skip the company.
    """
    page = 1
    seen = 0

    while seen < max_pdfs and page <= max_pages:
        rows = fetch_listing_page(session, letter=letter, page=page)
        if not rows:
            break

        for row in rows:
            if company_name and row.company_name.lower() != company_name.lower():
                continue
            yield row
            seen += 1
            if seen >= max_pdfs:
                return

        page += 1
        time.sleep(polite_delay)

    if page > max_pages:
        logger.warning(
            "Reached max_pages=%d scanning for %r — stopping early",
            max_pages,
            company_name,
        )


def download_pdf(session: requests.Session, url: str, timeout: int = 15) -> bytes:
    """
    Download a PDF and return its raw bytes.
    Retries up to MAX_RETRIES times on transient errors.
    Raises BlockedError if blocked/rate-limited.
    """
    resp = _get_with_retry(session, url, timeout=timeout)
    if "application/pdf" not in resp.headers.get("content-type", ""):
        logger.warning(
            "Response for %s may not be a PDF (content-type: %s)",
            url, resp.headers.get("content-type"),
        )
    return resp.content
