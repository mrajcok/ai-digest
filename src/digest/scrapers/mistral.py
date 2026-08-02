"""Mistral — single blog feed, ~0.1 articles/day; see docs/plan.md Step 5."""

from digest.scrapers.feed import FeedScraper


class MistralScraper(FeedScraper):
    company = "mistral"
