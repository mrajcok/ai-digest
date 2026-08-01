"""RSS/Atom feed scraper — docs/plan.md Step 3.

Eight of the ten sources in docs/sources.md are feeds; only Anthropic needs a
sitemap (Step 4). This module does all the feed work once so a company scraper
is a thin subclass that only declares `company` and lets `sources.py` supply the
feeds.

Parsing is stdlib `xml.etree.ElementTree` — `feedparser` is deliberately not a
dependency (see CLAUDE.md). The parser is namespace- and dialect-agnostic: it
walks for `<item>` (RSS) or `<entry>` (Atom) anywhere in the tree and reads each
child by local name, so RSS 2.0, RSS 1.0/RDF and Atom all land in the same
`FeedEntry`.

Ordering of work matters for cost: filtering (`include_categories`,
`exclude_patterns`, the age cutoff, `daily_cap`) all happens in
`discover_urls()`, before a single article page is fetched.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from digest.config import settings
from digest.scrapers.base import BaseScraper, Category
from digest.sources import Source, sources_for
from digest.storage.models import ArticleRecord, ScrapedPage

__all__ = ["FeedEntry", "FeedScraper", "parse_feed"]

logger = logging.getLogger(__name__)

# Feed kinds this scraper handles; a "sitemap" source belongs to SitemapScraper.
_FEED_KINDS = ("rss", "atom")

# Below this, the page is almost certainly a paywall/consent interstitial rather
# than an article. Warn only — the summarizer still gets a shot at it.
_THIN_CONTENT_CHARS = 200

# Sorts undated entries last, so a `daily_cap` keeps the ones we can date.
_UNDATED = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class FeedEntry:
    """One `<item>`/`<entry>`, as it appears in the feed — no filtering applied."""

    url: str
    title: str
    published: datetime | None          # tz-aware, UTC-normalized
    categories: tuple[str, ...] = ()
    content_html: str = ""              # content:encoded / Atom <content>; "" when absent

    @property
    def published_date(self) -> str | None:
        """`YYYY-MM-DD`, the form `ScrapedPage.published_date` wants."""
        return self.published.date().isoformat() if self.published else None


@dataclass(frozen=True)
class FeedMeta:
    """What `discover_urls()` learned about a URL, for `scrape_page()` to reuse."""

    entry: FeedEntry
    source: Source
    category: Category


# ----------------------------------------------------------------------------
# Parsing — pure functions, no HTTP, so they unit-test directly
# ----------------------------------------------------------------------------


def _localname(tag: str) -> str:
    """`{http://www.w3.org/2005/Atom}entry` → `entry`."""
    return tag.rpartition("}")[2] if "}" in tag else tag


def _text_of(el: ET.Element) -> str:
    """Full text of an element, including any nested markup (CDATA-wrapped HTML)."""
    return "".join(el.itertext()).strip()


def _parse_date(raw: str) -> datetime | None:
    """RFC 822 (`pubDate`) first, then ISO 8601 (Atom `published`/`updated`)."""
    raw = raw.strip()
    if not raw:
        return None
    dt: datetime | None = None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _entry_link(rss_link: str, atom_links: dict[str, str]) -> str:
    """RSS `<link>text</link>`, else the Atom `rel="alternate"` href, else any href."""
    if rss_link:
        return rss_link
    if "alternate" in atom_links:
        return atom_links["alternate"]
    return next(iter(atom_links.values()), "")


def _parse_entry(el: ET.Element) -> FeedEntry | None:
    """Build a FeedEntry from one `<item>`/`<entry>`. None when it has no link."""
    rss_link = ""
    atom_links: dict[str, str] = {}
    title = ""
    published_raw = ""
    updated_raw = ""
    categories: list[str] = []
    content_html = ""

    for child in el:
        name = _localname(child.tag)
        if name == "link":
            href = child.get("href")
            if href:
                atom_links.setdefault(child.get("rel") or "alternate", href.strip())
            elif child.text and not rss_link:
                rss_link = child.text.strip()
        elif name == "title" and not title:
            title = _text_of(child)
        elif name in ("pubDate", "published") and not published_raw:
            published_raw = _text_of(child)
        elif name in ("updated", "date", "modified") and not updated_raw:
            updated_raw = _text_of(child)
        elif name == "category":
            # RSS puts the label in the text, Atom in a `term` attribute.
            term = child.get("term") or _text_of(child)
            if term:
                categories.append(term.strip())
        elif name in ("encoded", "content") and not content_html:
            # content:encoded (RSS) or Atom <content>. `description`/`summary` are
            # deliberately not used: on TechCrunch they are truncated excerpts, and
            # treating one as the article body would silently summarize a teaser.
            content_html = _text_of(child)

    url = _entry_link(rss_link, atom_links)
    if not url:
        return None

    return FeedEntry(
        url=url,
        title=title,
        published=_parse_date(published_raw) or _parse_date(updated_raw),
        categories=tuple(categories),
        content_html=content_html,
    )


def parse_feed(xml: str) -> list[FeedEntry]:
    """Parse RSS or Atom into entries, in feed order. Malformed XML yields []."""
    try:
        root = ET.fromstring(xml.strip())
    except ET.ParseError as exc:
        logger.warning("feed XML parse error: %s", exc)
        return []

    entries: list[FeedEntry] = []
    for el in root.iter():
        if _localname(el.tag) in ("item", "entry"):
            entry = _parse_entry(el)
            if entry is not None:
                entries.append(entry)
    return entries


# ----------------------------------------------------------------------------
# Scraper
# ----------------------------------------------------------------------------


class FeedScraper(BaseScraper):
    """Base for every RSS/Atom company scraper.

    Subclasses set `company` and nothing else unless the site is unusual:

        class AwsScraper(FeedScraper):
            company = "aws"
    """

    company: str

    def __init__(self, sources: Sequence[Source] | None = None) -> None:
        super().__init__()
        if sources is None:
            sources = [s for s in sources_for(self.company) if s.kind in _FEED_KINDS]
        self.feed_sources: tuple[Source, ...] = tuple(sources)
        if not self.feed_sources:
            raise ValueError(f"{self.company}: no feed sources declared in the registry")
        # Populated by discover_urls(), read by scrape_page() and pre_check().
        self._feed_meta: dict[str, FeedMeta] = {}

    # -- index-page metadata (consumed by main._scraper_infos) ----------------

    @property
    def sources(self) -> list[str]:
        return [f"{s.url} — {s.label}" for s in self.feed_sources]

    @property
    def exclusions(self) -> list[str]:
        out: list[str] = []
        for s in self.feed_sources:
            if s.include_categories:
                out.append(f"{s.label}: only items tagged {', '.join(s.include_categories)}")
            out.extend(f'{s.label}: URLs/titles containing "{p}"' for p in s.exclude_patterns)
            if s.daily_cap is not None:
                out.append(f"{s.label}: at most {s.daily_cap} item(s) per run, newest first")
        return out

    # -- discovery ------------------------------------------------------------

    def discover_urls(self) -> list[tuple[str, Category]]:
        self._feed_meta.clear()
        cutoff = (datetime.now(UTC) - timedelta(days=settings.max_article_age_days)).date()

        urls: list[tuple[str, Category]] = []
        for source in self.feed_sources:
            for entry in self._discover_source(source, cutoff):
                if entry.url in self._feed_meta:
                    logger.debug("%s: duplicate URL across sources, keeping first: %s", self.company, entry.url)
                    continue
                category = self.categorize(source, entry)
                self._feed_meta[entry.url] = FeedMeta(entry=entry, source=source, category=category)
                urls.append((entry.url, category))
        return urls

    def categorize(self, source: Source, entry: FeedEntry) -> Category:
        """Category for one entry. Defaults to the source's; override per company."""
        return source.category

    def _discover_source(self, source: Source, cutoff: date) -> list[FeedEntry]:
        entries = self._fetch_entries(source, cutoff)
        if not entries:
            logger.warning(
                "%s: %s returned 0 entries — feed structure may have changed (%s)",
                self.company, source.key, source.url,
            )
            return []

        kept = [e for e in entries if self._keep(source, e, cutoff)]
        # Newest first, so `daily_cap` drops the oldest rather than an arbitrary slice.
        kept.sort(key=lambda e: e.published or _UNDATED, reverse=True)

        if source.daily_cap is not None and len(kept) > source.daily_cap:
            for dropped in kept[source.daily_cap:]:
                logger.debug("%s: %s daily_cap reached, dropping %s", self.company, source.key, dropped.url)
            kept = kept[: source.daily_cap]

        logger.info(
            "%s: %s kept %d of %d entry(s) (cutoff %s)",
            self.company, source.key, len(kept), len(entries), cutoff,
        )
        return kept

    def _fetch_entries(self, source: Source, cutoff: date) -> list[FeedEntry]:
        """Fetch page 1, then `?paged=N` while the cutoff window isn't yet covered.

        Pagination only matters for backfill (`--since`): the press feeds hold ~20
        items, which is ~31 hours of TechCrunch. A day-to-day run stops after one
        page because page 1 already reaches past the cutoff.
        """
        entries: list[FeedEntry] = []
        seen: set[str] = set()

        for page in range(1, settings.feed_max_pages + 1):
            url = _paged_url(source.url, page)
            try:
                xml = self._fetch_with_httpx(url)
            except Exception:
                logger.warning("%s: failed to fetch feed %s", self.company, url, exc_info=True)
                break

            page_entries = parse_feed(xml)
            fresh = [e for e in page_entries if e.url not in seen]
            for entry in fresh:
                seen.add(entry.url)
            entries.extend(fresh)

            if not source.paginate or not page_entries:
                break
            if not fresh:
                # The feed ignored ?paged and re-served page 1 — stop, or we loop.
                logger.debug("%s: %s page %d repeated page 1, stopping", self.company, source.key, page)
                break
            oldest = min((e.published for e in page_entries if e.published), default=None)
            if oldest is None or oldest.date() < cutoff:
                break
        else:
            logger.warning(
                "%s: %s hit feed_max_pages=%d without reaching cutoff %s",
                self.company, source.key, settings.feed_max_pages, cutoff,
            )

        return entries

    def _keep(self, source: Source, entry: FeedEntry, cutoff: date) -> bool:
        """Apply the registry filters. Every drop is logged at DEBUG (CLAUDE.md)."""
        if entry.published is not None and entry.published.date() < cutoff:
            logger.debug(
                "%s: %s older than cutoff %s (%s) %s",
                self.company, source.key, cutoff, entry.published_date, entry.url,
            )
            return False

        if source.include_categories:
            allowed = {c.lower() for c in source.include_categories}
            if not {c.lower() for c in entry.categories} & allowed:
                logger.debug(
                    "%s: %s no allowed category in %s — dropping %s",
                    self.company, source.key, entry.categories or "()", entry.url,
                )
                return False

        haystack = f"{entry.url} {entry.title}".lower()
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
        meta = self._feed_meta.get(url)
        if meta is None:
            logger.warning("%s: no feed metadata for %s — discover_urls() did not yield it", self.company, url)
            return None

        entry, source = meta.entry, meta.source

        if source.content_in_feed and entry.content_html:
            # Ars Technica ships full content:encoded — one less request per article.
            title = entry.title
            text = self.extract_text(entry.content_html)
            published_date = entry.published_date
        else:
            if source.content_in_feed:
                logger.debug("%s: %s has no content:encoded, fetching %s", self.company, source.key, url)
            html = self._fetch_page(url)
            soup = BeautifulSoup(html, "lxml")
            text = self.extract_text(html)
            title = entry.title or self.extract_title(soup)
            # The feed date is more reliable than HTML date extraction, so it wins.
            published_date = entry.published_date or self.extract_date(soup)

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
        """Use the feed's own date instead of a HEAD request per known URL.

        Returns False (skip) when the feed says the item is no newer than our last
        scrape. Otherwise falls through to a full scrape + content-hash compare —
        a bumped `pubDate` alone is not proof the body changed.
        """
        meta = self._feed_meta.get(url)
        if meta is None or meta.entry.published is None:
            return super().pre_check(url, existing)

        try:
            last_scraped = datetime.fromisoformat(existing.last_scraped_at)
        except ValueError:
            return None
        if last_scraped.tzinfo is None:
            last_scraped = last_scraped.replace(tzinfo=UTC)

        if meta.entry.published <= last_scraped:
            return False
        return None


def _paged_url(url: str, page: int) -> str:
    """Append `?paged=N` (WordPress pagination), preserving any existing query."""
    if page <= 1:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query["paged"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))
