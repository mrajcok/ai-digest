"""Registry invariants — docs/plan.md Step 2a.

These are the checks that stop a bad registry edit from silently dropping a
company page or double-counting a feed.
"""

import dataclasses

import pytest

from digest.sources import (
    COMPANIES,
    SOURCES,
    Company,
    Source,
    _validate,
    companies_in_group,
    company_keys,
    company_label,
    source_label,
    sources_for,
)
from digest.storage.models import CATEGORIES


def test_source_keys_are_unique() -> None:
    all_keys = [s.key for c in COMPANIES.values() for s in c.sources]
    assert len(all_keys) == len(set(all_keys))
    assert set(all_keys) == set(SOURCES)


def test_every_source_belongs_to_its_company() -> None:
    for key, company in COMPANIES.items():
        assert company.key == key
        for source in company.sources:
            assert source.company == key, f"{source.key} claims {source.company}"
            assert source.company in COMPANIES


def test_every_company_has_at_least_one_source() -> None:
    for company in COMPANIES.values():
        assert company.sources, f"{company.key} has no sources"


def test_every_source_category_is_valid() -> None:
    for source in SOURCES.values():
        assert source.category in CATEGORIES


def test_urls_are_https_and_unique() -> None:
    urls = [s.url for s in SOURCES.values()]
    assert len(urls) == len(set(urls))
    assert all(u.startswith("https://") for u in urls)


def test_expected_companies_present() -> None:
    """The eight companies from docs/sources.md, in render order."""
    assert company_keys() == [
        "anthropic", "openai", "google", "microsoft", "aws", "mistral",
        "techcrunch", "arstechnica",
    ]


def test_google_and_microsoft_have_two_sources() -> None:
    """The multi-source case the `source` field exists for."""
    assert len(sources_for("google")) == 2
    assert {s.key for s in sources_for("google")} == {"google-ai-blog", "google-deepmind"}
    assert len(sources_for("microsoft")) == 2


def test_groups_split_vendor_and_press() -> None:
    assert [c.key for c in companies_in_group("press")] == ["techcrunch", "arstechnica"]
    assert [c.key for c in companies_in_group("vendor")] == [
        "anthropic", "openai", "google", "microsoft", "aws", "mistral",
    ]


def test_press_sources_are_capped() -> None:
    """Uncapped press feeds would swamp the index — docs/plan.md Step 6."""
    for company in companies_in_group("press"):
        for source in company.sources:
            assert source.daily_cap is not None and source.daily_cap > 0
    for company in companies_in_group("vendor"):
        for source in company.sources:
            assert source.daily_cap is None


def test_filtered_sources_declare_an_allowlist() -> None:
    """Azure has no AI-scoped feed, so it must filter on <category>."""
    azure = SOURCES["microsoft-azure-blog"]
    assert "AI + machine learning" in azure.include_categories
    assert SOURCES["aws-ml-blog"].include_categories == ()
    # OpenAI's <category> elements are all empty — filtering on them drops everything.
    assert SOURCES["openai-news"].include_categories == ()


def test_sources_that_take_content_from_the_feed() -> None:
    in_feed = [k for k, s in SOURCES.items() if s.content_in_feed]
    assert in_feed == ["aws-ml-blog", "arstechnica-ai"]


def test_anthropic_is_the_only_sitemap() -> None:
    sitemaps = [k for k, s in SOURCES.items() if s.kind == "sitemap"]
    assert sitemaps == ["anthropic-sitemap"]
    assert SOURCES["anthropic-sitemap"].exclude_patterns


def test_labels_fall_back_to_the_key() -> None:
    assert company_label("google") == "Google"
    assert source_label("google-deepmind") == "DeepMind Blog"
    assert company_label("nope") == "nope"
    assert source_label("nope") == "nope"


def test_registry_is_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        SOURCES["openai-news"].url = "https://example.com"  # type: ignore[misc]


def test_validate_rejects_a_duplicate_source_key(monkeypatch: pytest.MonkeyPatch) -> None:
    dupe = Source(
        key="openai-news", company="mistral", label="Dupe",
        url="https://example.com/rss", kind="rss", category="blog",
    )
    broken = dict(COMPANIES)
    broken["mistral"] = dataclasses.replace(broken["mistral"], sources=(dupe,))
    monkeypatch.setattr("digest.sources.COMPANIES", broken)
    with pytest.raises(ValueError, match="duplicate source key"):
        _validate()


def test_validate_rejects_a_source_under_the_wrong_company(monkeypatch: pytest.MonkeyPatch) -> None:
    stray = Source(
        key="stray", company="openai", label="Stray",
        url="https://example.com/rss", kind="rss", category="blog",
    )
    broken = dict(COMPANIES)
    broken["mistral"] = dataclasses.replace(broken["mistral"], sources=(stray,))
    monkeypatch.setattr("digest.sources.COMPANIES", broken)
    with pytest.raises(ValueError, match="claims company"):
        _validate()


def test_validate_rejects_a_company_without_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = dict(COMPANIES)
    broken["mistral"] = Company(key="mistral", label="Mistral AI", group="vendor", sources=())
    monkeypatch.setattr("digest.sources.COMPANIES", broken)
    with pytest.raises(ValueError, match="no sources"):
        _validate()


def test_publisher_and_cli_read_from_the_registry() -> None:
    from digest.publisher.github_pages import COMPANIES as PUBLISHER_COMPANIES

    assert company_keys() == PUBLISHER_COMPANIES
