"""FeedScraper — docs/plan.md Step 3.

Fully offline: `respx` mocks every request, and the parser tests are pure.
Shape fixtures (RSS with categories, Atom, content:encoded, malformed) live in
`tests/fixtures/feeds/`; anything that depends on "now" — the age cutoff,
pagination, pre_check — builds its XML inline with relative dates so the suite
does not rot.
"""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
import respx

from digest.config import settings
from digest.scrapers.feed import FeedScraper, _paged_url, parse_feed
from digest.sources import Source
from digest.storage.models import ArticleRecord

FIXTURES = Path(__file__).parent / "fixtures" / "feeds"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _source(**kwargs) -> Source:
    defaults = {
        "key": "test-source",
        "company": "testco",
        "label": "Test Feed",
        "url": "https://example.com/feed/",
        "kind": "rss",
        "category": "news",
    }
    return Source(**{**defaults, **kwargs})


def _scraper(*sources: Source, company: str = "testco") -> FeedScraper:
    cls = type("_FixtureScraper", (FeedScraper,), {"company": company})
    return cls(sources or None)


# --- inline feed builders (relative dates) ---------------------------------


def _item(slug: str, dt: datetime, *, categories: tuple[str, ...] = ("AI",), title: str = "") -> str:
    cats = "".join(f"<category>{c}</category>" for c in categories)
    return (
        f"<item><title>{title or slug}</title>"
        f"<link>https://example.com/{slug}/</link>"
        f"<pubDate>{format_datetime(dt)}</pubDate>{cats}</item>"
    )


def _rss(*items: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        f"<channel><title>Test</title>{''.join(items)}</channel></rss>"
    )


def _days_ago(n: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


@pytest.fixture
def wide_window(monkeypatch: pytest.MonkeyPatch):
    """Disable the age cutoff so shape tests aren't dated by the fixture files."""
    monkeypatch.setattr(settings, "max_article_age_days", 36500)


# ---------------------------------------------------------------------------
# parse_feed — pure, no HTTP
# ---------------------------------------------------------------------------


def test_parses_rss_items() -> None:
    entries = parse_feed(_fixture("techcrunch_rss.xml"))
    assert [e.url for e in entries] == [
        "https://techcrunch.com/2026/07/31/openai-agent-framework/",
        "https://techcrunch.com/2026/07/31/india-is-starting-to-pay-for-apps/",
        "https://techcrunch.com/2026/07/30/anthropic-enterprise/",
    ]
    first = entries[0]
    assert first.title == "OpenAI ships a new agent framework"
    assert first.categories == ("AI", "OpenAI")
    assert first.published_date == "2026-07-31"
    assert first.published is not None and first.published.tzinfo is not None


def test_rss_description_is_not_treated_as_content() -> None:
    """A truncated excerpt must never stand in for the article body."""
    entries = parse_feed(_fixture("techcrunch_rss.xml"))
    assert all(e.content_html == "" for e in entries)


def test_parses_content_encoded() -> None:
    with_content, without = parse_feed(_fixture("ars_rss.xml"))
    assert "retrieval accuracy across context windows" in with_content.content_html
    assert without.content_html == ""


def test_parses_atom_entries() -> None:
    entries = parse_feed(_fixture("atom_feed.xml"))
    assert len(entries) == 2

    first = entries[0]
    # rel="alternate" wins over the rel="related" link that precedes it.
    assert first.url == "https://www.theverge.com/ai/2026/07/31/agent-eval-suite"
    assert first.categories == ("AI", "Artificial Intelligence")  # Atom uses term=
    assert first.published_date == "2026-07-31"  # <published> preferred over <updated>
    assert "long-horizon planning" in first.content_html

    # A bare <link href> with no rel still yields the URL; <updated> is the fallback date.
    assert entries[1].url == "https://www.theverge.com/ai/2026/07/29/updated-only"
    assert entries[1].published_date == "2026-07-29"


def test_malformed_xml_returns_empty_without_raising() -> None:
    assert parse_feed(_fixture("malformed.xml")) == []
    assert parse_feed("") == []
    assert parse_feed("not xml at all") == []


def test_feed_with_no_items_returns_empty() -> None:
    assert parse_feed(_rss()) == []


def test_entry_without_a_link_is_dropped() -> None:
    xml = _rss("<item><title>No link</title><pubDate>Fri, 31 Jul 2026 00:00:00 +0000</pubDate></item>")
    assert parse_feed(xml) == []


def test_undated_entry_parses_with_none_date() -> None:
    xml = _rss("<item><title>T</title><link>https://example.com/a/</link></item>")
    entry = parse_feed(xml)[0]
    assert entry.published is None
    assert entry.published_date is None


# ---------------------------------------------------------------------------
# discover_urls — filtering
# ---------------------------------------------------------------------------


@respx.mock
def test_discover_urls_yields_url_category_pairs(wide_window) -> None:
    src = _source(url="https://techcrunch.com/feed/", category="news")
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_fixture("techcrunch_rss.xml")))

    urls = _scraper(src).discover_urls()

    assert len(urls) == 3
    assert all(category == "news" for _, category in urls)


