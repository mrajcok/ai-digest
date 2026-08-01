import logging
import logging.handlers

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    openrouter_api_key: str = "dummy"
    openrouter_summarization_model: str = "google/gemma-3-27b-it"
    openrouter_stage_summarization_model: str = ""  # falls back to openrouter_summarization_model

    # Ollama local server (overrides OpenRouter for summarization when set)
    ollama_base_url: str = ""
    ollama_summarization_model: str = ""
    ollama_stage_summarization_model: str = ""  # falls back to ollama_summarization_model

    sqlite_db_path: str = "data/ai_digest.db"

    github_token: str = "dummy"
    github_repo: str = "dummy/dummy"
    github_pages_branch: str = "gh-pages"

    log_level: str = "INFO"
    max_article_age_days: int = 30
    index_page_limit: int = 10  # kept for backward compat; no longer used for index page layout
    index_per_company: int = 3
    company_page_limit: int = 30  # max articles shown on each company page
    max_api_retries: int = 5
    summarizer_content_chars: int = 15000

    # Discord run-completion notification — one-way webhook only, no bot
    discord_notify: bool = True
    discord_notify_method: str = "webhook"  # only "webhook" is supported
    discord_webhook_url: str = ""


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
