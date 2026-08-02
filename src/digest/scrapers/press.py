"""TechCrunch AI and Ars Technica AI — press, not vendor; see docs/plan.md Step 5.

Both feeds tag every item `AI` regardless of topic (a re-probe on 2026-08-01
found the category allowlist keeps everything), so off-topic bleed is handled
by `exclude_patterns` in the registry, not `include_categories`. Volume is
handled by `daily_cap`. Ars ships full `content:encoded`; TechCrunch does not.
"""

from digest.scrapers.feed import FeedScraper


class TechCrunchScraper(FeedScraper):
    company = "techcrunch"


class ArsTechnicaScraper(FeedScraper):
    company = "arstechnica"
