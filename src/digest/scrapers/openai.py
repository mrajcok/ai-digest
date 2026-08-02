"""OpenAI — single feed, no filtering; see docs/plan.md Step 5 and docs/sources.md.

The feed's real `<category>` tags are usable for `categorize()` even though
`include_categories` is empty (the whole feed is AI by definition).
"""

from digest.scrapers.feed import FeedEntry, FeedScraper
from digest.sources import Source
from digest.storage.models import Category

_RESEARCH_TAGS = {"research", "publication"}
_ENGINEERING_TAGS = {"engineering", "api"}
_PRODUCT_TAGS = {"product", "release"}


class OpenAIScraper(FeedScraper):
    company = "openai"

    def categorize(self, source: Source, entry: FeedEntry) -> Category:
        tags = {c.lower() for c in entry.categories}
        if tags & _RESEARCH_TAGS:
            return "research"
        if tags & _ENGINEERING_TAGS:
            return "engineering"
        if tags & _PRODUCT_TAGS:
            return "product"
        return source.category
