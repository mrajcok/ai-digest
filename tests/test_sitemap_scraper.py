"""SitemapScraper — docs/plan.md Step 4.

Fully offline: `respx` mocks every request. `tests/fixtures/sitemap/` holds a
trimmed real Anthropic sitemap plus two real (trimmed) Anthropic pages, saved
2026-08-01, to verify the date-extraction assumption against real markup
rather than a hand-built one — see the Step 4 note about Cribl's JSON-LD-first
extraction not applying here.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from digest.config import settings
from digest.scrapers.anthropic import AnthropicScraper
from digest.scrapers.sitemap import SitemapScraper, parse_sitemap
from digest.sources import Source
from digest.storage.models import ArticleRecord

FIXTURES = Path(__file__).parent / "fixtures" / "sitemap"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _source(**kwargs) -> Source:
    defaults = {
        "key": "test-sitemap",
        "company": "testco",
        "label": "Test Sitemap",
        "url": "https://example.com/sitemap.xml",
        "kind": "sitemap",
        "category": "news",
    }
    return Source(**{**defaults, **kwargs})


def _scraper(*sources: Source, company: str = "testco") -> SitemapScraper:
    cls = type("_FixtureScraper", (SitemapScraper,), {"company": company})
    return cls(sources or None)


def _record(url: str, last_scraped: datetime) -> ArticleRecord:
    return ArticleRecord(
        url=url,
        normalized_url=url,
        company="testco",
        source="test-sitemap",
        category="news",
        title="T",
        first_scraped_at=last_scraped.isoformat(),
        last_scraped_at=last_scraped.isoformat(),
        content_hash="abc",
    )


def _days_ago(n: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


@pytest.fixture
def wide_window(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "max_article_age_days", 36500)


def _sitemap(*urls: str) -> str:
    entries = "".join(
        f"<url><loc>{u}</loc><lastmod>{_days_ago(1).isoformat().replace('+00:00', 'Z')}</lastmod></url>"
        for u in urls
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'


# ---------------------------------------------------------------------------
# parse_sitemap — pure, no HTTP
# ---------------------------------------------------------------------------


def test_parses_urlset() -> None:
    entries = parse_sitemap(_fixture("anthropic_sitemap.xml"))
    assert [e.url for e in entries] == [
        "https://www.anthropic.com/legal/acst-disclosure",
        "https://www.anthropic.com/careers",
        "https://www.anthropic.com/news/100k-context-windows",
        "https://www.anthropic.com/research/a-general-language-assistant-as-a-laboratory-for-alignment",
        "https://www.anthropic.com/engineering/building-effective-agents",
        "https://www.anthropic.com/news/ancient-news-item",
    ]
    first = entries[0]
    assert first.lastmod is not None and first.lastmod.isoformat() == "2026-05-12"


def test_malformed_xml_returns_empty_without_raising() -> None:
    assert parse_sitemap("not xml at all") == []
    assert parse_sitemap("") == []


def test_urlset_with_no_urls_returns_empty() -> None:
    assert parse_sitemap('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>') == []


def test_url_without_loc_is_dropped() -> None:
    xml = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><lastmod>2026-01-01</lastmod></url></urlset>'
    assert parse_sitemap(xml) == []


def test_url_without_lastmod_parses_with_none() -> None:
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://example.com/a/</loc></url></urlset>"
    )
    entry = parse_sitemap(xml)[0]
    assert entry.lastmod is None


# ---------------------------------------------------------------------------
# discover_urls — filtering and category mapping
# ---------------------------------------------------------------------------


def _url_entry(url: str, when: datetime) -> str:
    lastmod = when.isoformat().replace("+00:00", "Z")
    return f"<url><loc>{url}</loc><lastmod>{lastmod}</lastmod></url>"


@respx.mock
def test_age_cutoff_drops_old_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_article_age_days", 2)
    src = _source()
    fresh = _url_entry("https://example.com/fresh/", _days_ago(1))
    stale = _url_entry("https://example.com/stale/", _days_ago(10))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{fresh}{stale}</urlset>'
    )
    respx.get(src.url).mock(return_value=httpx.Response(200, text=xml))

    urls = [u for u, _ in _scraper(src).discover_urls()]

    assert urls == ["https://example.com/fresh/"]


@respx.mock
def test_exclude_patterns_match_the_url(wide_window) -> None:
    src = _source(exclude_patterns=("/legal/", "/careers"))
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_fixture("anthropic_sitemap.xml")))

    urls = [u for u, _ in _scraper(src).discover_urls()]

    assert urls == [
        "https://www.anthropic.com/news/100k-context-windows",
        "https://www.anthropic.com/research/a-general-language-assistant-as-a-laboratory-for-alignment",
        "https://www.anthropic.com/engineering/building-effective-agents",
        "https://www.anthropic.com/news/ancient-news-item",
    ]


@respx.mock
def test_category_map_assigns_by_path_prefix(wide_window) -> None:
    respx.get("https://www.anthropic.com/sitemap.xml").mock(
        return_value=httpx.Response(200, text=_fixture("anthropic_sitemap.xml"))
    )

    urls = dict(AnthropicScraper().discover_urls())

    assert urls["https://www.anthropic.com/news/100k-context-windows"] == "news"
    assert (
        urls["https://www.anthropic.com/research/a-general-language-assistant-as-a-laboratory-for-alignment"]
        == "research"
    )
    assert urls["https://www.anthropic.com/engineering/building-effective-agents"] == "engineering"
    # No path-prefix match falls back to the source's own category.
    assert urls["https://www.anthropic.com/careers"] == "news"


@respx.mock
def test_sitemap_fetch_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch, wide_window) -> None:
    src = _source()
    respx.get(src.url).mock(return_value=httpx.Response(500))

    assert _scraper(src).discover_urls() == []


# ---------------------------------------------------------------------------
# scrape_page — real (trimmed) Anthropic markup
# ---------------------------------------------------------------------------


@respx.mock
def test_scrape_page_uses_lastmod_when_page_has_no_date(wide_window) -> None:
    """Anthropic's date is client-rendered — no JSON-LD, meta, or <time> tag on the
    saved page — so the sitemap's lastmod is the only date source."""
    respx.get("https://www.anthropic.com/sitemap.xml").mock(
        return_value=httpx.Response(200, text=_fixture("anthropic_sitemap.xml"))
    )
    respx.get("https://www.anthropic.com/news/100k-context-windows").mock(
        return_value=httpx.Response(200, text=_fixture("anthropic_news.html"))
    )

    scraper = AnthropicScraper()
    urls = scraper.discover_urls()
    page = scraper.scrape_page(*next(u for u in urls if u[0].endswith("100k-context-windows")))

    assert page is not None
    assert page.title == "Introducing 100K Context Windows"
    assert page.published_date == "2025-05-02"  # from lastmod, not the page
    assert page.category == "news"
    assert len(page.raw_text) > 200


