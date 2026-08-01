"""Anthropic — sitemap only; see docs/plan.md Step 4 and docs/sources.md."""

from typing import ClassVar

from digest.scrapers.sitemap import SitemapScraper
from digest.storage.models import Category


class AnthropicScraper(SitemapScraper):
    company = "anthropic"
    category_map: ClassVar[dict[str, Category]] = {
        "/news/": "news",
        "/research/": "research",
        "/engineering/": "engineering",
    }
