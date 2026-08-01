# Implementation Plan: `ai-digest`

A daily digest of AI company news, modeled on `product-update-digest` but
**deliberately smaller in scope**:

```
scrape → dedupe → summarize → publish
```

**No embeddings, no vector store, no RAG, no Hermes, no MCP server.** There is
no semantic search and no Discord question-answering. Discord is used only for
a one-way run-completion notification via webhook. Anything in
`product-update-digest` that exists to serve retrieval — `sqlite-vec`,
`storage/vec_client.py`, `tools/search.py`, `tools/rag.py`, `src/hermes/`, the
`ProductUpdate` model, the `--stage vector` pipeline stage — is **not ported**.

Source list is finalized in [`sources.md`](sources.md) — read that first; this
plan assumes it.

---

## Decision 0 — separate repo vs. generalize the existing one

**Settled: separate repo, code copied from `product-update-digest`.** Already in
motion — the scaffolding files are copied in (see Step 1).

The dropped-retrieval decision reinforces this. Generalizing
`product-update-digest` to serve both digests would now mean making the vector
store itself optional throughout that codebase, which is a larger and riskier
refactor than the fork it would avoid.

## Step 1 — Bootstrap (partially done)

### Already copied and adapted

`pyproject.toml` (name `ai-digest`, `sqlite-vec` and the `openai` SDK already
removed from dependencies), `Makefile` (`deploy-mcp` target already removed),
`.env.example`, `.gitignore`, `.python-version`, `LICENSE`, `README.md`,
`INSTALL.md`, `CLAUDE.md`, `.mcp.json`.

Package name stays **`digest`** (`[project.scripts] digest = "digest.main:main"`).
No rename is needed — the only reason to rename was MCP-server coexistence on
the VPS, and there is no MCP server here. The two projects have separate venvs.

### Still to copy from `product-update-digest`

| Path | Notes |
|---|---|
| `src/digest/config.py` | strip retrieval settings — Step 2d |
| `src/digest/main.py` | strip the `vector` stage — Step 8 |
| `src/digest/notifier.py` | webhook only — Step 8 |
| `src/digest/scrapers/base.py` | copy as-is; it has no vector coupling |
| `src/digest/storage/models.py` | drop `ProductUpdate` — Step 2b |
| `src/digest/storage/db.py` | add `source` column — Step 2c |
| `src/digest/summarizer/__init__.py` | copy as-is |
| `src/digest/publisher/` | incl. `templates/` — Step 7 |
| `tests/` | minus vendor + vector tests |
| `data/`, `logs/` | `.gitkeep` only |

### Deliberately NOT copied

`src/digest/storage/vec_client.py`, `tools/search.py`, `tools/rag.py`,
`src/hermes/digest_mcp.py`, `publisher/templates/vector_preview.html.j2`, and
the Cribl/Ocient/Palo Alto scrapers with their tests and fixtures.

### Target layout

```
ai-digest/
├── docs/{plan.md,sources.md}
├── pyproject.toml, Makefile, README.md, INSTALL.md, CLAUDE.md
├── src/digest/
│   ├── config.py
│   ├── main.py
│   ├── notifier.py
│   ├── sources.py            # NEW — the source registry
│   ├── scrapers/
│   │   ├── base.py
│   │   ├── feed.py           # NEW — RSS/Atom scraper base
│   │   ├── sitemap.py        # NEW — generalized from cribl.py
│   │   ├── anthropic.py
│   │   ├── openai.py
│   │   ├── google.py
│   │   ├── microsoft.py
│   │   ├── aws.py
│   │   ├── mistral.py
│   │   └── press.py          # TechCrunch + Ars, one module
│   ├── storage/{models.py,db.py}      # note: no vec_client.py
│   ├── publisher/{github_pages.py,templates/}
│   └── summarizer/__init__.py
├── tests/
└── data/, logs/
```

**Verify:** `make sync && make test` passes (trivially, with no tests yet).

## Step 2 — Source registry and schema

The structural work. Five changes: three driven by the source list, two by
dropping retrieval.

### 2a. `company` becomes a registry, not a `Literal`

`storage/models.py` currently pins `company: Literal["cribl", "ocient", "xsiam"]`
in each model. With eight companies — two of which have multiple feeds — that
becomes an unwieldy repeated `Literal`. Replace with a registry in `sources.py`:

