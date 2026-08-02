import logging
import logging.handlers
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # pydantic's default forbids undeclared keys, turning any stray .env
        # entry into a hard startup failure. Trade-off: a misspelled digest
        # setting is now ignored rather than reported, so check the README
        # config table when a setting doesn't seem to take effect.
        extra="ignore",
    )

    # OpenRouter is the only LLM backend — there is no local-model path.
    openrouter_api_key: str = "dummy"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_summarization_model: str = "google/gemma-3-27b-it"
    openrouter_stage_summarization_model: str = ""  # falls back to openrouter_summarization_model

    sqlite_db_path: str = "data/ai_digest.db"

    github_token: str = "dummy"
    github_repo: str = "dummy/dummy"
    github_pages_branch: str = "gh-pages"

    log_level: str = "INFO"
    max_article_age_days: int = 30
    index_page_limit: int = 10  # kept for backward compat; no longer used for index page layout
    index_per_company: int = 3
    index_per_company_press: int = 1  # lower cap for press companies (Step 6)
    company_page_limit: int = 30  # max articles shown on each company page
    max_api_retries: int = 5
    summarizer_content_chars: int = 15000

    # Feed pagination (?paged=N) — only reached during backfill, since a normal run's
    # cutoff is covered by page 1. Caps how far a --since backfill will walk a feed.
    feed_max_pages: int = 10

    # Companies whose sites are blocked by a work firewall: the summary is the only
    # thing the reader will ever see, so it gets a longer, self-contained treatment
    # built from a bigger slice of the article. Values are Company.key from sources.py
    # — a company key covers all of its feeds.
    long_summary_companies: Annotated[list[str], NoDecode] = ["anthropic", "openai", "mistral"]
    summarizer_content_chars_long: int = 30000
    long_summary_target_chars: int = 1000  # target summary length, in output characters

    # Discord run-completion notification — one-way webhook only, no bot
    discord_notify: bool = True
    discord_notify_method: str = "webhook"  # only "webhook" is supported
    discord_webhook_url: str = ""

    @field_validator("long_summary_companies", mode="before")
    @classmethod
    def _split_csv(cls, v: str | list[str]) -> list[str]:
        """Parse `a,b,c` from env.

        NoDecode above turns off pydantic-settings' default JSON decoding for list
        fields, which would reject a bare `LONG_SUMMARY_COMPANIES=anthropic,openai`.
        Every other value in .env.example is a plain scalar, so CSV is the consistent
        choice over requiring JSON.
        """
        return [s.strip().lower() for s in v.split(",") if s.strip()] if isinstance(v, str) else v


settings = Settings()  # type: ignore[call-arg]


def setup_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s:%(lineno)d: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                "logs/agent.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
            ),
        ],
    )
