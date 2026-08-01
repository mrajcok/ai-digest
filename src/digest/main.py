import argparse
import logging
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from digest.config import settings, setup_logging
from digest.notifier import post_discord_summary
from digest.publisher.github_pages import COMPANIES, GitHubPagesPublisher
from digest.storage.db import ArticleDB
from digest.storage.models import ArticleRecord, ScrapedPage, normalize_url
from digest.summarizer import Summarizer

logger = logging.getLogger(__name__)

_DRY_RUN_DIR = Path("data/dry-run")


# ---------------------------------------------------------------------------
# Model availability checks
# ---------------------------------------------------------------------------

def _assert_ollama_available(model: str) -> None:
    """Exit with a clear error if Ollama is not reachable or the model is not available."""
    base_url = settings.ollama_base_url.rstrip("/")
    try:
        resp = httpx.get(f"{base_url}/models", timeout=5.0)
    except Exception as exc:
        logger.error(
            "Cannot reach Ollama at %s: %s\n"
            "Ensure Ollama is running (ollama serve) on port 11434 by default.",
            base_url, exc,
        )
        sys.exit(1)

    if not resp.is_success:
        logger.error("Ollama server at %s returned HTTP %d.", base_url, resp.status_code)
        sys.exit(1)

    available = [m["id"] for m in resp.json().get("data", [])]
    if available and model not in available:
        logger.error(
            "Model %r not found in Ollama.\nAvailable model(s): %s\nRun: ollama pull %s",
            model, ", ".join(available), model,
        )
        sys.exit(1)


def _assert_model_available(model_id: str) -> None:
    """Exit with a clear error if model_id is not usable on OpenRouter."""
    if settings.openrouter_api_key == "dummy":
        return  # no key — let the actual call produce the 401

    # Probe with a 1-token call — the only reliable way to confirm a model
    # has working providers (listed models can still have no active providers).
    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": model_id, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
            timeout=15.0,
        )
    except Exception as exc:
        logger.warning("Could not probe model %r (%s) — skipping check", model_id, exc)
        return

    if resp.status_code == 200:
        return

    env_var = "OPENROUTER_STAGE_SUMMARIZATION_MODEL" if ":free" in model_id else "OPENROUTER_SUMMARIZATION_MODEL"
    try:
        error_detail = resp.json()
    except Exception:
        error_detail = resp.text
    logger.error(
        "Model %r is not available on OpenRouter (status %d).\n"
        "OpenRouter response: %s\n"
        "Update %s in your .env.",
        model_id,
        resp.status_code,
        error_detail,
        env_var,
    )
    sys.exit(1)


