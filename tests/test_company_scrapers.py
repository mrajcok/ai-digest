"""Per-company scrapers — docs/plan.md Step 5.

Each module in `digest/scrapers/` is a thin `FeedScraper` subclass that pulls
its sources from the `sources.py` registry; `FeedScraper` itself is already
fully covered generically in `test_feed_scraper.py`. These tests check the
registry wiring and the two per-company overrides: OpenAI's `categorize()` and
the press feeds' `exclude_patterns`.
"""

from pathlib import Path

import httpx
import pytest
import respx

from digest.config import settings
from digest.scrapers.aws import AwsScraper
from digest.scrapers.google import GoogleScraper
from digest.scrapers.microsoft import MicrosoftScraper
from digest.scrapers.mistral import MistralScraper
from digest.scrapers.openai import OpenAIScraper
from digest.scrapers.press import ArsTechnicaScraper, TechCrunchScraper
from digest.sources import SOURCES

FIXTURES = Path(__file__).parent / "fixtures" / "feeds"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def wide_window(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "max_article_age_days", 36500)


def _openai_rss(*items: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0">'
        f"<channel>{''.join(items)}</channel></rss>"
    )


def _openai_item(slug: str, category: str) -> str:
    return (
        f"<item><title>{slug}</title><link>https://openai.com/{slug}/</link>"
        f"<pubDate>Fri, 31 Jul 2026 00:00:00 +0000</pubDate>"
        f"<category>{category}</category></item>"
    )


# ---------------------------------------------------------------------------
# Registry wiring — every company key maps to sources it actually owns
# ---------------------------------------------------------------------------


def test_openai_sources_come_from_the_registry() -> None:
    assert [s.key for s in OpenAIScraper().feed_sources] == ["openai-news"]


def test_google_sources_come_from_the_registry() -> None:
    assert [s.key for s in GoogleScraper().feed_sources] == ["google-ai-blog", "google-deepmind"]


def test_microsoft_sources_come_from_the_registry() -> None:
    assert [s.key for s in MicrosoftScraper().feed_sources] == ["microsoft-source-ai", "microsoft-azure-blog"]


def test_aws_sources_come_from_the_registry() -> None:
    assert [s.key for s in AwsScraper().feed_sources] == ["aws-ml-blog"]


def test_mistral_sources_come_from_the_registry() -> None:
    assert [s.key for s in MistralScraper().feed_sources] == ["mistral-blog"]


def test_techcrunch_sources_come_from_the_registry() -> None:
    assert [s.key for s in TechCrunchScraper().feed_sources] == ["techcrunch-ai"]


def test_arstechnica_sources_come_from_the_registry() -> None:
    assert [s.key for s in ArsTechnicaScraper().feed_sources] == ["arstechnica-ai"]


# ---------------------------------------------------------------------------
# OpenAI — categorize() maps real feed tags onto our categories
# ---------------------------------------------------------------------------


@respx.mock
def test_openai_categorizes_by_feed_tag(wide_window) -> None:
    xml = _openai_rss(
        _openai_item("research-post", "Research"),
        _openai_item("publication-post", "Publication"),
        _openai_item("engineering-post", "Engineering"),
        _openai_item("api-post", "API"),
        _openai_item("product-post", "Product"),
        _openai_item("release-post", "Release"),
        _openai_item("company-post", "Company"),
    )
    respx.get(SOURCES["openai-news"].url).mock(return_value=httpx.Response(200, text=xml))

    urls = dict(OpenAIScraper().discover_urls())

    assert urls["https://openai.com/research-post/"] == "research"
    assert urls["https://openai.com/publication-post/"] == "research"
    assert urls["https://openai.com/engineering-post/"] == "engineering"
    assert urls["https://openai.com/api-post/"] == "engineering"
    assert urls["https://openai.com/product-post/"] == "product"
    assert urls["https://openai.com/release-post/"] == "product"
    # No mapped tag falls back to the source's own category.
    assert urls["https://openai.com/company-post/"] == "news"


# ---------------------------------------------------------------------------
# Google — DeepMind and the AI blog keep their own source category (no tags)
# ---------------------------------------------------------------------------


@respx.mock
def test_google_categorizes_by_source_not_tag(wide_window) -> None:
    ai_blog_xml = _openai_rss(_openai_item("blog-post", "AI"))
    deepmind_xml = (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<item><title>dm-post</title><link>https://deepmind.google/dm-post/</link>"
        "<pubDate>Fri, 31 Jul 2026 00:00:00 +0000</pubDate></item>"
        "</channel></rss>"
    )
    respx.get(SOURCES["google-ai-blog"].url).mock(return_value=httpx.Response(200, text=ai_blog_xml))
    respx.get(SOURCES["google-deepmind"].url).mock(return_value=httpx.Response(200, text=deepmind_xml))

    urls = dict(GoogleScraper().discover_urls())

    assert urls["https://openai.com/blog-post/"] == "blog"
    assert urls["https://deepmind.google/dm-post/"] == "research"


# ---------------------------------------------------------------------------
# Microsoft — Azure blog is filtered against the live category allowlist
# ---------------------------------------------------------------------------


@respx.mock
def test_azure_allowlist_drops_non_ai_categories(wide_window) -> None:
    source_ai_xml = _openai_rss()
    respx.get(SOURCES["microsoft-source-ai"].url).mock(return_value=httpx.Response(200, text=source_ai_xml))
    respx.get(SOURCES["microsoft-azure-blog"].url).mock(
        return_value=httpx.Response(200, text=_fixture("azure_rss.xml"))
    )

    urls = [u for u, _ in MicrosoftScraper().discover_urls()]

    assert urls == ["https://azure.microsoft.com/en-us/blog/announcing-new-models-in-foundry/"]


# ---------------------------------------------------------------------------
# Press — known off-topic items dropped by exclude_patterns, real examples
# from the 2026-08-01 source probe (docs/sources.md)
# ---------------------------------------------------------------------------


@respx.mock
def test_techcrunch_drops_the_india_apps_item(wide_window) -> None:
    respx.get(SOURCES["techcrunch-ai"].url).mock(
        return_value=httpx.Response(200, text=_fixture("techcrunch_rss.xml"))
    )

    urls = [u for u, _ in TechCrunchScraper().discover_urls()]

    assert urls == [
        "https://techcrunch.com/2026/07/31/openai-agent-framework/",
        "https://techcrunch.com/2026/07/30/anthropic-enterprise/",
    ]


@respx.mock
def test_arstechnica_drops_dmca_items(wide_window) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>'
        "<item><title>A DMCA takedown hits an AI training dataset</title>"
        "<link>https://arstechnica.com/ai/2026/07/dmca-takedown/</link>"
        "<pubDate>Thu, 30 Jul 2026 18:30:00 +0000</pubDate><category>AI</category>"
        "<content:encoded><![CDATA[<p>Off-topic legal piece.</p>]]></content:encoded></item>"
        "</channel></rss>"
    )
    respx.get(SOURCES["arstechnica-ai"].url).mock(return_value=httpx.Response(200, text=xml))

    urls = [u for u, _ in ArsTechnicaScraper().discover_urls()]

    assert urls == []
