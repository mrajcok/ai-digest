"""Sitemap scraper — docs/plan.md Step 4.

Anthropic publishes no RSS feed, so it is scraped from `sitemap.xml` instead:
a flat `<urlset>` of `<loc>`/`<lastmod>` pairs.

`lastmod` is a CMS *touch* timestamp, not a publication date — it mirrors the
Sanity `_updatedAt` field, so a bulk migration restamps hundreds of old posts at
once (on 2026-08-02, 30 articles going back to 2023 all read `2026-07-08`). It
is therefore used only where being wrong in that direction is harmless:

- as the discovery-time age filter, where it is an *upper* bound on the
  publication date — too new never drops a genuinely new article, and the real
  cutoff is re-applied in `BaseScraper.run()` against the extracted date;
- in `pre_check()`, where a bumped `lastmod` is inconclusive and falls through
  to the content-hash compare, so a restamp costs a fetch but not an LLM call;
- as the `published_date` of last resort, with a warning, when the page itself
  yields no date at all.

Mirrors `scrapers/feed.py`'s shape: a pure `parse_sitemap()` for unit tests, a
`*Meta` cache populated in `discover_urls()` and read by `scrape_page()`.

"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import ClassVar
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from digest.config import settings
from digest.scrapers.base import BaseScraper, Category
from digest.sources import Source, sources_for
from digest.storage.models import ArticleRecord, ScrapedPage

__all__ = ["SitemapEntry", "SitemapScraper", "parse_sitemap"]

logger = logging.getLogger(__name__)

_THIN_CONTENT_CHARS = 200


@dataclass(frozen=True)
class SitemapEntry:
    """One `<url>` element, as it appears in the sitemap — no filtering applied."""

    url: str
    lastmod: date | None


@dataclass(frozen=True)
class SitemapMeta:
    """What `discover_urls()` learned about a URL, for `scrape_page()` to reuse."""

    entry: SitemapEntry
    source: Source
    category: Category


def _localname(tag: str) -> str:
    """`{http://www.sitemaps.org/schemas/sitemap/0.9}url` → `url`."""
    return tag.rpartition("}")[2] if "}" in tag else tag


def _parse_lastmod(raw: str) -> date | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_sitemap(xml: str) -> list[SitemapEntry]:
    """Parse a `<urlset>` into entries, in document order. Malformed XML yields []."""
    try:
        root = ET.fromstring(xml.strip())
    except ET.ParseError as exc:
        logger.warning("sitemap XML parse error: %s", exc)
        return []

    entries: list[SitemapEntry] = []
    for url_el in root:
        if _localname(url_el.tag) != "url":
            continue
        loc = ""
        lastmod: date | None = None
        for child in url_el:
            name = _localname(child.tag)
            if name == "loc" and child.text:
                loc = child.text.strip()
            elif name == "lastmod" and child.text:
                lastmod = _parse_lastmod(child.text)
        if loc:
            entries.append(SitemapEntry(url=loc, lastmod=lastmod))
    return entries


class SitemapScraper(BaseScraper):
    """Base for sitemap-driven company scrapers.

    Subclasses set `company` and, when the sitemap covers more than one
    section, `category_map` — a path-prefix → `Category` lookup checked
    against each URL's path:

        class AnthropicScraper(SitemapScraper):
            company = "anthropic"
            category_map = {
                "/news/": "news",
                "/research/": "research",
                "/engineering/": "engineering",
            }

    A URL matching no prefix falls back to the source's own `category`.
    """

    company: str
    category_map: ClassVar[dict[str, Category]] = {}

    def __init__(self, sources: Sequence[Source] | None = None) -> None:
        super().__init__()
        if sources is None:
            sources = [s for s in sources_for(self.company) if s.kind == "sitemap"]
        self.sitemap_sources: tuple[Source, ...] = tuple(sources)
        if not self.sitemap_sources:
            raise ValueError(f"{self.company}: no sitemap sources declared in the registry")
        # Populated by discover_urls(), read by scrape_page() and pre_check().
        self._sitemap_meta: dict[str, SitemapMeta] = {}

    # -- index-page metadata (consumed by main._scraper_infos) ----------------

    @property
    def sources(self) -> list[str]:
        return [f"{s.url} — {s.label}" for s in self.sitemap_sources]

    @property
    def exclusions(self) -> list[str]:
        out: list[str] = []
        for s in self.sitemap_sources:
            out.extend(f'{s.label}: URLs containing "{p}"' for p in s.exclude_patterns)
        return out

    # -- discovery ------------------------------------------------------------

    def discover_urls(self) -> list[tuple[str, Category]]:
        self._sitemap_meta.clear()
        cutoff = (datetime.now(UTC) - timedelta(days=settings.max_article_age_days)).date()

        urls: list[tuple[str, Category]] = []
        for source in self.sitemap_sources:
            for entry in self._discover_source(source, cutoff):
                category = self.categorize(source, entry)
                self._sitemap_meta[entry.url] = SitemapMeta(entry=entry, source=source, category=category)
                urls.append((entry.url, category))
        return urls

    def categorize(self, source: Source, entry: SitemapEntry) -> Category:
        """Category for one URL: longest matching `category_map` prefix, else the source's."""
        path = urlparse(entry.url).path
        for prefix, category in self.category_map.items():
            if path.startswith(prefix):
                return category
        return source.category

    def _discover_source(self, source: Source, cutoff: date) -> list[SitemapEntry]:
        try:
            xml = self._fetch_with_httpx(source.url)
        except Exception:
            logger.warning("%s: failed to fetch sitemap %s", self.company, source.url, exc_info=True)
            return []

        entries = parse_sitemap(xml)
        if not entries:
            logger.warning(
                "%s: %s returned 0 URLs — sitemap structure may have changed (%s)",
                self.company, source.key, source.url,
            )
            return []

        kept = [e for e in entries if self._keep(source, e, cutoff)]
        logger.info(
            "%s: %s kept %d of %d entry(s) (cutoff %s)",
            self.company, source.key, len(kept), len(entries), cutoff,
        )
        return kept

    def _keep(self, source: Source, entry: SitemapEntry, cutoff: date) -> bool:
        """Apply the registry filters. Every drop is logged at DEBUG (CLAUDE.md).

        The age check here is a cheap pre-filter only: `lastmod >= published`, so
        dropping on it is safe, but keeping on it proves nothing. `run()` re-checks
        the extracted date once the page has been fetched.
        """
        if entry.lastmod is not None and entry.lastmod < cutoff:
            logger.debug(
                "%s: %s older than cutoff %s (%s) %s",
                self.company, source.key, cutoff, entry.lastmod, entry.url,
            )
            return False

        haystack = entry.url.lower()

        if source.include_patterns and not any(p.lower() in haystack for p in source.include_patterns):
            logger.debug(
                "%s: %s matched no include pattern %r — dropping %s",
                self.company, source.key, source.include_patterns, entry.url,
            )
            return False

        for pattern in source.exclude_patterns:
            if pattern.lower() in haystack:
                logger.debug(
                    "%s: %s matched exclude pattern %r — dropping %s",
                    self.company, source.key, pattern, entry.url,
                )
                return False

        return True

    # -- scraping -------------------------------------------------------------

    def scrape_page(self, url: str, category: Category) -> ScrapedPage | None:
        meta = self._sitemap_meta.get(url)
        if meta is None:
            logger.warning("%s: no sitemap metadata for %s — discover_urls() did not yield it", self.company, url)
            return None

        entry, source = meta.entry, meta.source

        html = self._fetch_page(url)
        soup = BeautifulSoup(html, "lxml")
        text = self.extract_text(html)
        title = self.extract_title(soup)

        published_date = self.extract_date(soup)
        if published_date is None and entry.lastmod is not None:
            # See the module docstring: lastmod tracks CMS edits, so this dates a
            # 2023 post to whenever it was last migrated. Warn — a site that stops
            # rendering its dates should be visible, not silently mis-dated.
            logger.warning(
                "%s: no date on the page, falling back to sitemap lastmod (%s) %s",
                self.company, entry.lastmod, url,
            )
            published_date = entry.lastmod.isoformat()

        if len(text) < _THIN_CONTENT_CHARS:
            logger.warning("%s: thin content (%d chars) at %s", self.company, len(text), url)

        return ScrapedPage(
            url=url,
            company=self.company,
            source=source.key,
            category=category,
            title=title,
            raw_text=text,
            published_date=published_date,
        )

    # -- deduplication --------------------------------------------------------

    def pre_check(self, url: str, existing: ArticleRecord) -> bool | None:
        """Use the sitemap's own lastmod instead of a HEAD request per known URL.

        Returns False (skip) when lastmod is no newer than our last scrape.
        Otherwise falls through to a full scrape + content-hash compare — a
        bumped lastmod alone is not proof the body changed.
        """
        meta = self._sitemap_meta.get(url)
        if meta is None or meta.entry.lastmod is None:
            return super().pre_check(url, existing)

        try:
            last_scraped = datetime.fromisoformat(existing.last_scraped_at)
        except ValueError:
            return None
        if last_scraped.tzinfo is None:
            last_scraped = last_scraped.replace(tzinfo=UTC)

        if meta.entry.lastmod <= last_scraped.date():
            return False
        return None
