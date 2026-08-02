"""Fixture-based tests against real feed responses — docs/plan.md Step 9.

`tests/fixtures/live_feeds/` holds real HTTP responses (trimmed to 5 items
each) captured from all nine feed sources on 2026-08-01, per the plan's
"save real responses from all ten sources now, before they drift." The tenth
source, Anthropic's sitemap, already has its own real capture in
`tests/fixtures/sitemap/` from Step 4.

These tests run each company scraper's `discover_urls()` against real markup
rather than hand-built XML, catching shape drift the synthetic fixtures in
`test_company_scrapers.py` would not.
"""

from pathlib import Path
from urllib.parse import urlparse

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

FIXTURES = Path(__file__).parent / "fixtures" / "live_feeds"


def _fixture(source_key: str) -> str:
    return (FIXTURES / f"{source_key}.xml").read_text(encoding="utf-8")


@pytest.fixture
def wide_window(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "max_article_age_days", 36500)


def _mock_sources(*keys: str) -> None:
    for key in keys:
        respx.get(SOURCES[key].url).mock(return_value=httpx.Response(200, text=_fixture(key)))


@pytest.mark.parametrize(
    ("scraper_cls", "source_keys"),
    [
        (OpenAIScraper, ("openai-news",)),
        (GoogleScraper, ("google-ai-blog", "google-deepmind")),
        (MicrosoftScraper, ("microsoft-source-ai", "microsoft-azure-blog")),
        (AwsScraper, ("aws-ml-blog",)),
        (MistralScraper, ("mistral-blog",)),
        (TechCrunchScraper, ("techcrunch-ai",)),
        (ArsTechnicaScraper, ("arstechnica-ai",)),
    ],
)
@respx.mock
def test_discover_urls_against_a_real_capture(wide_window, scraper_cls, source_keys) -> None:
    _mock_sources(*source_keys)

    urls = [u for u, _ in scraper_cls().discover_urls()]

    assert urls, "real capture yielded no URLs — feed shape may have drifted"
    for url in urls:
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc
