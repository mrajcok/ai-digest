"""Anthropic — sitemap only; see docs/plan.md Step 4 and docs/sources.md."""

from typing import ClassVar

from bs4 import BeautifulSoup

from digest.scrapers.base import BaseScraper
from digest.scrapers.listing import ListingScraper
from digest.storage.models import Category


class AnthropicScraper(ListingScraper):
    """Two sources: www.anthropic.com's sitemap, and the claude.com blog listing."""

    company = "anthropic"
    category_map: ClassVar[dict[str, Category]] = {
        "/news/": "news",
        "/research/": "research",
        "/engineering/": "engineering",
        "/blog/": "blog",
    }

    @staticmethod
    def extract_date(soup: BeautifulSoup) -> str | None:
        """Anthropic posts carry no JSON-LD, no meta date, and no `<time>` tag — the
        publication date exists only as text in the post header, so fall through to
        `extract_visible_date`. The base implementation still runs first because
        `/news/` posts that redirect to claude.com do emit JSON-LD.
        """
        return BaseScraper.extract_date(soup) or BaseScraper.extract_visible_date(soup)
