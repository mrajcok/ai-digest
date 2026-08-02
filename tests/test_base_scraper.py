"""BaseScraper — dedup edge cases and date extraction.

`_process_url`: a record with an empty summary (e.g. left behind by
`--stage scrape` without a follow-up `--stage summarize`) must always be
retried, never skipped forever by pre_check's freshness check.

`extract_date` / `extract_visible_date`: every date source normalizes to
`YYYY-MM-DD` or is skipped, so a scraped date can be trusted over a sitemap
`lastmod` — see `scrapers/sitemap.py`.
"""

from datetime import UTC, datetime

import pytest
from bs4 import BeautifulSoup

from digest.scrapers.base import BaseScraper
from digest.storage.db import ArticleDB
from digest.storage.models import ArticleRecord, ScrapedPage


@pytest.fixture
def db():
    with ArticleDB(":memory:") as database:
        yield database


class _StubScraper(BaseScraper):
    company = "testco"

    def __init__(self) -> None:
        super().__init__()
        self.scrape_calls: list[str] = []

    def discover_urls(self):
        return []

    def scrape_page(self, url: str, category: str) -> ScrapedPage | None:
        self.scrape_calls.append(url)
        return ScrapedPage(
            url=url, company="testco", source="testco-blog", category=category,
            title="T", raw_text="body",
        )

    def pre_check(self, url: str, existing: ArticleRecord) -> bool | None:
        # Would skip if ever reached — proves the empty-summary check short-circuits first.
        return False


def _record(url: str, *, summary: str) -> ArticleRecord:
    now = datetime.now(UTC).isoformat()
    return ArticleRecord(
        url=url, normalized_url=url, company="testco", source="testco-blog",
        category="news", title="T", first_scraped_at=now, last_scraped_at=now,
        content_hash="abc", summary=summary,
    )


def test_empty_summary_record_is_always_retried(db: ArticleDB) -> None:
    url = "https://example.com/a"
    db.upsert(_record(url, summary=""))

    scraper = _StubScraper()
    page = scraper._process_url(url, "news", db)

    assert page is not None
    assert scraper.scrape_calls == [url]


def test_summarized_record_defers_to_pre_check(db: ArticleDB) -> None:
    url = "https://example.com/b"
    db.upsert(_record(url, summary="a real summary"))

    scraper = _StubScraper()
    page = scraper._process_url(url, "news", db)

    assert page is None
    assert scraper.scrape_calls == []


# ---------------------------------------------------------------------------
# Date extraction — every source normalizes to YYYY-MM-DD or is skipped
# ---------------------------------------------------------------------------


def _soup(body: str) -> BeautifulSoup:
    return BeautifulSoup(f"<html><body>{body}</body></html>", "lxml")


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<script type="application/ld+json">{"datePublished": "2024-12-18T14:16:00Z"}</script>', "2024-12-18"),
        # claude.com emits a human-readable datePublished; truncating it to 10
        # characters used to yield the un-rejectable "Oct 16, 20".
        ('<script type="application/ld+json">{"datePublished": "Oct 16, 2025"}</script>', "2025-10-16"),
        ('<script type="application/ld+json">{"datePublished": "n/a"}</script>', None),
        ('<meta property="article:published_time" content="2026-01-02T09:00:00+00:00"/>', "2026-01-02"),
        ('<time datetime="2026-03-04">whenever</time>', "2026-03-04"),
        ('<time datetime="not a date">whenever</time>', None),
        ("<p>no date here</p>", None),
    ],
)
def test_extract_date_normalizes_or_skips(html: str, expected: str | None) -> None:
    assert BaseScraper.extract_date(_soup(html)) == expected


def test_extract_date_falls_through_an_unparseable_source() -> None:
    soup = _soup(
        '<script type="application/ld+json">{"datePublished": "Sometime in 2024"}</script>'
        '<meta property="article:published_time" content="2024-05-23"/>'
    )
    assert BaseScraper.extract_date(soup) == "2024-05-23"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<div>Mar 8, 2023</div>", "2023-03-08"),
        ("<div>October 16, 2025</div>", "2025-10-16"),
        ("<div>  Dec 1, 2021  </div>", "2021-12-01"),
        # Prose that merely contains a date is not a byline.
        ("<p>; applications close on January 20, 2025.</p>", None),
        ("<script>var d = 'Jan 1, 2026'</script>", None),
        ("<div>Feb 30, 2024</div>", None),
        ("<div>no date</div>", None),
    ],
)
def test_extract_visible_date(html: str, expected: str | None) -> None:
    assert BaseScraper.extract_visible_date(_soup(html)) == expected


def test_extract_visible_date_prefers_the_header_over_later_prose() -> None:
    soup = _soup("<div class='header'>Dec 18, 2024</div><p>applications close on January 20, 2025.</p>")
    assert BaseScraper.extract_visible_date(soup) == "2024-12-18"
