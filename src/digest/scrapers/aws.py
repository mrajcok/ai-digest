"""AWS — ML blog only, fully AI-scoped; see docs/plan.md Step 5."""

from digest.scrapers.feed import FeedScraper


class AwsScraper(FeedScraper):
    company = "aws"
