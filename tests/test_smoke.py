"""
Placeholder suite for the bootstrap step.

The real suite is ported in Step 9 of docs/plan.md, once the source registry
(Step 2a) and the `source` column (Step 2c) exist. Until then these assert only
that the copied package imports and that the schema round-trips, so `make test`
has something to collect.
"""
import pytest

from digest.storage.db import ArticleDB
from digest.storage.models import ArticleRecord, ScrapedPage, normalize_url


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Provide minimal env vars so config.Settings validates without a real .env file."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "test-user/test-repo")


@pytest.fixture
def db():
    """In-memory ArticleDB — isolated per test, no disk I/O."""
    with ArticleDB(":memory:") as database:
        yield database


def test_package_imports() -> None:
    """Every ported module imports — nothing still references the dropped retrieval code."""
    import digest.main
    import digest.notifier
    import digest.publisher.github_pages
    import digest.scrapers.base
    import digest.summarizer

    assert digest.main.main is not None
    assert digest.summarizer.Summarizer is not None
    assert digest.scrapers.base.BaseScraper is not None
    assert digest.notifier.post_discord_summary is not None
    assert digest.publisher.github_pages.GitHubPagesPublisher is not None


def test_retrieval_symbols_are_gone() -> None:
    """ProductUpdate, vec_id_for and the vec_id field are not ported — see docs/plan.md Step 2b."""
    import digest.storage.models as models

    assert not hasattr(models, "ProductUpdate")
    assert not hasattr(models, "vec_id_for")
    assert "vec_id" not in ArticleRecord.model_fields


def test_db_round_trip(db: ArticleDB) -> None:
    """A ScrapedPage survives upsert → get_by_url with the vec_id column removed."""
    page = ScrapedPage(
        url="https://www.anthropic.com/news/test-post/",
        company="cribl",  # TODO(Step 2a): becomes a registry key once the Literal is replaced
        category="blog",
        title="Test Post",
        raw_text="A test article body.",
    )
    db.upsert(ArticleRecord.from_scraped_page(page, summary="A summary."))
    db.save_text(normalize_url(page.url), page.raw_text)

    record = db.get_by_url(page.url)
    assert record is not None
    assert record.title == "Test Post"
    assert record.summary == "A summary."
    assert record.content_hash == page.content_hash
    assert db.get_text(record.normalized_url) == "A test article body."