@respx.mock
def test_scrape_page_research_article(wide_window) -> None:
    respx.get("https://www.anthropic.com/sitemap.xml").mock(
        return_value=httpx.Response(200, text=_fixture("anthropic_sitemap.xml"))
    )
    url = "https://www.anthropic.com/research/a-general-language-assistant-as-a-laboratory-for-alignment"
    respx.get(url).mock(return_value=httpx.Response(200, text=_fixture("anthropic_research.html")))

    scraper = AnthropicScraper()
    scraper.discover_urls()
    page = scraper.scrape_page(url, "research")

    assert page is not None
    assert page.title == "A General Language Assistant as a Laboratory for Alignment"
    assert page.published_date == "2024-12-19"
    assert page.category == "research"


def test_scrape_page_without_discovery_returns_none() -> None:
    assert _scraper(_source()).scrape_page("https://example.com/never-seen/", "news") is None


# ---------------------------------------------------------------------------
# pre_check — the sitemap's lastmod replaces a HEAD request
# ---------------------------------------------------------------------------


@respx.mock
def test_pre_check_skips_when_lastmod_is_not_newer(wide_window) -> None:
    src = _source()
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_sitemap("https://example.com/a/")))
    head = respx.head("https://example.com/a/")

    scraper = _scraper(src)
    scraper.discover_urls()

    assert scraper.pre_check("https://example.com/a/", _record("https://example.com/a/", _days_ago(0))) is False
    assert not head.called


@respx.mock
def test_pre_check_is_inconclusive_when_lastmod_is_newer(wide_window) -> None:
    src = _source()
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_sitemap("https://example.com/a/")))

    scraper = _scraper(src)
    scraper.discover_urls()

    assert scraper.pre_check("https://example.com/a/", _record("https://example.com/a/", _days_ago(5))) is None


@respx.mock
def test_pre_check_falls_back_to_head_without_sitemap_metadata() -> None:
    from email.utils import format_datetime

    src = _source()
    url = "https://example.com/unknown/"
    head = respx.head(url).mock(
        return_value=httpx.Response(200, headers={"last-modified": format_datetime(_days_ago(1))})
    )

    result = _scraper(src).pre_check(url, _record(url, _days_ago(5)))

    assert head.called
    assert result is True


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_sources_come_from_the_registry() -> None:
    scraper = AnthropicScraper()
    assert [s.key for s in scraper.sitemap_sources] == ["anthropic-sitemap"]


def test_feed_sources_are_not_sitemap_sources() -> None:
    with pytest.raises(ValueError, match="no sitemap sources"):
        _scraper(company="google")


def test_index_metadata_describes_sources_and_exclusions() -> None:
    scraper = AnthropicScraper()
    assert any("anthropic.com/sitemap.xml" in s for s in scraper.sources)
    assert any('"/legal/"' in e for e in scraper.exclusions)
