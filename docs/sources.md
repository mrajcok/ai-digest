# ai-digest — source list

Finalized 2026-07-31. Every URL below was probed live on that date; status and
volume figures are measured, not assumed.

Companies own one or more **sources**. A source is a single feed or sitemap.
`company` is the grouping key used for GitHub Pages sections and for the
`company` filter in semantic search / RAG.

## Sources

| Company | Source | URL | Type | Filtering | Measured volume |
|---|---|---|---|---|---|
| `anthropic` | news + research + engineering | `https://www.anthropic.com/sitemap.xml` | sitemap | path prefix: `/news/`, `/research/`, `/engineering/` | 429 URLs total; ~1–2/day |
| `openai` | news | `https://openai.com/news/rss.xml` | RSS | none | 1104 items to 2015; ~1–2/day |
| `google` | Google AI blog | `https://blog.google/technology/ai/rss/` | RSS | none (topic-scoped) | 20 items / ~2 mo; ~0.3/day |
| `google` | DeepMind blog | `https://deepmind.google/blog/rss.xml` | RSS | none | 100 items / ~9 mo; ~0.4/day |
| `microsoft` | Microsoft Source AI | `https://news.microsoft.com/source/topics/ai/feed/` | RSS | none (topic-scoped) | 10 items / ~80 d; ~0.1/day |
| `microsoft` | Azure blog | `https://azure.microsoft.com/en-us/blog/feed/` | RSS | **yes** — `<category>` must match AI terms | 10 items / ~28 d; ~0.35/day pre-filter |
| `aws` | AWS Machine Learning blog | `https://aws.amazon.com/blogs/machine-learning/feed/` | RSS | none (fully AI-scoped) | 20 items / 8 d; ~2.5/day |
| `mistral` | Mistral blog | `https://mistral.ai/rss.xml` | RSS | none | 78 items since 2023; ~0.1/day |
| `techcrunch` | TechCrunch AI | `https://techcrunch.com/category/artificial-intelligence/feed/` | RSS | **recommended** — see note | 20 items / 31 h; **~15/day** |
| `arstechnica` | Ars Technica AI | `https://arstechnica.com/ai/feed/` | RSS | **recommended** — see note | 20 items / 4 d; ~5/day |

Estimated total: **~26 articles/day**, dominated by the two press sources.

## Per-source notes