@respx.mock
def test_include_categories_drops_untagged_and_off_topic(wide_window) -> None:
    src = _source(url="https://azure.example/feed/", include_categories=("AI + machine learning", "Azure AI Foundry"))
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_fixture("azure_rss.xml")))

    urls = [u for u, _ in _scraper(src).discover_urls()]

    assert urls == ["https://azure.microsoft.com/en-us/blog/announcing-new-models-in-foundry/"]


@respx.mock
def test_include_categories_match_is_case_insensitive(wide_window) -> None:
    src = _source(url="https://azure.example/feed/", include_categories=("ai + MACHINE learning",))
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_fixture("azure_rss.xml")))

    assert len(_scraper(src).discover_urls()) == 1


@respx.mock
def test_empty_include_categories_keeps_everything(wide_window) -> None:
    src = _source(url="https://azure.example/feed/")
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_fixture("azure_rss.xml")))

    assert len(_scraper(src).discover_urls()) == 3


@respx.mock
def test_exclude_patterns_match_url_or_title(wide_window) -> None:
    src = _source(url="https://techcrunch.com/feed/", exclude_patterns=("pay-for-apps", "Anthropic"))
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_fixture("techcrunch_rss.xml")))

    urls = [u for u, _ in _scraper(src).discover_urls()]

    assert urls == ["https://techcrunch.com/2026/07/31/openai-agent-framework/"]


@respx.mock
def test_age_cutoff_drops_old_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_article_age_days", 2)
    src = _source()
    respx.get(src.url).mock(
        return_value=httpx.Response(200, text=_rss(_item("fresh", _days_ago(1)), _item("stale", _days_ago(10))))
    )

    urls = [u for u, _ in _scraper(src).discover_urls()]

    assert urls == ["https://example.com/fresh/"]


@respx.mock
def test_daily_cap_keeps_the_newest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_article_age_days", 30)
    src = _source(daily_cap=2)
    respx.get(src.url).mock(
        return_value=httpx.Response(
            200,
            # Deliberately out of order in the feed — the cap must sort before slicing.
            text=_rss(_item("older", _days_ago(3)), _item("newest", _days_ago(1)), _item("middle", _days_ago(2))),
        )
    )

    urls = [u for u, _ in _scraper(src).discover_urls()]

    assert urls == ["https://example.com/newest/", "https://example.com/middle/"]


@respx.mock
def test_duplicate_urls_across_sources_are_yielded_once(wide_window) -> None:
    a = _source(key="a", url="https://example.com/a/feed/")
    b = _source(key="b", url="https://example.com/b/feed/", category="blog")
    xml = _rss(_item("shared", _days_ago(1)))
    respx.get(a.url).mock(return_value=httpx.Response(200, text=xml))
    respx.get(b.url).mock(return_value=httpx.Response(200, text=xml))

    urls = _scraper(a, b).discover_urls()

    assert urls == [("https://example.com/shared/", "news")]  # first source wins


@respx.mock
def test_feed_fetch_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch, wide_window) -> None:
    monkeypatch.setattr("digest.scrapers.base.time.sleep", lambda _: None)
    src = _source()
    respx.get(src.url).mock(return_value=httpx.Response(500))

    assert _scraper(src).discover_urls() == []


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@respx.mock
def test_no_pagination_when_paginate_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_article_age_days", 30)
    src = _source()  # paginate defaults to False
    route = respx.get(src.url).mock(return_value=httpx.Response(200, text=_rss(_item("a", _days_ago(1)))))

    _scraper(src).discover_urls()

    assert route.call_count == 1


