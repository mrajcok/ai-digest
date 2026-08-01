"""The source registry — the single source of truth for what this pipeline scrapes.

Sources are data, not code: adding a feed means adding a `Source` entry here, not
writing a new module. `main.py`, `publisher/github_pages.py` and the `--site`
argparse choices all read from `COMPANIES`.

A company owns one or more sources (Google has DeepMind plus the AI blog), which
is why articles carry both `company` and `source` — see docs/plan.md Step 2a and
the measured source list in docs/sources.md.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from digest.storage.models import CATEGORIES, Category

logger = logging.getLogger(__name__)

Kind = Literal["rss", "atom", "sitemap"]
Group = Literal["vendor", "press"]


@dataclass(frozen=True)
class Source:
    """One feed or sitemap."""

    key: str                    # "google-deepmind" — unique across all sources
    company: str                # "google"
    label: str                  # "DeepMind Blog"
    url: str
    kind: Kind
    category: Category                          # default category for items from this source
    content_in_feed: bool = False               # True → skip the article fetch
    paginate: bool = False                      # True → ?paged=N supported for backfill
    include_categories: tuple[str, ...] = ()    # feed <category> allowlist; empty → keep all
    exclude_patterns: tuple[str, ...] = ()      # URL/title substrings to drop
    daily_cap: int | None = None                # max items per run, newest first


@dataclass(frozen=True)
class Company:
    """A grouping of sources — one GitHub Pages section per company."""

    key: str            # "google"
    label: str          # "Google"
    group: Group
    sources: tuple[Source, ...]


# Anthropic publishes no RSS feed (both /rss.xml candidates 404), so it is scraped
# from the sitemap. `lastmod` values are genuine per-page timestamps, so the age
# cutoff filters directly off them. Path prefix → category mapping lives in the
# scraper (Step 4); these are the non-article prefixes to drop outright.
_ANTHROPIC_EXCLUDES = (
    "/legal/", "/careers/", "/events/", "/claude/", "/product/", "/pricing/",
    "/customers/", "/partners/", "/supported-countries", "/contact-sales",
)

# There is no AI-scoped Azure feed (the topic feed 404s), so the main Azure feed
# is filtered on <category>. Values must match the feed text exactly. Taken from
# the live feed on 2026-08-01; Step 5 tunes this against the DEBUG drop log.
_AZURE_AI_CATEGORIES = (
    "AI",
    "AI + machine learning",
    "Azure AI",
    "Azure AI Foundry",
    "Azure OpenAI Service",
    "Microsoft Foundry",
    "Foundry Managed Compute",
    "Open Source models",
    "Machine learning",
    "Copilot",
    "M365 Copilot",
    "Microsoft Copilot Cowork",
    "Anthropic",
    "OpenAI",
)

# Both press feeds are already AI-scoped: every item measured on 2026-08-01
# carried an "AI" tag, so this allowlist is a sanity gate, not a volume control.
# Volume is handled by `daily_cap` (Step 6), off-topic bleed by exclude_patterns.
_PRESS_AI_CATEGORIES = ("AI", "Artificial Intelligence", "AI agents", "LLMs", "generative ai")


COMPANIES: dict[str, Company] = {
    "anthropic": Company(
        key="anthropic",
        label="Anthropic",
        group="vendor",
        sources=(
            Source(
                key="anthropic-sitemap",
                company="anthropic",
                label="Anthropic",
                url="https://www.anthropic.com/sitemap.xml",
                kind="sitemap",
                category="news",
                exclude_patterns=_ANTHROPIC_EXCLUDES,
            ),
        ),
    ),
    "openai": Company(
        key="openai",
        label="OpenAI",
        group="vendor",
        sources=(
            # The feed's 958 <category> elements are all empty — never filter on them.
            Source(
                key="openai-news",
                company="openai",
                label="OpenAI News",
                url="https://openai.com/news/rss.xml",
                kind="rss",
                category="news",
            ),
        ),
    ),
    "google": Company(
        key="google",
        label="Google",
        group="vendor",
        sources=(
            Source(
                key="google-ai-blog",
                company="google",
                label="Google AI Blog",
                url="https://blog.google/technology/ai/rss/",
                kind="rss",
                category="blog",
            ),
            # DeepMind's feed carries no <category> elements — categorize by source.
            Source(
                key="google-deepmind",
                company="google",
                label="DeepMind Blog",
                url="https://deepmind.google/blog/rss.xml",
                kind="rss",
                category="research",
            ),
        ),
    ),
    "microsoft": Company(
        key="microsoft",
        label="Microsoft",
        group="vendor",
        sources=(
            Source(
                key="microsoft-source-ai",
                company="microsoft",
                label="Microsoft Source AI",
                url="https://news.microsoft.com/source/topics/ai/feed/",
                kind="rss",
                category="news",
            ),
            Source(
                key="microsoft-azure-blog",
                company="microsoft",
                label="Azure Blog",
                url="https://azure.microsoft.com/en-us/blog/feed/",
                kind="rss",
                category="blog",
                include_categories=_AZURE_AI_CATEGORIES,
            ),
        ),
    ),
    "aws": Company(
        key="aws",
        label="AWS",
        group="vendor",
        sources=(
            Source(
                key="aws-ml-blog",
                company="aws",
                label="AWS Machine Learning Blog",
                url="https://aws.amazon.com/blogs/machine-learning/feed/",
                kind="rss",
                category="blog",
            ),
        ),
    ),
    "mistral": Company(
        key="mistral",
        label="Mistral AI",
        group="vendor",
        sources=(
            Source(
                key="mistral-blog",
                company="mistral",
                label="Mistral Blog",
                url="https://mistral.ai/rss.xml",
                kind="rss",
                category="blog",
            ),
        ),
    ),
    "techcrunch": Company(
        key="techcrunch",
        label="TechCrunch",
        group="press",
        sources=(
            # ~15 articles/day — more than every vendor source combined, hence the cap.
            Source(
                key="techcrunch-ai",
                company="techcrunch",
                label="TechCrunch AI",
                url="https://techcrunch.com/category/artificial-intelligence/feed/",
                kind="rss",
                category="news",
                paginate=True,
                include_categories=_PRESS_AI_CATEGORIES,
                daily_cap=8,
            ),
        ),
    ),
    "arstechnica": Company(
        key="arstechnica",
        label="Ars Technica",
        group="press",
        sources=(
            # Ships full content:encoded — no second fetch per article.
            Source(
                key="arstechnica-ai",
                company="arstechnica",
                label="Ars Technica AI",
                url="https://arstechnica.com/ai/feed/",
                kind="rss",
                category="news",
                content_in_feed=True,
                paginate=True,
                include_categories=_PRESS_AI_CATEGORIES,
                daily_cap=5,
            ),
        ),
    ),
}


SOURCES: dict[str, Source] = {
    source.key: source for company in COMPANIES.values() for source in company.sources
}


def company_keys() -> list[str]:
    """Company keys in registry order — the order pages are rendered in."""
    return list(COMPANIES)


def companies_in_group(group: Group) -> list[Company]:
    return [c for c in COMPANIES.values() if c.group == group]


def sources_for(company_key: str) -> tuple[Source, ...]:
    return COMPANIES[company_key].sources


def company_label(company_key: str) -> str:
    company = COMPANIES.get(company_key)
    return company.label if company else company_key


def source_label(source_key: str) -> str:
    source = SOURCES.get(source_key)
    return source.label if source else source_key


def _validate() -> None:
    """Fail at import on a malformed registry rather than mis-rendering a run."""
    seen: set[str] = set()
    for key, company in COMPANIES.items():
        if company.key != key:
            raise ValueError(f"company {key!r} declares key {company.key!r}")
        if not company.sources:
            raise ValueError(f"company {key!r} has no sources")
        for source in company.sources:
            if source.key in seen:
                raise ValueError(f"duplicate source key {source.key!r}")
            seen.add(source.key)
            if source.company != key:
                raise ValueError(f"source {source.key!r} claims company {source.company!r}")
            if source.category not in CATEGORIES:
                raise ValueError(f"source {source.key!r} has unknown category {source.category!r}")


_validate()