def _make_summarizer(stage: bool) -> Summarizer:
    if settings.ollama_base_url:
        model = (
            settings.ollama_stage_summarization_model or settings.ollama_summarization_model
            if stage
            else settings.ollama_summarization_model
        )
        return Summarizer(model=model, base_url=settings.ollama_base_url, api_key="ollama")
    stage_model = settings.openrouter_stage_summarization_model or settings.openrouter_summarization_model
    return Summarizer(model=stage_model if stage else None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_scrapers(site: str | None) -> list:
    # TODO(Step 5): instantiate the per-company scrapers from the sources.py
    # registry (Step 2a). No scrapers exist yet, so every stage is a no-op.
    logger.warning("No scrapers registered yet — see docs/plan.md Steps 2a and 5")
    return []


def _scraper_infos(scrapers: list) -> list[dict]:
    return [{"company": s.company, "sources": s.sources, "exclusions": s.exclusions} for s in scrapers]


def _scrape_and_cache(scraper, db: ArticleDB, limit: int, category: str | None = None) -> list[ScrapedPage]:
    """Run scraper, persist each page to scraped_articles + article_text cache, return pages."""
    pages = scraper.run(db, limit=limit, category=category)
    for page in pages:
        existing = db.get_by_url(page.url)
        record = ArticleRecord.from_scraped_page(
            page,
            first_scraped_at=existing.first_scraped_at if existing else None,
        )
        db.upsert(record)
        db.save_text(normalize_url(page.url), page.raw_text)
    return pages


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------

def _run_scrape(args: argparse.Namespace, db: ArticleDB) -> None:
    limit = args.limit or 1
    scrapers = _build_scrapers(args.site)
    all_pages: list[ScrapedPage] = []
    for scraper in scrapers:
        pages = _scrape_and_cache(scraper, db, limit=limit, category=args.category)
        logger.info("[stage:scrape] %s: %d page(s) scraped", scraper.company, len(pages))
        for i, page in enumerate(pages, 1):
            preview = " ".join(page.raw_text.split())[:400]
            if page.published_date:
                try:
                    age_days = (datetime.now(UTC).date() - date.fromisoformat(page.published_date)).days
                    age_str = f"{age_days}d ago"
                except ValueError:
                    age_str = "unknown age"
            else:
                age_str = "unknown age"
            logger.info(
                "[stage:scrape] [%d/%d] %s | %s | %s | %s\n  Title: %s\n  URL:   %s\n  Text (%s chars): %s%s",
                i, len(pages), page.company, page.category,
                page.published_date or "no date", age_str,
                page.title, page.url,
                f"{len(page.raw_text):,}",
                preview, "…" if len(page.raw_text) > 400 else "",
            )
        all_pages.extend(pages)

    publisher = GitHubPagesPublisher(db)
    publisher.render_scrape_preview(all_pages, _DRY_RUN_DIR, _scraper_infos(scrapers))


def _run_summarize(args: argparse.Namespace, db: ArticleDB) -> None:
    if settings.ollama_base_url:
        model = settings.ollama_stage_summarization_model or settings.ollama_summarization_model
        backend = f"ollama ({model})"
        _assert_ollama_available(model)
    else:
        model = settings.openrouter_stage_summarization_model or settings.openrouter_summarization_model
        backend = f"openrouter ({model})"
        _assert_model_available(model)

    limit = args.limit or 1
    scrapers = _build_scrapers(args.site)
    summarizer = _make_summarizer(stage=True)
    records: list[ArticleRecord] = []

    for scraper in scrapers:
        cached = db.articles_with_text(scraper.company, category=args.category, limit=limit)
        if cached:
            pages = [
                ScrapedPage(
                    url=a.url, company=a.company, category=a.category,
                    title=a.title, raw_text=db.get_text(a.normalized_url) or "",
                    published_date=a.published_date,
                )
                for a in cached
            ]
            logger.info("[stage:summarize] %s: using %d cached article(s)", scraper.company, len(pages))
        else:
            logger.info("[stage:summarize] %s: no cached articles — scraping %d", scraper.company, limit)
            pages = _scrape_and_cache(scraper, db, limit=limit, category=args.category)
            if not pages:
                logger.warning("[stage:summarize] %s: no pages found", scraper.company)
                continue

        for i, page in enumerate(pages, 1):
            logger.info("[stage:summarize] [%d/%d] summarizing %s via %s", i, len(pages), page.url, backend)
            t0 = time.monotonic()
            summary = summarizer.summarize(page)
            logger.info(
                "[stage:summarize] [%d/%d] done in %.1fs — input %d chars, output %d chars",
                i, len(pages), time.monotonic() - t0, len(page.raw_text), len(summary),
            )

            existing = db.get_by_url(page.url)
            record = ArticleRecord.from_scraped_page(
                page,
                first_scraped_at=existing.first_scraped_at if existing else None,
                summary=summary,
            )
            db.upsert(record)
            records.append(record)

    publisher = GitHubPagesPublisher(db)
    publisher.render_summary_preview(records, _DRY_RUN_DIR, _scraper_infos(scrapers))


def _run_render(args: argparse.Namespace, db: ArticleDB) -> None:
    publisher = GitHubPagesPublisher(db)
    publisher.render_from_db(_DRY_RUN_DIR, _scraper_infos(_build_scrapers(args.site)), limit=args.limit)
    logger.info("[stage:render] HTML written to %s — review, then run --publish", _DRY_RUN_DIR)


def _run_publish(args: argparse.Namespace, db: ArticleDB) -> None:
    publisher = GitHubPagesPublisher(db)
    publisher.publish(_scraper_infos(_build_scrapers(args.site)))


def _run_full_pipeline(args: argparse.Namespace, db: ArticleDB) -> None:
    if settings.ollama_base_url:
        _assert_ollama_available(settings.ollama_summarization_model)
    else:
        _assert_model_available(settings.openrouter_summarization_model)

    scrapers = _build_scrapers(args.site)
    summarizer = _make_summarizer(stage=False)
    publisher = GitHubPagesPublisher(db)
    new_records: list[ArticleRecord] = []
    stats: dict[str, dict[str, int]] = {}

    for scraper in scrapers:
        pages = scraper.run(db)
        n_pages = len(pages)
        logger.info("%s: %d new/updated pages", scraper.company, n_pages)
        stats[scraper.company] = {"found": n_pages, "processed": 0}

        for j, page in enumerate(pages, 1):
            progress = f"[{j}/{n_pages}]"
            logger.info("%s %s | %s", progress, page.company, page.title)
            try:
                # Persist raw text for future staged runs
                db.save_text(normalize_url(page.url), page.raw_text)

                summary = summarizer.summarize(page)

                existing = db.get_by_url(page.url)
                first_scraped_at = existing.first_scraped_at if existing else None
                record = ArticleRecord.from_scraped_page(
                    page,
                    first_scraped_at=first_scraped_at,
                    summary=summary,
                )
                db.upsert(record)
                new_records.append(record)
                stats[scraper.company]["processed"] += 1
                logger.debug("Processed %s", page.url)
            except Exception:
                logger.exception("Failed to process %s — skipping", page.url)

    if new_records:
        logger.info("Publishing %d updates to GitHub Pages", len(new_records))
        publisher.publish(_scraper_infos(scrapers))
    else:
        logger.info("No new updates — skipping publish")

    post_discord_summary(stats)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_count(args: argparse.Namespace) -> None:
    """Discover URLs via sitemap/RSS and print counts — no scraping, no DB writes."""
    if args.since:
        try:
            since_date = date.fromisoformat(args.since)
        except ValueError:
            logger.error("--since must be YYYY-MM-DD, got: %s", args.since)
            sys.exit(1)
        age_days = (datetime.now(UTC).date() - since_date).days
        if age_days < 0:
            logger.error("--since date %s is in the future", args.since)
            sys.exit(1)
        settings.max_article_age_days = age_days
        logger.info("Using cutoff date %s (%d days back)", since_date, age_days)

    scrapers = _build_scrapers(args.site)
    grand_total = 0
    for scraper in scrapers:
        urls = scraper.discover_urls()
        by_cat: dict[str, int] = {}
        for _, cat in urls:
            by_cat[cat] = by_cat.get(cat, 0) + 1
        total = len(urls)
        grand_total += total
        breakdown = ", ".join(f"{cat}: {n}" for cat, n in sorted(by_cat.items()))
        print(f"{scraper.company}: {total} URL(s)  [{breakdown}]")
        for url, cat in urls:
            logger.debug("  [%s] %s", cat, url)
        scraper.close()

    if len(scrapers) > 1:
        print(f"total: {grand_total} URL(s)")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape news and blog posts and publish to GitHub Pages")
    parser.add_argument(
        "--count",
        action="store_true",
        help="Discover and count URLs only — no scraping, no DB writes",
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Override the article age cutoff (used with --count or any stage)",
    )
    parser.add_argument(
        "--stage",
        choices=["scrape", "summarize", "render"],
        help=(
            "Run one pipeline stage: "
            "scrape (fetch + cache, render preview), "
            "summarize (LLM summary from cache, render preview), "
            "render (render full site from DB to data/dry-run/ for review)"
        ),
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Rebuild full site from DB and push to GitHub Pages",
    )
    parser.add_argument(
        "--site",
        choices=COMPANIES,  # TODO(Step 2a): source these from the sources.py registry
        help="Run only one scraper (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Max articles per company (default: 1 for scrape/summarize, all for render)",
    )
    parser.add_argument(
        "--category",
        choices=["blog", "press_release", "product", "release_notes"],
        help="Filter to one article category (default: all)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    setup_logging()

    if args.count:
        _run_count(args)
        return

    # Apply --since outside of --count too (e.g. backfill scrape)
    if args.since:
        try:
            since_date = date.fromisoformat(args.since)
        except ValueError:
            logger.error("--since must be YYYY-MM-DD, got: %s", args.since)
            sys.exit(1)
        settings.max_article_age_days = (datetime.now(UTC).date() - since_date).days
        logger.info("Cutoff overridden to %s (%d days)", since_date, settings.max_article_age_days)

    logger.info("Starting ai-digest (stage=%s, site=%s)", args.stage, args.site)

    with ArticleDB(settings.sqlite_db_path) as db:
        if args.publish:
            _run_publish(args, db)
        elif args.stage == "scrape":
            _run_scrape(args, db)
        elif args.stage == "summarize":
            _run_summarize(args, db)
        elif args.stage == "render":
            _run_render(args, db)
        else:
            _run_full_pipeline(args, db)


if __name__ == "__main__":
    main()
