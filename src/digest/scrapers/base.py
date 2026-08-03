import json
import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from bs4 import BeautifulSoup

from digest.config import settings
from digest.storage.db import ArticleDB
from digest.storage.models import ArticleRecord, Category, ScrapedPage

__all__ = ["BaseScraper", "Category"]

logger = logging.getLogger(__name__)

_RETRY_STATUSES = {429, 500, 502, 503, 504}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "Mar 8, 2023" / "October 16, 2025" — the byline format sites render for humans.
_VISIBLE_DATE_RE = re.compile(
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s*(20\d{2})",
    re.IGNORECASE,
)


def _normalize_date(raw: str) -> str | None:
    """Any date string we scrape → `YYYY-MM-DD`, or None if it is not a date.

    Returning None rather than a mangled prefix matters: JSON-LD `datePublished`
    is not always ISO 8601 (claude.com emits `"Oct 16, 2025"`), and truncating
    that to 10 characters yields `"Oct 16, 20"` — a value no later parse can
    reject. Callers fall through to their next date source instead.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    match = _VISIBLE_DATE_RE.fullmatch(raw)
    if match is None:
        return None
    month, day, year = match.groups()
    try:
        return date(int(year), _MONTHS[month.lower()], int(day)).isoformat()
    except ValueError:
        return None


def _is_too_old(published_date: str | None, cutoff: date) -> bool:
    """Return True if published_date is known and older than cutoff. None = unknown = keep."""
    if not published_date:
        return False
    try:
        return date.fromisoformat(published_date) < cutoff
    except ValueError:
        return False


class BaseScraper(ABC):
    company: str
    _sleep_between_requests: float = 1.0
    _user_agent: str = "ai-digest/1.0"
    known_issues: tuple[str, ...] = ()  # index-page Coverage note; see main._scraper_infos

    def __init__(self) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": self._user_agent},
            follow_redirects=True,
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, db: ArticleDB, limit: int | None = None, category: str | None = None) -> list[ScrapedPage]:
        """Discover URLs, deduplicate via SQLite, return pages needing summarization."""
        urls = self.discover_urls()
        if not urls:
            logger.warning(
                "%s: discover_urls() returned 0 URLs — site structure may have changed",
                self.company,
            )
            return []

        cutoff = (datetime.now(UTC) - timedelta(days=settings.max_article_age_days)).date()

        results: list[ScrapedPage] = []
        for i, (url, url_category) in enumerate(urls, 1):
            if category and url_category != category:
                continue
            if limit is not None and len(results) >= limit:
                break
            logger.info("%s: [%d/%d] %s", self.company, i, len(urls), url)
            try:
                page = self._process_url(url, url_category, db)
                if page is not None:
                    if _is_too_old(page.published_date, cutoff):
                        logger.debug(
                            "%s: skipping article older than %d days (%s) %s",
                            self.company, settings.max_article_age_days, page.published_date, url,
                        )
                        continue
                    results.append(page)
            except Exception:
                logger.exception("%s: unexpected error processing %s", self.company, url)
            time.sleep(self._sleep_between_requests)

        logger.info("%s: %d new/changed page(s) from %d discovered URL(s)", self.company, len(results), len(urls))
        return results

    # ------------------------------------------------------------------
    # Abstract interface for subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def discover_urls(self) -> list[tuple[str, Category]]:
        """Return list of (url, category) tuples to scrape."""

    @abstractmethod
    def scrape_page(self, url: str, category: Category) -> ScrapedPage | None:
        """Fetch and parse one page into a ScrapedPage. Return None on failure."""

    # ------------------------------------------------------------------
    # Deduplication hooks — override in subclasses for site-specific logic
    # ------------------------------------------------------------------

    def pre_check(self, url: str, existing: ArticleRecord) -> bool | None:
        """
        Lightweight change check before a full re-scrape.

        Returns:
            True  — definitely changed, proceed to full scrape
            False — definitely unchanged, skip
            None  — inconclusive, fall through to full scrape + hash comparison
        """
        try:
            resp = self.client.head(url)
            last_modified = resp.headers.get("last-modified")
            if last_modified:
                lm_dt = parsedate_to_datetime(last_modified)
                last_scraped = datetime.fromisoformat(existing.last_scraped_at)
                if last_scraped.tzinfo is None:
                    last_scraped = last_scraped.replace(tzinfo=UTC)
                return lm_dt > last_scraped
        except Exception:
            logger.debug("%s: pre_check HEAD failed for %s", self.company, url)
        return None

    def should_process(self, page: ScrapedPage, existing: ArticleRecord | None) -> bool:
        """Compare content hash to decide whether changed content needs re-summarization."""
        if existing is None:
            return True
        return page.content_hash != existing.content_hash

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_url(self, url: str, category: Category, db: ArticleDB) -> ScrapedPage | None:
        existing = db.get_by_url(url)

        if existing is None or not existing.summary:
            # No record, or a scrape-only record (e.g. from `--stage scrape`)
            # that was never summarized — always retry rather than letting
            # pre_check's freshness check skip it forever.
            return self._safe_scrape(url, category)

        changed = self.pre_check(url, existing)
        if changed is False:
            logger.debug("%s: unchanged (pre_check), skipping %s", self.company, url)
            return None
        if changed is True:
            logger.debug("%s: changed (pre_check), scraping %s", self.company, url)
            return self._safe_scrape(url, category)

        # pre_check inconclusive — full re-scrape + hash comparison
        page = self._safe_scrape(url, category)
        if page is None:
            return None
        if self.should_process(page, existing):
            return page
        logger.debug("%s: unchanged (hash), skipping %s", self.company, url)
        return None

    def _safe_scrape(self, url: str, category: Category) -> ScrapedPage | None:
        """Call scrape_page and catch all exceptions so one failure doesn't abort the run."""
        try:
            return self.scrape_page(url, category)
        except Exception:
            logger.warning("%s: scrape_page failed for %s", self.company, url, exc_info=True)
            return None

    def _fetch_page(self, url: str) -> str:
        return self._fetch_with_httpx(url)

    def _fetch_with_httpx(self, url: str) -> str:
        delay = 0.5
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(3):
            try:
                resp = self.client.get(url)
                if resp.status_code in _RETRY_STATUSES:
                    raise httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                return resp.text
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < 2:
                    logger.debug("%s: attempt %d failed (%s), retrying in %.1fs", self.company, attempt + 1, exc, delay)
                    time.sleep(delay)
                    delay *= 2
        raise last_exc

    @staticmethod
    def extract_text(html: str) -> str:
        """Strip nav/footer/script noise and return plain text from HTML."""
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)

    @staticmethod
    def extract_title(soup: BeautifulSoup) -> str:
        """og:title → h1 → <title>. Shared by the feed and sitemap scrapers."""
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return str(og["content"]).strip()
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        title_tag = soup.find("title")
        return title_tag.get_text(strip=True) if title_tag else ""

    @staticmethod
    def extract_date(soup: BeautifulSoup) -> str | None:
        """JSON-LD datePublished → article:published_time → <time datetime>, as YYYY-MM-DD.

        A source that holds something unparseable is skipped, not returned — see
        `_normalize_date`.
        """
        # JSON-LD is the most reliable source on modern SSR/SPA article pages.
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, AttributeError):
                continue
            for candidate in data if isinstance(data, list) else [data]:
                if not isinstance(candidate, dict):
                    continue
                for key in ("datePublished", "dateCreated"):
                    val = candidate.get(key)
                    if val and (normalized := _normalize_date(str(val))):
                        return normalized
        for prop in ("article:published_time", "og:article:published_time"):
            meta = soup.find("meta", property=prop)
            if meta and meta.get("content") and (normalized := _normalize_date(str(meta["content"]))):
                return normalized
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag and (normalized := _normalize_date(str(time_tag["datetime"]))):
            return normalized
        return None

    @staticmethod
    def extract_visible_date(soup: BeautifulSoup) -> str | None:
        """First text node that is *only* a `Mon D, YYYY` date, as YYYY-MM-DD.

        The last resort for pages that render a byline date but expose no
        machine-readable equivalent (every www.anthropic.com post). Requiring the
        node to be nothing but the date is what makes this safe: prose such as
        "applications close on January 20, 2025" is skipped, and article headers
        precede body copy in document order, so the first hit is the byline.
        """
        for text in soup.find_all(string=_VISIBLE_DATE_RE):
            if text.parent is not None and text.parent.name in ("script", "style"):
                continue
            if normalized := _normalize_date(str(text)):
                return normalized
        return None

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "BaseScraper":
        return self

    def __exit__(self, *_) -> None:
        self.close()
