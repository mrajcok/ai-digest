"""The category Literal is declared once and reused everywhere — docs/plan.md Step 2e."""

from digest.scrapers.base import Category as ScraperCategory
from digest.storage.models import CATEGORIES, ArticleRecord, Category, ScrapedPage


def test_category_set() -> None:
    assert set(CATEGORIES) == {
        "blog", "research", "engineering", "news", "press_release", "product", "release_notes",
    }


def test_scrapers_reuse_the_canonical_category() -> None:
    """scrapers.base re-exports the models Category rather than declaring its own."""
    assert ScraperCategory is Category


def test_models_accept_every_category() -> None:
    for category in CATEGORIES:
        page = ScrapedPage(
            url=f"https://example.com/{category}",
            company="cribl",  # TODO(Step 2b): becomes a registry key
            category=category,  # type: ignore[arg-type]
            title="T",
            raw_text="body",
        )
        assert ArticleRecord.from_scraped_page(page).category == category