@respx.mock
def test_pagination_walks_until_the_cutoff_is_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_article_age_days", 5)
    src = _source(paginate=True)
    page1 = respx.get(src.url, params__eq={}).mock(
        return_value=httpx.Response(200, text=_rss(_item("p1a", _days_ago(1)), _item("p1b", _days_ago(2))))
    )
    page2 = respx.get(src.url, params={"paged": "2"}).mock(
        return_value=httpx.Response(200, text=_rss(_item("p2a", _days_ago(4)), _item("p2b", _days_ago(9))))
    )

    urls = [u for u, _ in _scraper(src).discover_urls()]

    assert page1.call_count == 1
    assert page2.call_count == 1
    # p2b is past the cutoff, so it stops after page 2 and drops that entry.
    assert urls == ["https://example.com/p1a/", "https://example.com/p1b/", "https://example.com/p2a/"]


@respx.mock
def test_pagination_stops_at_feed_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_article_age_days", 3650)
    monkeypatch.setattr(settings, "feed_max_pages", 2)
    src = _source(paginate=True)
    respx.get(src.url, params__eq={}).mock(return_value=httpx.Response(200, text=_rss(_item("p1", _days_ago(1)))))
    page2 = respx.get(src.url, params={"paged": "2"}).mock(
        return_value=httpx.Response(200, text=_rss(_item("p2", _days_ago(2))))
    )

    urls = [u for u, _ in _scraper(src).discover_urls()]

    assert page2.call_count == 1
    assert urls == ["https://example.com/p1/", "https://example.com/p2/"]


@respx.mock
def test_pagination_stops_when_a_feed_ignores_paged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some feeds re-serve page 1 for any ?paged=N — that must not loop to the cap."""
    monkeypatch.setattr(settings, "max_article_age_days", 3650)
    src = _source(paginate=True)
    xml = _rss(_item("same", _days_ago(1)))
    route = respx.get(src.url).mock(return_value=httpx.Response(200, text=xml))

    urls = [u for u, _ in _scraper(src).discover_urls()]

    assert route.call_count == 2  # page 1, then page 2 detected as a repeat
    assert urls == ["https://example.com/same/"]


def test_paged_url_preserves_existing_query() -> None:
    assert _paged_url("https://x.com/feed/", 1) == "https://x.com/feed/"
    assert _paged_url("https://x.com/feed/", 3) == "https://x.com/feed/?paged=3"
    assert _paged_url("https://x.com/feed/?tag=ai", 2) == "https://x.com/feed/?tag=ai&paged=2"


# ---------------------------------------------------------------------------
# scrape_page
# ---------------------------------------------------------------------------

_ARTICLE_HTML = """
<html><head>
  <meta property="og:title" content="Title From The Page">
  <meta property="article:published_time" content="2020-01-01T00:00:00Z">
