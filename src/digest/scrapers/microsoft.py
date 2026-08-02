"""Microsoft — Source AI (topic-scoped) + Azure blog (category-filtered).

The Azure allowlist lives in `sources.py` as `_AZURE_AI_CATEGORIES`; see
docs/plan.md Step 5.
"""

from digest.scrapers.feed import FeedScraper


class MicrosoftScraper(FeedScraper):
    company = "microsoft"
