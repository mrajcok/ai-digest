"""SQLite schema — docs/plan.md Step 2c."""

import pytest

from digest.storage.db import ArticleDB
from digest.storage.models import ArticleRecord, ScrapedPage, normalize_url


@pytest.fixture
def db():
    with ArticleDB(":memory:") as database:
        yield database


def _page(**overrides) -> ScrapedPage:
    kwargs = {
        "url": "https://deepmind.google/blog/some-post/",
        "company": "google",
        "source": "google-deepmind",
        "category": "research",
        "title": "Some Post",
        "raw_text": "Body text.",
    }
    return ScrapedPage(**{**kwargs, **overrides})


def test_source_round_trips(db: ArticleDB) -> None:
    db.upsert(ArticleRecord.from_scraped_page(_page(), summary="s"))
    record = db.get_by_url("https://deepmind.google/blog/some-post/")
    assert record is not None
    assert record.source == "google-deepmind"


def test_schema_has_source_and_no_vec_id(db: ArticleDB) -> None:
    columns = [r["name"] for r in db._conn.execute("PRAGMA table_info(scraped_articles)")]
    assert "source" in columns
    assert "vec_id" not in columns


def test_two_sources_one_company_are_separate_rows(db: ArticleDB) -> None:
    db.upsert(ArticleRecord.from_scraped_page(_page(), summary="s"))
    db.upsert(
        ArticleRecord.from_scraped_page(
            _page(
                url="https://blog.google/technology/ai/other-post/",
                source="google-ai-blog",
                category="blog",
            ),
            summary="s",
        )
    )
    records = db.get_all(company="google")
    assert len(records) == 2
    assert {r.source for r in records} == {"google-deepmind", "google-ai-blog"}


def test_upsert_updates_source(db: ArticleDB) -> None:
    """A source key correction on re-scrape overwrites, unlike first_scraped_at."""
    original = ArticleRecord.from_scraped_page(_page(source="wrong-key"), summary="s")
    db.upsert(original)
    updated = ArticleRecord.from_scraped_page(_page(), summary="s2")
    db.upsert(updated)

    record = db.get_by_url(_page().url)
    assert record is not None
    assert record.source == "google-deepmind"
    assert record.first_scraped_at == original.first_scraped_at


def test_text_cache_still_keyed_by_normalized_url(db: ArticleDB) -> None:
    page = _page()
    db.upsert(ArticleRecord.from_scraped_page(page, summary="s"))
    db.save_text(normalize_url(page.url), page.raw_text)
    assert db.get_text(normalize_url(page.url)) == "Body text."
    assert db.articles_with_text("google")[0].source == "google-deepmind"