```python
# src/digest/sources.py
@dataclass(frozen=True)
class Source:
    key: str                    # "google-deepmind" — unique across all sources
    company: str                # "google"
    label: str                  # "DeepMind Blog"
    url: str
    kind: Literal["rss", "atom", "sitemap"]
    category: Category          # default category for items from this source
    content_in_feed: bool = False   # True → skip the article fetch
    paginate: bool = False          # True → ?paged=N supported for backfill
    include_categories: tuple[str, ...] = ()   # feed <category> allowlist
    exclude_patterns: tuple[str, ...] = ()     # URL/title substrings to drop
    daily_cap: int | None = None

@dataclass(frozen=True)
class Company:
    key: str            # "google"
    label: str          # "Google"
    group: Literal["vendor", "press"]
    sources: tuple[Source, ...]

COMPANIES: dict[str, Company] = {...}   # single source of truth
```

`main.py`, `publisher/github_pages.py` (which hardcodes `COMPANIES = [...]`),
and the `--site` argparse `choices` all read from this registry.

### 2b. `storage/models.py` — add `source`, drop `ProductUpdate`

**Add** `source: str` (the `Source.key`) to `ScrapedPage` and `ArticleRecord`.
This is what makes "one company, many sources" work: a DeepMind article carries
`company="google"`, `source="google-deepmind"`, so the Google page can badge or
group by source.

**Remove**, as retrieval-only:

- the entire `ProductUpdate` model — it existed solely as the sqlite-vec
  document, and its `source_text` field only existed to be embedded
- `vec_id_for()` — the MD5-of-normalized-URL vector document ID
- the `vec_id` field on `ArticleRecord`

**Keep** `normalize_url()` and the `content_hash` logic — both belong to
deduplication, not retrieval.

### 2c. `storage/db.py` — one new column

Add a `source` column: `CREATE TABLE`, `INSERT`, `SELECT`, and the row→model
mapping. Drop `vec_id` from all four. Greenfield DB, so **no migration** — get
the schema right once.

### 2d. `config.py` — remove retrieval settings

Delete: `openrouter_embedding_model`, `embedding_dimensions`,
`openrouter_rag_model`, `ollama_rag_model`, `max_source_text_chars`,
`search_score_threshold`, `rag_chunk_size_chars`, `rag_chunk_overlap_chars`.

Keep `summarizer_content_chars` — that one feeds the summarizer, not embeddings.

Change: `sqlite_db_path` → `/opt/digest/ai_digest.db`, `github_repo` → the new
Pages repo, `discord_notify_method` default → `webhook` (matching the copied
`.env.example`; the `hermes` method is removed entirely — see Step 8).

### 2e. Widen the `category` Literal

Current: `blog | press_release | product | release_notes`. AI sources need
`research` (Anthropic, DeepMind), `engineering` (Anthropic), and `news`
(OpenAI, press). Final set:

`blog | research | engineering | news | press_release | product | release_notes`

**Verify:** unit tests for registry invariants (source keys unique, every
`Source.company` present in `COMPANIES`, every `Company.sources` non-empty).

## Step 3 — `FeedScraper` base class

Eight of the ten sources are RSS. `scrapers/feed.py` handles all of them; only
Anthropic needs a sitemap.

Responsibilities:

1. **Parse** RSS `<item>` and Atom `<entry>` with `xml.etree.ElementTree`
   (stdlib — no `feedparser` dependency; already validated against all ten
   feeds). Extract `link`, `title`, `pubDate`/`updated`, `<category>` tags, and
   `content:encoded` where present.
2. **Cache per-URL metadata** in `self._feed_meta[url]` during `discover_urls()`
   — same pattern as `CriblScraper._sitemap_lastmod`, but carrying title, date,
   categories, and body. `scrape_page()` then reads from it.
3. **Skip the HTTP fetch when `content_in_feed=True`.** Ars Technica ships full
   `content:encoded`; using it saves one request and one parse per article.
   Everything else fetches the article page as usual.
4. **Filter** by `include_categories` and `exclude_patterns` before any fetch,
   so filtering costs nothing.
