# CLAUDE.md — ai-digest

Guidance for agents (and humans) working in this repo. Read this **before**
making changes.

A daily cron job that scrapes AI company blogs and news feeds, summarizes each
new item with an LLM, and publishes a static HTML digest to GitHub Pages.
Sources are fixed in [`docs/sources.md`](docs/sources.md); the build plan is
[`docs/plan.md`](docs/plan.md). Read both before adding a scraper.

## Stack (locked — do not swap)

| Concern | Choice |
|---|---|
| Language / runtime | Python 3.13, venv at `.venv/` (never system Python) |
| Package manager | `uv` — `make sync` runs `uv sync --extra dev` |
| HTTP | `httpx` **only** — sync `Client`, `follow_redirects=True` |
| HTML parsing | `beautifulsoup4` with the `lxml` parser |
| Feed / sitemap parsing | `xml.etree.ElementTree` (stdlib) |
| Storage | SQLite via stdlib `sqlite3` — dedup + operational metadata |
| Models / config | `pydantic` v2 + `pydantic-settings` |
| LLM | `langchain-core` + `langchain-openai` against OpenRouter — **the only backend** |
| Templating | `Jinja2` |
| Publishing | `GitPython` → push to `gh-pages` |
| Retries | `tenacity` |
| CLI output | `rich` |
| Tests | `pytest` + `pytest-mock` + `respx` |
| Lint | `ruff` — config in `pyproject.toml`, run via `make lint` |

**Do not add** any of these, all of which were deliberately excluded:

- `sqlite-vec`, `chromadb`, or any embeddings client — **this project has no
  vector store, no semantic search, and no RAG.** See the scope note at the top
  of `docs/plan.md`.
- An MCP server or Hermes integration. Discord is a one-way webhook
  notification only; there is no bot to ask questions of.
- `feedparser` — stdlib `ElementTree` already parses every feed in
  `docs/sources.md`.
- `playwright`, `selenium`, or any headless browser. The httpx-only constraint
  is why xAI and Perplexity are excluded as sources; adding a browser is a
  scope decision, not a scraper fix.
- `requests` — `httpx` is the one HTTP client.
- **Ollama, LM Studio, or any local model server.** OpenRouter is the only LLM
  backend. The ported code had an Ollama path (`OLLAMA_BASE_URL` and friends);
  it was removed on 2026-08-01. `OPENROUTER_BASE_URL` exists for pointing at a
  proxy, not for reintroducing a local backend.

## Rules