### Anthropic — sitemap only
There is no RSS feed. `www.anthropic.com/rss.xml` and `/news/rss.xml` both 404.
The sitemap is a flat `urlset` with genuine per-URL `lastmod` timestamps (not
reset daily the way Palo Alto's is), so the `MAX_ARTICLE_AGE_DAYS` pre-filter
works directly off `lastmod`. This is structurally the same as the Cribl
scraper in `product-update-digest`.

Suggested category mapping: `/news/` → `news`, `/research/` → `research`,
`/engineering/` → `engineering`. Exclude `/legal/`, `/careers/`, `/events/`.

### OpenAI — the category tags are usable after all
`openai.com/blog/rss.xml` redirects to the same feed. `pubDate` is reliable.

**Corrected 2026-08-01.** The 2026-07-31 probe recorded all 958 `<category>`
elements as empty; that was a CDATA-handling artifact of the probe, not the
feed. Re-read through `parse_feed()`, all 958 carry real values across 20
distinct terms:

```
Company 196 · Research 194 · Product 145 · Global Affairs 102 · Story 66
Safety & Alignment 61 · Safety 47 · OpenAI Academy 30 · Publication 29
Security 21 · Engineering 17 · API 14 · … (AI Adoption, Release, Startup, …)
```

No **filtering** is needed — the whole feed is AI by definition — but the tags
are good enough to **categorize** on: `Research`/`Publication` → `research`,
`Engineering`/`API` → `engineering`, `Product`/`Release` → `product`, everything
else → `news`. `FeedScraper.categorize()` is the hook for that; wiring it up is
Step 5's call.

### Google — two sources, no filtering
`blog.google/technology/ai/rss/` is already topic-scoped, which resolves the
open question about how hard Google would be to filter: it isn't. DeepMind is
a separate feed with no overlap. Note DeepMind's feed has no `<category>`
elements — categorize by source, not by tag.

`cloud.google.com/blog/rss` and `.../products/ai-machine-learning/rss` return
zero entries; do not use them.

### Microsoft — Azure needs real filtering
`azure.microsoft.com/en-us/blog/topics/artificial-intelligence/feed/` **404s**,
so there is no AI-scoped Azure feed. The main Azure feed carries 39 distinct
`<category>` values; filter on AI-related ones (Foundry, AI + machine learning,
Azure OpenAI, Copilot, etc.). This is the only source in the list requiring
non-trivial filtering logic.

The allowlist now lives in `sources.py` as `_AZURE_AI_CATEGORIES`, seeded from
the live feed on 2026-08-01 — 6 of 10 items tagged `AI + machine learning`, and
the remainder split across `Management and governance`, `Databases`, `Internet
of things` and similar non-AI values. Values must match the feed text exactly.

`news.microsoft.com/source/topics/ai/feed/` is topic-scoped and needs none, but
is low volume and PR-flavored.

### AWS — ML blog only
`aws.amazon.com/blogs/machine-learning/feed/` is fully AI-scoped and is the
single highest-volume vendor source.

Deliberately excluded: `/blogs/aws/feed/` is mostly "AWS Weekly Roundup" posts,
and `about-aws/whats-new/recent/feed/` is ~90% non-AI infrastructure
announcements (Aurora regions, Lambda runtimes, Direct Connect). The
`/blogs/aws/tag/generative-ai/feed/` tag feed 404s.

### TechCrunch AI and Ars Technica AI — press, not vendor
These behave differently from the vendor sources and are worth treating as a
distinct group in the UI:

- **Volume.** TechCrunch alone is ~15 articles/day — more than every vendor
  source combined. Without a cap it will dominate the index page.
- **Off-topic bleed.** Both category feeds include tangential items (e.g.
  TechCrunch's "India is starting to pay for apps", Ars's Reddit/DMCA piece).
  Both expose `<category>` tags (`AI`, `AI agents`, `Anthropic`, `Claude`,
  `ChatGPT`, `Artificial Intelligence`).

  **Re-probed 2026-08-01: a `<category>` allowlist cannot filter these feeds.**
  All 20 TechCrunch items and all 20 Ars items carried the `AI` tag, including
  the two off-topic examples above — the tag is applied by the category feed
  itself, so an allowlist keeps everything. The allowlist in `sources.py` is
  therefore a sanity gate (it catches a feed that stops being AI-scoped), not a
  volume control. Volume is handled by `daily_cap`, off-topic bleed by
  `exclude_patterns`, and neither can be tuned without watching real output —
  see plan Step 5/6.
- **Content extraction.** Ars includes full `content:encoded`, so the article
  body can be taken straight from the feed with no second HTTP fetch.
  TechCrunch does **not** — its items are summary-only and require fetching the
  article page.
- **Backfill.** Both feeds hold only ~20 items (TechCrunch ≈ 31 hours of
  coverage). `?paged=N` pagination works on both and is the only way to reach
  the 30-day window.

## Rejected sources

| Source | Reason |
|---|---|
| xAI | Cloudflare 403 on `x.ai/rss.xml` **and** `x.ai/sitemap.xml`. Needs a headless browser; incompatible with the httpx-only design. |
| Perplexity | Cloudflare 403 on RSS; `sitemap.xml` is a 733-byte stub. |
| Cohere | `cohere.com/blog/rss.xml` returns HTML, not XML. |
| DeepSeek | No public feed; `api.deepseek.com/news/rss` returns 401. |
| Meta | Excluded by choice. |
| Google Cloud blog | Both RSS URLs return zero entries. |
| Stability AI, Groq | Feed URLs 404. |
| Together AI | Feed parses but yields a single item. |

## Deferred (viable, not enabled)

| Source | URL | Why deferred |
|---|---|---|
| Hugging Face blog | `https://huggingface.co/blog/feed.xml` | 834 items, ~2–3/day, mixes official and community posts. High signal but adds volume. |
| NVIDIA blog | `https://blogs.nvidia.com/feed/` | ~1.2/day but heavily diluted with GeForce/gaming. 51 `<category>` tags make it filterable. |
| Google Research | `https://research.google/blog/rss/` | 100 items; mixes in quantum and health research. |
| Microsoft Research | `https://www.microsoft.com/en-us/research/feed/` | Currently near-100% AI, but only one `<category>` value so it can't be filtered if that changes. |
| Simon Willison | `https://simonwillison.net/atom/everything/` | Very high signal on LLMs, but not AI-scoped and is a personal blog. |
| The Verge AI | `https://www.theverge.com/rss/ai-artificial-intelligence/index.xml` | Atom, 10 entries. Redundant with TechCrunch/Ars. |
