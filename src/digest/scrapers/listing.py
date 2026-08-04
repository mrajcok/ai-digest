"""Listing-page scraper — for index pages that carry a date next to each link.

`docs/sources.md` prefers sitemaps and feeds over listing pages, because listing
pages are usually JS-rendered and break silently. `claude.com/blog` is the
exception that earns one: it has no feed, and its sitemap entries carry **no**
`lastmod` at all, so a sitemap-driven run would have to fetch all ~199 posts
every day just to read their dates. The listing page is server-rendered and
pairs each of the ~25 newest posts with its publication date, which turns
discovery into a single request and lets the age cutoff run *before* any article
is fetched.

The date on a listing page is a real publication date, not a `lastmod`, so
entries are marked `date_is_publication=True` — see `scrapers/sitemap.py`.
"""

import logging
from datetime import date
from typing import ClassVar
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from digest.scrapers.base import _VISIBLE_DATE_RE, _normalize_date
from digest.scrapers.sitemap import SitemapEntry, SitemapScraper
from digest.sources import Source

__all__ = ["ListingScraper", "parse_listing"]

logger = logging.getLogger(__name__)

# How far up from a link to look for its date. claude.com renders two card
# layouts — the date sits 2 levels up in the grid and 3 in the featured row.
_MAX_CARD_DEPTH = 4


def _article_urls(node: Tag, base_url: str, base_path: str) -> set[str]:
    """Distinct article links inside `node` — the test for "still one card"."""
    urls = set()
    for anchor in node.find_all("a", href=True):
        url = urljoin(base_url, str(anchor["href"]))
        if urlparse(url).path.rstrip("/").startswith(f"{base_path}/"):
            urls.add(url)
    return urls


def _card_date(anchor: Tag, base_url: str, base_path: str) -> str | None:
    """Walk up from a link looking for the date of *its* card.

    Stops widening as soon as an ancestor covers more than one **distinct**
    article URL — that ancestor is the grid, not the card, and its first date
    belongs to some other post. Counting distinct URLs rather than `<a>` tags
    matters: a card links its own post two or three times (image, title, tag).
    Only a text node that is nothing but a date counts, for the same reason as
    `BaseScraper.extract_visible_date`.
    """
    node: Tag | None = anchor
    for _ in range(_MAX_CARD_DEPTH):
        if node is None:
            return None
        for text in node.find_all(string=_VISIBLE_DATE_RE):
            if (normalized := _normalize_date(str(text))) is not None:
                return normalized
        parent = node.parent
        if parent is None or len(_article_urls(parent, base_url, base_path)) > 1:
            return None
        node = parent
    return None


def parse_listing(html: str, base_url: str) -> list[SitemapEntry]:
    """Parse a listing page into entries, in document order, first link wins.

    Links without a resolvable date are kept with `lastmod=None` rather than
    dropped: `_keep()` treats an unknown date as "keep" everywhere else, and a
    layout change that hides dates should surface as articles with no date, not
    as an empty run that looks like the site went away.
    """
    soup = BeautifulSoup(html, "lxml")
    base_path = urlparse(base_url).path.rstrip("/")

    entries: list[SitemapEntry] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        url = urljoin(base_url, str(anchor["href"]))
        path = urlparse(url).path.rstrip("/")
        # Only links *below* the listing page itself — skips nav, the page's own
        # canonical link, and pagination controls.
        if not path.startswith(f"{base_path}/") or url in seen:
            continue
        seen.add(url)
        published = _card_date(anchor, base_url, base_path)
        entries.append(
            SitemapEntry(
                url=url,
                lastmod=None if published is None else date.fromisoformat(published),
                date_is_publication=True,
            )
        )
    return entries


class ListingScraper(SitemapScraper):
    """A `SitemapScraper` that also accepts `kind="listing"` registry sources.

    Everything downstream of discovery — filtering, categorization, dedup — is
    inherited unchanged; only the index document's format differs.
    """

    source_kinds: ClassVar[tuple[str, ...]] = ("sitemap", "listing")

    def parse_index(self, source: Source, body: str) -> list[SitemapEntry]:
        if source.kind == "listing":
            return parse_listing(body, source.url)
        return super().parse_index(source, body)
