"""Google — AI blog + DeepMind, both topic-scoped; see docs/plan.md Step 5.

No filtering and no `categorize()` override needed: DeepMind carries no
`<category>` elements, so it is categorized by its own source entry
(`category="research"` in the registry), same as the AI blog (`"blog"`).
"""

from digest.scrapers.feed import FeedScraper


class GoogleScraper(FeedScraper):
    company = "google"
