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

## Step 1 — Bootstrap — **done**

Completed 2026-07-31. The tree imports, `make lint` is clean, and `make test`
passes. Two decisions shaped how it was done:

- **Copy + strip, not verbatim copy.** Files were ported with every reference to
  non-ported retrieval code removed in the same pass, because a verbatim copy
  could not import at all — `main.py` referenced `VecClient`, and `config.py`
  declared `max_source_text_chars: int` with no default, so `Settings()` raised
  at import. This pulled forward parts of Steps 2b, 2c, 2d and all of Step 8.
- **Tests deferred to Step 9.** Only `tests/test_smoke.py` ships, and only
  because `pytest` exits 5 on an empty suite, which would fail `make test`.

Left stubbed with `TODO(Step 2a)` markers: `COMPANIES` in
`publisher/github_pages.py` is a hardcoded eight-key list, `--site` reads its
choices from it, and `_build_scrapers()` returns `[]` and logs a warning.

### Already copied and adapted

`pyproject.toml` (name `ai-digest`, `sqlite-vec` and the `openai` SDK already
removed from dependencies), `Makefile` (`deploy-mcp` target already removed),
`.env.example`, `.gitignore`, `.python-version`, `LICENSE`, `README.md`,
`INSTALL.md`, `CLAUDE.md`, `.mcp.json`.

Package name stays **`digest`** (`[project.scripts] digest = "digest.main:main"`).
No rename is needed — the only reason to rename was MCP-server coexistence on
the VPS, and there is no MCP server here. The two projects have separate venvs.

### Copied from `product-update-digest`

| Path | Status |
|---|---|
| `src/digest/config.py` | ✅ retrieval + hermes settings stripped (Step 2d) |
| `src/digest/main.py` | ✅ `vector` stage stripped (Step 8) |
| `src/digest/notifier.py` | ✅ webhook only (Step 8) |
| `src/digest/scrapers/base.py` | ✅ as-is; only the User-Agent string changed |
| `src/digest/storage/models.py` | ✅ `ProductUpdate`, `vec_id_for`, `vec_id` dropped (Step 2b); `source` column still to add |
| `src/digest/storage/db.py` | ✅ `vec_id` column + `chroma_id` migration dropped; `source` column still to add (Step 2c) |
| `src/digest/summarizer/__init__.py` | ✅ as-is; prompt rewritten in Step 2f |
| `src/digest/publisher/` | ✅ incl. `templates/`, minus `vector_preview.html.j2`; templates still Cribl/Ocient-branded until Step 7 |
| `tests/` | ⏸ deferred to Step 9; only `test_smoke.py` exists |
| `data/`, `logs/` | ✅ `.gitkeep` only, with a `!data/.gitkeep` negation |

Also cleaned up here, since Step 10 lists them as Step 1 items: the stale hermes
references in `README.md`, the DB filename (four different spellings across
`config.py`, `.env.example`, `README.md` and `INSTALL.md`), and the README
config table, which was missing six settings and had a wrong
`COMPANY_PAGE_LIMIT` default. `INSTALL.md` lost its `/opt/digest` group setup.

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

## Step 2 — Source registry and schema — **done**

Completed 2026-08-01. The structural work. Seven changes: three driven by the
source list, two by dropping retrieval, and two on the summarizer — a prompt
still written for the old vendors (2f) and longer summaries for firewall-blocked
companies (2g).

Landed as 2e → 2a → 2b → 2c → 2f → 2g, one commit each; 2e moved first because
the registry declares categories from the widened set, and 2d was already done
in Step 1. The suite is 52 tests, all offline. Still stubbed for Step 5:
`_build_scrapers()` returns `[]`, so every stage is a no-op until scrapers exist.

### 2a. `company` becomes a registry, not a `Literal` — **done**

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

As built, `sources.py` also exposes `SOURCES` (flat, keyed by source key), the
`company_keys()` / `sources_for()` / `companies_in_group()` / `company_label()` /
`source_label()` helpers, and a `_validate()` that runs at import so a malformed
registry fails immediately instead of silently dropping a company page.

Two live-probe findings landed with it (both recorded in `sources.md`): the Azure
allowlist is seeded from the real feed rather than guessed, and **a `<category>`
allowlist cannot filter the press feeds** — every TechCrunch and Ars item carries
the `AI` tag, including the off-topic ones, so the press allowlist is only a
sanity gate and `daily_cap` does the real work.

### 2b. `storage/models.py` — add `source`, drop `ProductUpdate` — **done**

