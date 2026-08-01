import hashlib
from datetime import UTC, datetime
from typing import Literal, get_args
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel, model_validator

# The canonical category set. `research` and `engineering` come from Anthropic and
# DeepMind, `news` from OpenAI and the press feeds. `release_notes` is kept even
# though no AI source emits it today — see docs/plan.md Step 2e.
Category = Literal[
    "blog", "research", "engineering", "news", "press_release", "product", "release_notes"
]
CATEGORIES: tuple[str, ...] = get_args(Category)


def normalize_url(url: str) -> str:
    """Normalize a URL for consistent deduplication across minor variations."""
    parsed = urlparse(url)
    scheme = "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parsed.query)))
    return urlunparse((scheme, netloc, path, "", query, ""))


class ScrapedPage(BaseModel):
    """Transient in-memory representation of a freshly scraped page."""

    url: str
    company: Literal["cribl", "ocient", "xsiam"]
    category: Category
    title: str
    raw_text: str
    scraped_at: datetime = None  # type: ignore[assignment]
    content_hash: str = ""
    http_last_modified: str | None = None
    published_date: str | None = None  # ISO 8601, extracted from page HTML

    @model_validator(mode="after")
    def _set_defaults(self) -> "ScrapedPage":
        if self.scraped_at is None:
            self.scraped_at = datetime.now(UTC)
        if not self.content_hash:
            self.content_hash = hashlib.sha256(self.raw_text.encode()).hexdigest()
        return self


class ArticleRecord(BaseModel):
    """Operational record stored in SQLite — tracks scrape history and deduplication state."""

    url: str
    normalized_url: str
    company: Literal["cribl", "ocient", "xsiam"]
    category: Category
    title: str
    first_scraped_at: str  # ISO 8601
    last_scraped_at: str   # ISO 8601
    content_hash: str
    published_date: str | None = None
    summary: str = ""
    status: Literal["ok", "error", "skipped"] = "ok"

    @classmethod
    def from_scraped_page(
        cls,
        page: ScrapedPage,
        first_scraped_at: str | None = None,
        summary: str = "",
    ) -> "ArticleRecord":
        now = page.scraped_at.isoformat()
        return cls(
            url=page.url,
            normalized_url=normalize_url(page.url),
            company=page.company,
            category=page.category,
            title=page.title,
            first_scraped_at=first_scraped_at or now,
            last_scraped_at=now,
            content_hash=page.content_hash,
            published_date=page.published_date,
            summary=summary,
            status="ok",
        )
