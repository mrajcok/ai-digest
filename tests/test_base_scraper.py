"""BaseScraper._process_url dedup edge case — docs/plan.md Step 10 review.

A record with an empty summary (e.g. left behind by `--stage scrape` without a
follow-up `--stage summarize`) must always be retried, never skipped forever
by pre_check's freshness check.
"""

from datetime import UTC, datetime

import pytest

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
