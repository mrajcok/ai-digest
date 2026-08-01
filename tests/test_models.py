"""Model shape — docs/plan.md Step 2b."""

import pytest
from pydantic import ValidationError

from digest.sources import SOURCES
from digest.storage.models import ArticleRecord, ScrapedPage, normalize_url


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


def test_source_survives_the_page_to_record_hop() -> None:
    record = ArticleRecord.from_scraped_page(_page())
    assert record.source == "google-deepmind"
    assert record.company == "google"


def test_two_sources_one_company() -> None:
    """The case `source` exists for: same company, different feed."""
    a = ArticleRecord.from_scraped_page(_page(source="google-deepmind"))
    b = ArticleRecord.from_scraped_page(
        _page(url="https://blog.google/technology/ai/x/", source="google-ai-blog", category="blog")
    )
    assert a.company == b.company
    assert a.source != b.source
    assert {a.source, b.source} <= set(SOURCES)


def test_company_is_no_longer_a_literal() -> None:
    """Any registry key is accepted — the old Literal listed cribl/ocient/xsiam."""
    for key in ("anthropic", "openai", "google", "microsoft", "aws", "mistral"):
        assert ScrapedPage(**{**_page().model_dump(), "company": key}).company == key


def test_unknown_category_still_rejected() -> None:
    with pytest.raises(ValidationError):
        _page(category="not-a-category")


def test_source_defaults_to_empty() -> None:
    """Optional so a scraper mid-port doesn't fail validation; Step 5 sets it everywhere."""
    assert _page(source="").source == ""


def test_dedup_fields_survive() -> None:
    """normalize_url and content_hash belong to dedup, not the dropped retrieval code."""
    page = _page()
    record = ArticleRecord.from_scraped_page(page)
    assert record.normalized_url == normalize_url(page.url)
    assert record.content_hash == page.content_hash
    assert "vec_id" not in ArticleRecord.model_fields