Built as specified, with one deviation: `source` defaults to `""` rather than
being required, so this change lands green on its own — `db.py` does not persist
the column until 2c. Step 5's scrapers always set it. `company` dropped its
`Literal["cribl", "ocient", "xsiam"]` for a plain `str`; validating it against
the registry would make `models` import `sources`, which imports `models`.


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

### 2c. `storage/db.py` — one new column — **done**

Add a `source` column: `CREATE TABLE`, `INSERT`, `SELECT`, and the row→model
mapping. Drop `vec_id` from all four. Greenfield DB, so **no migration** — get
the schema right once.

Also added `idx_source`, and `source` to the upsert's `DO UPDATE SET` so a
corrected source key overwrites on re-scrape (`first_scraped_at` still doesn't).
The empty `data/ai_digest.db` left over from Step 1 was deleted and recreated
rather than migrated — `CREATE TABLE IF NOT EXISTS` would not have added the
column to it.

### 2d. `config.py` — remove retrieval settings — **done in Step 1**

Deleted: `openrouter_embedding_model`, `embedding_dimensions`,
`openrouter_rag_model`, `ollama_rag_model`, `max_source_text_chars`,
`search_score_threshold`, `rag_chunk_size_chars`, `rag_chunk_overlap_chars`.

Kept `summarizer_content_chars` — that one feeds the summarizer, not embeddings.

Changed: `sqlite_db_path` → `data/ai_digest.db`, `discord_notify_method` default
→ `webhook` (the `hermes` method is removed entirely — see Step 8).

The DB lives **inside the project**, not in `/opt/digest`. That shared setgid
directory existed in `product-update-digest` only so the `hermes` account could
read the DB; with no second account there is nothing to share, so the whole tree
stays mode 700 under one user. `data/` is gitignored and `ArticleDB` creates it
on first run. The cron log moves to `logs/last_run.log` for the same reason.

Still open: `github_repo` default is `dummy/dummy` — harmless, since `.env` sets
it, but change it if a real default is ever wanted.

### 2e. Widen the `category` Literal — **done**

Current: `blog | press_release | product | release_notes`. AI sources need
`research` (Anthropic, DeepMind), `engineering` (Anthropic), and `news`
(OpenAI, press). Final set:

`blog | research | engineering | news | press_release | product | release_notes`

Landed **before 2a**, because the 2a registry declares `research`/`news`
categories that have to exist first. `Category` is now declared once in
`storage/models.py` (with a `CATEGORIES` tuple beside it); `scrapers/base.py`
re-exports it and `main.py`'s `--category` choices read from it, so the Literal
no longer appears in four places.

### 2f. Rewrite the summarizer prompt

`summarizer/__init__.py` was ported verbatim in Step 1, so its system prompt
still reads *"a product intelligence analyst tracking two data-infrastructure
companies (Cribl and Ocient)"*. Left alone, every summary this pipeline produces
is written for the wrong domain. Three changes:

1. **System prompt** — replace the Cribl/Ocient persona with an AI-industry one:
   an analyst tracking AI labs and AI press coverage for software engineers and
   architects. Keep the existing output contract verbatim (markdown only, no
   commentary, note when content was too long to summarize fully).
2. **`_CATEGORY_INSTRUCTIONS`** — add the three categories from 2e. The dict is
   keyed by category and falls back to a generic instruction, so a missing key
   fails silently rather than loudly:
   - `research` — the claim or result, the method in one clause, and what is new
     versus prior work. Preserve model, benchmark, and dataset names exactly.
   - `engineering` — the system or technique described, the problem it solves,
     and any concrete numbers (latency, cost, scale).
   - `news` — what was announced, by whom, and the stated impact. For press
     sources, attribute claims to the outlet rather than asserting them.
3. **Drop `release_notes`** from `_CATEGORY_INSTRUCTIONS` and from the
   `release_notes` branch of `_length_guidance()` only if 2e drops the category.
   The plan keeps it in the Literal, so leave both in place — no AI source emits
   it today, but nothing breaks by keeping it.

Prompt wording is not a mechanical port; write it fresh against the category
list 2e settles on, then eyeball the output of `--stage summarize --limit 1`
against a real Anthropic research page and a TechCrunch item before moving on.

**Done.** `release_notes` was rewritten rather than left alone — its wording was
Ocient-docs-specific (SQL statement names, "the vendor's docs site"), which is
as wrong for this domain as the system prompt was. The prompt is also now handed
`company_label()` rather than the raw key, so it sees "Ars Technica", not
"arstechnica" — that matters for the `news` attribution instruction.

Verified against live pages (no scrapers yet, so via a throwaway harness rather
than `--stage summarize`): Anthropic's `/research/discovering-cryptographic-
weaknesses` and a TechCrunch item, both on `google/gemma-3-27b-it`. The research
summary kept model, benchmark and algorithm names exact and preserved the post's
hedges; the news summary opened "TechCrunch reports that…" rather than asserting
the claim.

### 2g. Longer summaries for firewall-blocked companies — **done**

Some of these sites are blocked by a work firewall, so for them the summary is
not a preview of the article — it is the only thing the reader will ever see. A
2-3 sentence summary that says "the post explains the new approach" is useless
when you cannot click through. Those companies get substantially longer,
more self-contained summaries.

**Config.** A new setting in `config.py`, defaulting to the three known-blocked
companies:

```python
long_summary_companies: Annotated[list[str], NoDecode] = ["anthropic", "openai", "mistral"]

@field_validator("long_summary_companies", mode="before")
@classmethod
def _split_csv(cls, v: str | list[str]) -> list[str]:
    return [s.strip().lower() for s in v.split(",") if s.strip()] if isinstance(v, str) else v
```

The `NoDecode` + validator is load-bearing: pydantic-settings JSON-decodes
`list` fields from env by default, so a bare `LONG_SUMMARY_COMPANIES=anthropic,openai`
raises a parse error without it. Every other value in `.env.example` is a plain
scalar, so CSV is the consistent choice over requiring JSON. Values are company
keys (`Company.key` from 2a), not source keys — the distinction matters for
Google, where one key covers both DeepMind and the AI blog.

**Behavior.** `_length_guidance()` takes a `long: bool`. `Summarizer.summarize()`
computes `long = page.company in settings.long_summary_companies`.

**Revised 2026-08-01 (user):** the long branch is *not* a bigger bullet list. It
targets **~1000 characters of prose** — `long_summary_target_chars`, a new
setting — because these summaries are read as the article, and a bullet list
reads as notes about an article. Bullets are permitted only where the source is
genuinely an enumeration. The guidance also forbids "consult the original for
details", which is worthless when the original is blocked. Thin articles aren't
padded to the full target: under 500 chars of source targets a quarter of it,
under 2000 a half.

Also raise the input budget for these companies, not just the output length —
a longer summary built from the same truncated `summarizer_content_chars` just
pads. Add `summarizer_content_chars_long` (suggest 2x) and select on the same
flag.

**Cost.** These three are ~3 articles/day combined, so doubling their output is
noise against the ~$0.16/month in Step 6.

**Verify:** unit tests that a company in the list gets the longer guidance and
one outside it does not, and that `LONG_SUMMARY_COMPANIES=a,b` parses to
`["a", "b"]` rather than raising. Both run offline — no LLM call needed, since
`_length_guidance()` is a pure function.

Built as specified (`summarizer_content_chars_long` defaults to 30000, 2x). The
long branch also states the reason in the prompt — *"the reader may not be able
to open the original, so the summary must stand on its own"* — since a length
target alone doesn't tell the model the summary is a replacement, not a preview.

Confirmed on the live Anthropic page from 2f: 24,905 chars of source produced a
1,236-char prose summary in two paragraphs, keeping the model, benchmark and
algorithm names, the cost and speedup figures, and the "does not affect
production systems" caveat.

**Verify (Step 2 overall):** unit tests for registry invariants (source keys
unique, every `Source.company` present in `COMPANIES`, every `Company.sources`
non-empty), and that `_CATEGORY_INSTRUCTIONS` has an entry for every category in
the Literal — that test is what stops a future category from silently taking the
fallback.

## Step 3 — `FeedScraper` base class — **done**

Completed 2026-08-01. `scrapers/feed.py` is ~330 lines and covers all nine feed
sources; 33 offline tests in `tests/test_feed_scraper.py`, suite now 87.

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

### As built

Everything above landed as specified. What the plan didn't say:

- **`parse_feed()` is a module-level pure function**, not a method, so the six
  parser tests need no scraper, no HTTP and no registry entry. It walks
  `root.iter()` for `item`/`entry` and reads children by local name, so RSS 2.0,
  RSS 1.0/RDF and Atom all land in one `FeedEntry` without dialect branching.
- **`description`/`summary` are deliberately not treated as content.** Only
  `content:encoded` and Atom `<content>` populate `content_html`. TechCrunch's
  `description` is a truncated excerpt, and accepting it would have meant
  silently summarizing a teaser had anyone ever set `content_in_feed=True` on it.
  A `content_in_feed` source whose item lacks `content:encoded` falls back to
  fetching the page.
- **Pagination needs two stop conditions, not one.** Besides "the cutoff is
  covered", a feed that ignores `?paged=N` and re-serves page 1 would otherwise
  walk to the cap on every run. Repeated URLs are detected and stop the loop.
  The cap itself is a new setting, `feed_max_pages` (default 10).
- **`pre_check` returns `None`, not `True`, when the feed date is newer.** The
  plan only specified the `False` case. A bumped `pubDate` is not proof the body
  changed, so it falls through to the content-hash compare — which still avoids
  the HEAD request, the point of the override.
- **`_extract_title`/`_extract_date` moved to `BaseScraper`** as
  `extract_title`/`extract_date`, beside the existing `extract_text`, rather than
  being copied into `feed.py`. Step 4 reuses them instead of re-porting Cribl's.
  The JSON-LD branch also now handles a top-level array, which Cribl's did not.
- **`categorize(source, entry)` is an overridable hook**, defaulting to
  `source.category`. Nothing uses it yet; it exists because the live probe found
  OpenAI's tags are usable (below).
- `FeedScraper` exposes `sources`/`exclusions` as properties derived from the
  registry, which is what `main._scraper_infos()` and the index template read.
  It raises at construction for a company with no feed sources, so pointing it at
  Anthropic fails loudly rather than scraping nothing.

**Live probe, 2026-08-01.** All nine feeds were run through `parse_feed()`: every
one returned 200 and parsed, with a date on 100% of entries. `content:encoded` is
present on Ars (20/20), AWS, Azure and MS Source; absent on TechCrunch, OpenAI,
DeepMind, Google AI and Mistral. DeepMind and Mistral carry no `<category>`
elements, as `sources.md` said.

One correction fell out of it: **OpenAI's `<category>` elements are not empty.**
The 2026-07-31 probe that recorded all 958 as blank was mishandling CDATA. They
carry 20 real terms (`Research` 194, `Product` 145, `Engineering` 17, …).
Nothing changes today — OpenAI has no `include_categories` — but `sources.md`
and the registry comment were corrected, and Step 5 can now categorize OpenAI
items off the tags via `categorize()` instead of flattening everything to `news`.

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
| `openai.py` | `openai` | `news/rss.xml` | Trivial — no filtering. Optionally override `categorize()` to map the feed's real tags (`Research`, `Engineering`, `Product`) onto categories; see the Step 3 correction. |
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

## Step 7a — Daily overview ("summary of summaries")

A single ~500-word synthesis of the day's **non-press** articles, rendered at the
top of `index.html`. It answers "what actually happened in AI today?" without
making the reader assemble it from a dozen cards.

Numbered `7a` rather than `8` to avoid renumbering Steps 8–10, which existing
commits and notes already reference. It depends on Step 7 (the index template)
and Step 6.2 (the `vendor`/`press` split), so it lands after both.

### Scope — vendor only

Input is every article from companies whose `Company.group == "vendor"`
(`companies_in_group("vendor")` from Step 2a). Press is excluded because it is
~20 of the ~26 articles/day: include it and the overview becomes a TechCrunch
recap with the labs as a footnote. Make it `overview_include_press: bool = False`
rather than hardcoding the exclusion, so the choice is reversible without a code
change.

### Input is the summaries, and only the summaries

The day's `ArticleRecord.summary` values are the entire input. `raw_text` is
never read here — this step is strictly a summary of summaries, so it does not
re-fetch, re-truncate, or re-reason over article bodies, and
`summarizer_content_chars` is irrelevant to it.

That keeps the call small: roughly 10 vendor articles × ~1000 chars ≈ 10k chars
≈ 3k tokens in, ~700 out, so about **$0.0005/day** — noise against the ~$0.16/mo
in Step 6.

**No separate model.** It uses `openrouter_summarization_model` like every other
call, with the same `--stage` override. Condensing text that is already condensed
is not a harder task than writing those summaries was, so there is nothing here
to pay more for.

### "The day's" articles means first-seen, not published

Select on `date(first_scraped_at) == today` (UTC), not `published_date`: a
backfilled three-day-old Anthropic post is still new *to this digest* on the day
it first appears, and that is what the reader wants summarized. Needs one new
query in `storage/db.py` — `articles_first_seen_on(day, companies)`, returning
`ok` records with a non-empty summary.

### Persist it — a new table, not a re-computation

```sql
CREATE TABLE IF NOT EXISTS daily_overview (
    day            TEXT PRIMARY KEY,   -- YYYY-MM-DD, UTC
    text           TEXT NOT NULL,
    article_count  INTEGER NOT NULL,
    source_hash    TEXT NOT NULL,      -- sha256 of the sorted normalized_urls
    model          TEXT NOT NULL,
    generated_at   TEXT NOT NULL
);
```

`source_hash` is the dedup key, in the same spirit as `content_hash`: regenerate
only when the day's article set actually changed. Without it, every `--publish`,
every cron retry, and every manual re-render bills another call and silently
changes the text under a reader who already read it. Keeping history also gives
the template something to fall back on (below), and makes a future
`/overview/2026-08-01.html` archive page a template change rather than a schema
change.

### Pipeline placement

Generated in the full run **after** summarization and **before** publish, so the
overview covers the articles that same run just wrote. Add a matching dry-run
stage — `--stage overview` — which generates from the DB and writes the preview
without pushing, bringing the stage list to four: `scrape`, `summarize`,
`overview`, `render`. Guard it the same way as `--stage summarize`: it is the
other stage that spends money.

### Rendering

`index.html.j2` gets a block above the article cards, before the vendor section:
the heading ("Today in AI"), the date, the article count and a "vendor sources
only" note, then the text through the existing `markdown` filter.

Three cases the template must handle, because a daily page is read on quiet days
too:

1. **Today's overview exists** — render it.
2. **It doesn't, but an older one does** — render the most recent, labelled with
   its own date ("Overview for 2026-07-31"). Never present a stale overview as
   today's.
3. **None exists** — omit the block entirely. No empty box, no placeholder.

### Prompt

A second template in `summarizer/__init__.py` (e.g. `summarize_day()`), reusing
the AI-analyst system prompt from 2f. Instructions worth stating explicitly:

- Target `overview_target_words: int = 500`, as prose in three to five
  paragraphs. **No bullet list and no per-company headings** — the cards below
  are already the itemized view; this is the part that reads like a person wrote
  it.
- **Organize by theme, not by company.** Two labs shipping competing agent
  features on the same day is the story; two paragraphs that each start "OpenAI
  announced…" is not.
- Lead with the single most consequential item of the day.
- Keep model names, version numbers and figures exact; don't introduce claims
  absent from the summaries.
- Say so plainly when it was a quiet day rather than inflating three minor posts
  into 500 words. The target is a ceiling, not a quota.

### Edge cases

- **Zero vendor articles** — skip the LLM call entirely, write no row, let the
  template fall back to case 2. A cron run on a quiet Sunday should cost nothing.
- **One or two articles** — still generate; the "quiet day" instruction handles
  the length.
- **A backfill run** (`--since 30d`) — this would generate one overview for the
  backfill day covering hundreds of articles. Cap the input at the most recent
  N records (`overview_max_articles: int = 40`) so a backfill can't produce a
  60k-char prompt.

### Not in scope

The Discord notification stays counts-only (Step 8). Posting the overview text
there is a reasonable follow-up, but it is a separate decision about how noisy
that webhook should be.

**Verify:** offline tests with a stubbed chain — press companies excluded from
the input; selection is by `first_scraped_at`, not `published_date`; an unchanged
`source_hash` does **not** re-call the LLM; the template renders each of the
three cases. Then `--stage overview` against a real day's summaries to eyeball
the prose before it goes on the front page.

## Step 8 — Pipeline stages, notifier

### Stages drop from four to three

`main.py`'s stage machinery loses `vector` entirely:

| Stage | What it does |
|---|---|
| `scrape` | Fetch pages, cache text in SQLite, write `data/dry-run/` preview |
| `summarize` | Call the LLM on cached articles, write summary preview |
| `render` | Render the full site from the DB to `data/dry-run/` |

Step 7a adds a fourth, `overview` — the count returns to four, but the dropped
stage and the added one are unrelated.

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
- `test_overview.py` — Step 7a: press excluded, day selected by
  `first_scraped_at`, unchanged `source_hash` skips the LLM call, and the three
  template cases.
- One fixture-based test per company scraper.

Fixtures: save real responses from all ten sources **now**, before they drift.
All ten were reachable on 2026-07-31.

Everything stays offline — `respx` for HTTP, in-memory SQLite. With sqlite-vec
gone the suite has one less native dependency.

## Step 10 — Deployment

`INSTALL.md` is already adapted and is nearly correct. Remaining work:

1. ~~Apply the Step 1 cleanup items (Hermes reference, DB filename
   consistency).~~ **Done in Step 1.** `INSTALL.md` also dropped the
   `/opt/digest` group setup — the DB is now `data/ai_digest.db` inside the
   project and the cron log is `logs/last_run.log`, so the install is a single
   mode-700 tree with no `sudo` steps beyond installing `uv`.
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