5. **Paginate** via `?paged=N` when `paginate=True` and the requested date
   window isn't yet covered — needed only for backfill, since TechCrunch's feed
   holds ~31 hours and Ars's ~4 days.
6. **Apply `daily_cap`** as a per-run limit, newest first.

Date handling: feed `pubDate` is RFC 822 → `email.utils.parsedate_to_datetime`
(already imported in `base.py`), then `.date().isoformat()`. More reliable than
HTML date extraction, so feed sources prefer the feed date and fall back to page
HTML only when absent.

`pre_check`: the inherited HEAD-based check works, but the feed's own `pubDate`
is a better signal — override `pre_check` to return `False` (unchanged) when the
feed date isn't newer than `last_scraped_at`, avoiding a HEAD request per known
URL on every run.

**Verify:** `respx`-mocked tests per feed shape — RSS with categories, Atom,
`content:encoded` present/absent, pagination, and malformed XML that must not
raise.

## Step 4 — `SitemapScraper` base + Anthropic

Generalize `CriblScraper._discover_from_sitemap()` into `scrapers/sitemap.py`:
fetch sitemap, parse `<url>` elements, filter by `lastmod` against the age
cutoff, map path prefixes to categories, stash `lastmod` as a date fallback.

`scrapers/anthropic.py` configures it:

| Path prefix | Category |
|---|---|
| `/news/` | `news` |
| `/research/` | `research` |
| `/engineering/` | `engineering` |