</head><body><article><p>The article body as served by the site, long enough to clear the thin-content
warning threshold so the log stays quiet during tests. It repeats itself a bit on purpose.</p></article></body></html>
"""


@respx.mock
def test_content_in_feed_skips_the_article_fetch(wide_window) -> None:
    src = _source(url="https://arstechnica.com/ai/feed/", content_in_feed=True)
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_fixture("ars_rss.xml")))
    article = respx.get("https://arstechnica.com/ai/2026/07/long-context-retrieval-failures/")

    scraper = _scraper(src)
    urls = scraper.discover_urls()
    page = scraper.scrape_page(*urls[0])

    assert not article.called
    assert page is not None
    assert "retrieval accuracy across context windows" in page.raw_text
    assert page.title == "A study of long-context retrieval failures"
    assert page.published_date == "2026-07-30"
    assert page.source == src.key
    assert page.company == "testco"


@respx.mock
def test_content_in_feed_falls_back_to_fetching_when_encoded_is_absent(wide_window) -> None:
    src = _source(url="https://arstechnica.com/ai/feed/", content_in_feed=True)
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_fixture("ars_rss.xml")))
    article = respx.get("https://arstechnica.com/ai/2026/07/no-encoded-content/").mock(
        return_value=httpx.Response(200, text=_ARTICLE_HTML)
    )

    scraper = _scraper(src)
    urls = scraper.discover_urls()
    page = scraper.scrape_page(*urls[1])

    assert article.called
    assert page is not None
    assert "The article body as served by the site" in page.raw_text


@respx.mock
def test_feed_date_and_title_win_over_page_html(wide_window) -> None:
    src = _source(url="https://techcrunch.com/feed/")
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_fixture("techcrunch_rss.xml")))
    respx.get("https://techcrunch.com/2026/07/31/openai-agent-framework/").mock(
        return_value=httpx.Response(200, text=_ARTICLE_HTML)
    )

    scraper = _scraper(src)
    urls = scraper.discover_urls()
    page = scraper.scrape_page(*urls[0])

    assert page is not None
    assert page.title == "OpenAI ships a new agent framework"  # not "Title From The Page"
    assert page.published_date == "2026-07-31"  # not the page's 2020-01-01


@respx.mock
def test_page_html_supplies_the_date_when_the_feed_has_none(wide_window) -> None:
    src = _source()
    respx.get(src.url).mock(
        return_value=httpx.Response(200, text=_rss("<item><link>https://example.com/a/</link></item>"))
    )
    respx.get("https://example.com/a/").mock(return_value=httpx.Response(200, text=_ARTICLE_HTML))

    scraper = _scraper(src)
    page = scraper.scrape_page(*scraper.discover_urls()[0])

    assert page is not None
    assert page.published_date == "2020-01-01"
    assert page.title == "Title From The Page"


def test_scrape_page_without_discovery_returns_none() -> None:
    assert _scraper(_source()).scrape_page("https://example.com/never-seen/", "news") is None


# ---------------------------------------------------------------------------
# pre_check — the feed date replaces a HEAD request
# ---------------------------------------------------------------------------


def _record(url: str, last_scraped: datetime) -> ArticleRecord:
    return ArticleRecord(
        url=url,
        normalized_url=url,
        company="testco",
        source="test-source",
        category="news",
        title="T",
        first_scraped_at=last_scraped.isoformat(),
        last_scraped_at=last_scraped.isoformat(),
        content_hash="abc",
    )


@respx.mock
def test_pre_check_skips_when_the_feed_date_is_not_newer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_article_age_days", 30)
    src = _source()
    published = _days_ago(3)
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_rss(_item("a", published))))
    head = respx.head("https://example.com/a/")

    scraper = _scraper(src)
    scraper.discover_urls()

    assert scraper.pre_check("https://example.com/a/", _record("https://example.com/a/", _days_ago(1))) is False
    assert not head.called  # the whole point: no HEAD per known URL


@respx.mock
def test_pre_check_is_inconclusive_when_the_feed_date_is_newer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bumped pubDate isn't proof the body changed — fall through to the hash compare."""
    monkeypatch.setattr(settings, "max_article_age_days", 30)
    src = _source()
    respx.get(src.url).mock(return_value=httpx.Response(200, text=_rss(_item("a", _days_ago(1)))))

    scraper = _scraper(src)
    scraper.discover_urls()

    assert scraper.pre_check("https://example.com/a/", _record("https://example.com/a/", _days_ago(5))) is None


@respx.mock
def test_pre_check_falls_back_to_head_without_feed_metadata() -> None:
    src = _source()
    head = respx.head("https://example.com/unknown/").mock(
        return_value=httpx.Response(200, headers={"last-modified": format_datetime(_days_ago(1))})
    )

    url = "https://example.com/unknown/"
    result = _scraper(src).pre_check(url, _record(url, _days_ago(5)))

    assert head.called
    assert result is True


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_sources_come_from_the_registry() -> None:
    scraper = _scraper(company="google")
    assert [s.key for s in scraper.feed_sources] == ["google-ai-blog", "google-deepmind"]


def test_sitemap_sources_are_not_feed_sources() -> None:
    """Anthropic is sitemap-only, so FeedScraper must refuse it rather than no-op."""
    with pytest.raises(ValueError, match="no feed sources"):
        _scraper(company="anthropic")


def test_index_metadata_describes_sources_and_filters() -> None:
    scraper = _scraper(company="techcrunch")
    assert any("techcrunch.com" in s for s in scraper.sources)
    assert any("at most 8 item(s)" in e for e in scraper.exclusions)