- Before writing/debugging against an external library, fetch current docs
  rather than relying on memory. Two sources, and they don't overlap:
  - **`chub`** ([Context Hub](https://github.com/andrewyng/context-hub)) — the
    default, and it covers nearly this whole stack. Use `--lang py`.
    Verified entries: `langchain/core`, `langchain/openai`, `pydantic/package`,
    `pydantic/settings`, `httpx/package`, `beautifulsoup4/package`,
    `lxml/package`, `jinja2/package`, `tenacity/package`, `rich/package`,
    `pytest/package`, `pytest/mock`, `respx/package`, `uv/package`.
  - **Context7 MCP** — **only** for the three things `chub` has no entry for:
    **GitPython**, **Python-Markdown**, and the **OpenRouter API**. Don't burn
    calls on it for anything in the `chub` list above.

- Never commit `.env`. `data/` and `logs/` are gitignored; the SQLite DB lives
  outside the repo in production (`SQLITE_DB_PATH`).

- Never let one bad page abort a run. Scrapers catch per-URL exceptions, log,
  and continue — see `_safe_scrape` / `run` in `scrapers/base.py`.

## Validate

Write tests for your changes, then:

```bash
make test        # uv run pytest
make lint        # uv run ruff check .   — must be clean before committing
make lint-sh     # shellcheck on every tracked/new *.sh — must also be clean
make fix         # ruff check --fix      — auto-fixes imports, sorting, simple rules
```

Two ruff rule groups exist specifically to enforce conventions documented below,
so don't `# noqa` them without a reason:

- **`G` (logging-format)** — `G004` catches f-strings in log calls, enforcing
  the lazy `%s` rule.
- **`DTZ` (datetimez)** — `DTZ005` catches `datetime.now()` without a timezone.
  This pipeline is UTC-only end to end.

Formatting is **not** enforced. `make format` (`ruff format`) exists but is
opt-in and deliberately excluded from `make lint`, so porting code from
`product-update-digest` doesn't produce a whole-file reformat diff.

Tests must run **fully offline** — `respx` for HTTP, in-memory SQLite, saved
fixtures for every feed and page. No test may hit a live site or an LLM API.
Capture a fixture instead.

Before publishing anything, use the dry-run stages, which write preview HTML to
`data/dry-run/` and never push:

```bash
uv run digest --count                     # discovery only; no scrape, no DB write
uv run digest --stage scrape --limit 3    # fetch + cache text
uv run digest --stage summarize --limit 1 # LLM summaries
uv run digest --stage render              # full site preview from the DB
```

`--stage summarize` calls a real LLM. Point
`OPENROUTER_STAGE_SUMMARIZATION_MODEL` at a free-tier model while iterating.

## Conventions (follow these)

**Layout.** `src/` layout, package `digest`, entry point `uv run digest`
(`[project.scripts]` → `digest.main:main`). New modules go under
`src/digest/`.

**Sources are data, not code.** Every company and feed is declared in
`src/digest/sources.py`. Adding a source means adding a registry entry — never
hardcode a company list. `main.py`, `publisher/github_pages.py`, and the
`--site` argparse choices all read from that registry.

**Scrapers.** Subclass `BaseScraper` (or `FeedScraper` / `SitemapScraper`) and
implement `discover_urls()` and `scrape_page()`. Prefer sitemaps and feeds over
scraping listing pages — listing pages are usually JS-rendered and break
silently. Log a warning when `discover_urls()` returns zero URLs; that is the
signal a site changed.

**Config.** Everything tunable is an env var in `config.py` via
`pydantic-settings`, with a default. No hardcoded paths, keys, URLs, or magic
numbers in module bodies. Document new settings in `README.md`'s config table
and `.env.example`.

**Deduplication.** Normalized URL + SHA-256 content hash. Never re-summarize
unchanged content — LLM calls are the only real cost in this pipeline.

**Logging.** Module-level `logger = logging.getLogger(__name__)`, lazy `%s`
formatting (not f-strings) in log calls, and prefix messages with the company
key. Anything a filter drops should be logged at `DEBUG` so a mis-tuned
allowlist is visible rather than silent.

**Typing.** Full annotations; modern syntax (`str | None`, `list[tuple[...]]`).
Python 3.13, so no `from __future__ import annotations` needed.

**Dates.** ISO 8601 strings in models, `YYYY-MM-DD` for `published_date`. Feed
`pubDate` is RFC 822 — parse with `email.utils.parsedate_to_datetime`.

<!-- rtk-instructions v2 -->

## RTK — token-optimized commands

Prefix a command with `rtk` where a filter applies for compact output; anything
without a dedicated filter passes through unchanged, so `rtk` is always safe
to prepend. Relevant to this project:

```bash
rtk git status / log / diff / add / commit / push / pull   # compact (59-80%)
rtk ruff check .              # violations grouped by rule, then listed
rtk ruff format --check .     # files needing format only
rtk test uv run pytest        # failures only
rtk err <cmd>                 # errors/warnings only — good for `uv run digest`
rtk ls / read / grep / rg / find / tree   # compact file & search output (60-75%)
rtk diff                      # ultra-condensed diff (changed lines only)
rtk deps                      # summarize pyproject dependencies
rtk curl / json               # compact HTTP + JSON when probing feeds
```

`rtk ruff` shells out to `ruff` on **PATH**, not the project venv — that copy is
installed with `uv tool install ruff@0.14.14`, pinned to match the dev
dependency. `make lint` uses the venv copy. If you bump ruff in
`pyproject.toml`, re-run `uv tool install` or the two will drift.

The JS/TS filters (`npm`, `tsc`, `lint`, `prettier`, `playwright`) don't apply
to this repo — note `rtk lint` is ESLint, not ruff.

Full command reference and analytics (`rtk gain`, etc.) in
[README.md](./README.md#rtk-cli-reference).
<!-- /rtk-instructions -->