Exclude `/legal/`, `/careers/`, `/events/`, `/claude/`, `/product/`, and the
policy/program pages. Anthropic's `lastmod` values are genuine per-page
timestamps (unlike Palo Alto's, which reset daily), so the cutoff filter works
directly — no dateline-parsing workaround needed.

Title/date extraction: reuse Cribl's `_extract_title` / `_extract_date`
(og:title → h1 → title; JSON-LD `datePublished` → `article:published_time` →
`<time datetime>`). Verify against a saved Anthropic page fixture before
assuming JSON-LD is present.

**Verify:** fixture-based test using a real saved `/news/` and `/research/`
page; assert category mapping and that excluded prefixes drop.

## Step 5 — Company scrapers

Each is a thin subclass declaring its sources. Expected total: **~26
articles/day**, so most of the work is the two filtered sources.

| Module | Company | Sources | Work required |
|---|---|---|---|
| `anthropic.py` | `anthropic` | sitemap | Step 4 |
| `openai.py` | `openai` | `news/rss.xml` | Trivial. **Ignore `<category>` — all 958 tags are empty.** |
| `google.py` | `google` | blog.google AI + DeepMind | Trivial; both topic-scoped. DeepMind has no `<category>` elements — categorize by source. |
| `microsoft.py` | `microsoft` | Source AI + Azure blog | **Azure needs a category allowlist** — there is no AI-scoped Azure feed (the topic feed 404s). Allowlist against the 39 `<category>` values. |
| `aws.py` | `aws` | ML blog | Trivial; fully AI-scoped. |
| `mistral.py` | `mistral` | `rss.xml` | Trivial; ~0.1/day. |
| `press.py` | `techcrunch`, `arstechnica` | two feeds | Category allowlist + `daily_cap`; Ars uses `content_in_feed=True`; both `paginate=True`. |

**The Azure allowlist is the one piece of guesswork here.** Pull the live
`<category>` list first, pick the AI terms, and log anything the filter drops at
`DEBUG` so a mis-tuned allowlist is visible rather than silent. Same for the
press feeds.

**Verify:** per scraper, a test asserting `discover_urls()` returns the expected
`(url, category)` pairs from a fixture feed, and that filtered sources drop known
off-topic items (e.g. TechCrunch's "India is starting to pay for apps", Ars's
Reddit/DMCA piece — both real examples from the source probe).

## Step 6 — Volume control

TechCrunch alone is ~15 articles/day, more than all seven vendor sources
combined. Without this step the index page and the Discord notification are both
dominated by press coverage.

1. **`daily_cap` per source** — enforced in `FeedScraper`, newest first.
   Suggested: TechCrunch 8, Ars 5, uncapped elsewhere.
2. **`group` on `Company`** (`vendor` | `press`) — the index template renders
   vendor sources first, press in a separate section below.
3. **`index_per_company`** already exists in config and does the right thing.
   Set press companies lower.

Cost: ~26 articles/day at `google/gemma-3-27b-it` rates is ~$0.005/day, about
**$0.16/month**. With embeddings dropped, summarization is the only API spend.

## Step 7 — Publisher

- `COMPANIES` list in `github_pages.py` comes from the registry (Step 2a).
- `index.html.j2` — split vendor and press sections (Step 6.2).
- `company_index.html.j2` — for multi-source companies, show a source badge
  ("DeepMind", "Google AI Blog") per entry. Single-source companies render
  unchanged.
- Do not port `vector_preview.html.j2`.
- Site structure: `index.html`, then `anthropic/`, `openai/`, `google/`,
  `microsoft/`, `aws/`, `mistral/`, `techcrunch/`, `arstechnica/`.

**Verify:** `--stage render` produces the full site into `data/dry-run/` for
local review before anything is pushed. Confirm the source badges and the
vendor/press split there.

## Step 8 — Pipeline stages, notifier

### Stages drop from four to three

`main.py`'s stage machinery loses `vector` entirely:

| Stage | What it does |
|---|---|
| `scrape` | Fetch pages, cache text in SQLite, write `data/dry-run/` preview |
| `summarize` | Call the LLM on cached articles, write summary preview |
| `render` | Render the full site from the DB to `data/dry-run/` |

Remove from `main.py`: the `VecClient` import and all its call sites, the
`--stage vector` branch and its `data/dry-run/vec_test.db` temp store, and
`vector` from the `--stage` argparse `choices`. The default full-pipeline run
becomes **scrape → summarize → publish**.

### Notifier

`notifier.py` keeps the run-completion Discord message but **webhook only** —
remove the `hermes` method, `discord_hermes_channel`, and `discord_hermes_bin`.
Per-company counts come from the registry. Message shape:

```
**Daily AI digest complete**
• Anthropic: 2 new articles
• OpenAI: 1 new article
• Google: 3 new articles
…
**Total: 26 found, 24 processed**
```

This is the **only** Discord integration. There is no bot to ask questions of.

## Step 9 — Tests

Port the existing suite minus the vendor scraper tests and every vector/RAG test
(`test_vec_client.py`, search/RAG tool tests), plus:

- `test_sources.py` — registry invariants (Step 2a).
- `test_feed_scraper.py` — RSS/Atom/`content:encoded`/pagination/malformed XML.
- `test_filters.py` — category allowlists drop known off-topic items.
- `test_daily_cap.py` — cap applied newest-first.
- One fixture-based test per company scraper.

Fixtures: save real responses from all ten sources **now**, before they drift.
All ten were reachable on 2026-07-31.

Everything stays offline — `respx` for HTTP, in-memory SQLite. With sqlite-vec
gone the suite has one less native dependency.

## Step 10 — Deployment

`INSTALL.md` is already adapted and is nearly correct. Remaining work:

1. Apply the Step 1 cleanup items (Hermes reference, DB filename consistency).
2. Create the GitHub Pages repo, initialize the `gh-pages` orphan branch
   (INSTALL.md Step 7 covers this).
3. Backfill: `uv run digest --since <30d ago>` — the one run where press
   pagination matters, and the only run with a non-trivial LLM bill (~800
   articles ≈ $0.16 one-time). Note `.env.example` ships
   `MAX_ARTICLE_AGE_DAYS=2`, so day-to-day runs stay small.
4. Cron, offset from the existing `product-update-digest` job so the two don't
   contend — INSTALL.md currently has `0 6 * * *`; confirm that doesn't collide.

No MCP deployment step, no `make deploy-mcp`, no Hermes `config.yaml` changes,
and no group-readable data directory needed for a second account to read the DB.
The digest is self-contained: cron in, GitHub Pages and a Discord webhook out.

## Open items

- **Azure `<category>` allowlist** — needs the live category list before it can
  be written (Step 5). Everything else is fully specified.
- **Anthropic page structure** — Cribl's JSON-LD-first date extraction is
  assumed to work; confirm against a real page in Step 4.
- **xAI, Perplexity, Cohere, DeepSeek** are blocked by Cloudflare or have no
  feed (see `sources.md`). If one becomes important it needs a headless browser,
  which breaks the httpx-only design — a separate decision, not a scraper.
