"""ListingScraper — the claude.com/blog source.

Fully offline. `tests/fixtures/listing/claude_blog.html` is real (trimmed)
claude.com markup saved 2026-08-02, cut down to eight article cards covering
**both** layouts the page renders: the featured row, where the date sits three
levels above the link, and the grid, where it sits two. That difference is the
whole reason `_card_date` walks up instead of reading a fixed selector.
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from digest.config import settings
from digest.scrapers.anthropic import AnthropicScraper
from digest.scrapers.listing import ListingScraper, parse_listing
from digest.sources import Source
from digest.storage.models import ArticleRecord

FIXTURES = Path(__file__).parent / "fixtures" / "listing"
BASE = "https://claude.com/blog"


def _fixture(name: str = "claude_blog.html") -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _source(**kwargs) -> Source:
    defaults = {
        "key": "test-listing",
        "company": "testco",
        "label": "Test Listing",
        "url": BASE,
        "kind": "listing",
        "category": "blog",
    }
    return Source(**{**defaults, **kwargs})


def _scraper(*sources: Source, company: str = "testco") -> ListingScraper:
    cls = type("_FixtureListingScraper", (ListingScraper,), {"company": company})
    return cls(sources or None)


@pytest.fixture
def wide_window(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "max_article_age_days", 36500)


# ---------------------------------------------------------------------------
# parse_listing — pure, no HTTP
# ---------------------------------------------------------------------------


def test_parses_every_card_with_its_own_date() -> None:
    entries = parse_listing(_fixture(), BASE)

    assert len(entries) == 8
    assert all(e.lastmod is not None for e in entries)
    assert entries[0].url == "https://claude.com/blog/bringing-mcp-2026-07-28-to-claude"
    assert entries[0].lastmod.isoformat() == "2026-07-28"
    assert entries[-1].url == "https://claude.com/blog/new-in-claude-managed-agents"
    assert entries[-1].lastmod.isoformat() == "2026-05-19"


def test_listing_dates_are_publication_dates() -> None:
    """Unlike a sitemap lastmod, so scrape_page may trust them without warning."""
    assert all(e.date_is_publication for e in parse_listing(_fixture(), BASE))


def test_ignores_links_outside_the_listing_path() -> None:
    urls = {e.url for e in parse_listing(_fixture(), BASE)}
    assert not any(u.endswith(("/", "/pricing")) for u in urls)
    assert all("/blog/" in u for u in urls)


def test_relative_hrefs_are_resolved_against_the_listing_url() -> None:
    html = "<a href='/blog/one'>One</a><div>Mar 8, 2023</div>"
    assert parse_listing(html, BASE)[0].url == "https://claude.com/blog/one"


def test_each_card_in_a_grid_keeps_its_own_date() -> None:
    """The guard that matters: a shared grid ancestor must not leak one card's
    date onto the next."""
    html = """
      <div class="grid">
        <div class="card"><a href="/blog/a">A</a><span>Mar 8, 2023</span></div>
        <div class="card"><a href="/blog/b">B</a><span>Apr 9, 2024</span></div>
      </div>
    """
    entries = parse_listing(html, BASE)
    assert [(e.url.rsplit("/", 1)[1], e.lastmod.isoformat()) for e in entries] == [
        ("a", "2023-03-08"),
        ("b", "2024-04-09"),
    ]


def test_a_card_linking_its_post_several_times_still_finds_the_date() -> None:
    """Image, title and tag all link the same post — that is still one card, so
    counting distinct URLs rather than <a> tags is what makes the date reachable."""
    html = """
      <div class="card">
        <div><a href="/blog/a">image</a></div>
        <div><a href="/blog/a">title</a><a href="#">share</a></div>
        <span>Jul 16, 2026</span>
      </div>
    """
    assert parse_listing(html, BASE)[0].lastmod.isoformat() == "2026-07-16"


def test_undated_link_is_kept_rather_than_dropped() -> None:
    entries = parse_listing("<div><a href='/blog/mystery'>M</a></div>", BASE)
    assert len(entries) == 1
    assert entries[0].lastmod is None


def test_duplicate_links_are_collapsed_first_occurrence_wins() -> None:
    html = """
      <div><a href="/blog/a">first</a><span>Mar 8, 2023</span></div>
      <div><a href="/blog/a">again</a><span>Apr 9, 2024</span></div>
    """
    entries = parse_listing(html, BASE)
    assert len(entries) == 1
    assert entries[0].lastmod.isoformat() == "2023-03-08"


def test_empty_page_yields_no_entries() -> None:
    assert parse_listing("<html><body><p>nothing</p></body></html>", BASE) == []


# ---------------------------------------------------------------------------
# ListingScraper — discovery
# ---------------------------------------------------------------------------


@respx.mock
def test_discovery_is_a_single_request(wide_window) -> None:
    """The whole point: one fetch of the listing, not one fetch per article."""
    src = _source()
    route = respx.get(BASE).mock(return_value=httpx.Response(200, text=_fixture()))

    urls = _scraper(src).discover_urls()

    assert len(urls) == 8
    assert route.call_count == 1


@respx.mock
def test_age_cutoff_applies_before_any_article_is_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_article_age_days", 1)
    src = _source()
    respx.get(BASE).mock(return_value=httpx.Response(200, text=_fixture()))

    # Every fixture card predates the window, and no article route is mocked —
    # respx would raise on any request beyond the listing itself.
    assert _scraper(src).discover_urls() == []


@respx.mock
def test_zero_urls_logs_a_warning(wide_window, caplog) -> None:
    src = _source()
    respx.get(BASE).mock(return_value=httpx.Response(200, text="<html><body>redesigned</body></html>"))

    with caplog.at_level(logging.WARNING):
        assert _scraper(src).discover_urls() == []

    assert "returned 0 URLs" in caplog.text


@respx.mock
def test_include_patterns_still_apply(wide_window) -> None:
    src = _source(include_patterns=("claude-managed-agents",))
    respx.get(BASE).mock(return_value=httpx.Response(200, text=_fixture()))

    urls = [u for u, _ in _scraper(src).discover_urls()]

    assert urls == [
        "https://claude.com/blog/claude-managed-agents-updates",
        "https://claude.com/blog/new-in-claude-managed-agents",
    ]


@respx.mock
def test_fetch_failure_does_not_raise(wide_window) -> None:
    src = _source()
    respx.get(BASE).mock(return_value=httpx.Response(500))

    assert _scraper(src).discover_urls() == []


# ---------------------------------------------------------------------------
# Dedup — a known post costs zero requests
# ---------------------------------------------------------------------------


@respx.mock
def test_known_post_is_skipped_without_a_request(wide_window) -> None:
    """Steady state: the listing is fetched once, and posts already summarized
    are skipped on their publication date alone — no HEAD, no GET."""
    src = _source()
    respx.get(BASE).mock(return_value=httpx.Response(200, text=_fixture()))
    head = respx.head("https://claude.com/blog/artifacts-in-claude-code")

    scraper = _scraper(src)
    scraper.discover_urls()
    url = "https://claude.com/blog/artifacts-in-claude-code"
    scraped_at = datetime.now(UTC) - timedelta(days=1)
    record = ArticleRecord(
        url=url,
        normalized_url=url,
        company="testco",
        source="test-listing",
        category="blog",
        title="T",
        first_scraped_at=scraped_at.isoformat(),
        last_scraped_at=scraped_at.isoformat(),
        content_hash="abc",
    )

    assert scraper.pre_check(url, record) is False
    assert not head.called


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_anthropic_scraper_claims_both_sources() -> None:
    scraper = AnthropicScraper()
    try:
        kinds = {s.kind for s in scraper.sitemap_sources}
        assert kinds == {"sitemap", "listing"}
        assert any("claude.com/blog" in s for s in scraper.sources)
    finally:
        scraper.close()


@respx.mock
def test_blog_urls_are_categorized_as_blog(wide_window) -> None:
    respx.get("https://www.anthropic.com/sitemap.xml").mock(
        return_value=httpx.Response(200, text="<urlset></urlset>")
    )
    respx.get(BASE).mock(return_value=httpx.Response(200, text=_fixture()))

    scraper = AnthropicScraper()
    try:
        categories = {c for _, c in scraper.discover_urls()}
    finally:
        scraper.close()

    assert categories == {"blog"}
