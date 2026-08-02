# ai-digest

**[View the live digest →](https://mrajcok.github.io/ai-digest/)**

A daily cron job that scrapes news and blog posts from sites mentioned in [docs/sources.md](docs/sources.md).

## What it does

1. **Scrapes** websites for new or changed content
2. **Deduplicates** using SQLite — skips unchanged content via URL tracking and SHA-256 content hashing
3. **Summarizes** each new item using a configurable LLM (default: `google/gemma-3-27b-it` via OpenRouter)
4. **Publishes** a static HTML digest to GitHub Pages

## Requirements

- Python 3.13
- [uv](https://docs.astral.sh/uv/) — fast Python package manager
- [OpenRouter](https://openrouter.ai) API key — the only LLM backend
- GitHub personal access token with `repo` scope (for pushing to GitHub Pages)


## Setup

```bash
# install uv (once per machine)
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/mrajcok/ai-digest.git
cd ai-digest
make sync
cp .env.example .env
# edit .env with your API keys and config
```

`make sync` runs `uv sync --extra dev`, which creates the venv and installs all dependencies. No separate services required.

### Optional setup

**[Context Hub](https://github.com/andrewyng/context-hub) (`chub`)** — a CLI
that serves curated, versioned API docs to coding agents so they don't
hallucinate library APIs. `CLAUDE.md`'s Rules section tells agents to consult
it before writing against an external library, so install it if you're using an
agent in this repo. No API key or login required.

```bash
npm install -g @aisuite/chub   # goes into your nvm Node dir — no sudo needed
```

Verify it works:

```bash
chub search react              # should list react/react, react-dom, …
chub get react/react --lang js # should print doc content, not an error
```

`--lang` is required whenever a doc has language variants — `chub get react/react`
alone errors with "Multiple languages available". Other useful commands:
`chub search` with no query lists everything; `chub annotate <id> <note>` saves a
local note for later sessions.

**Coverage caveat:** it's curated, so not everything is present. Of this
project's stack, TBD are
covered, but TBD return no results — Context7 below
fills exactly that gap.

Optionally install the companion Claude Code skill, so agents reach for `chub`
without being told:

```bash
mkdir -p ~/.claude/skills/get-api-docs
curl -o ~/.claude/skills/get-api-docs/SKILL.md \
  https://raw.githubusercontent.com/andrewyng/context-hub/main/cli/skills/get-api-docs/SKILL.md
```

> Installed globally, `chub` is tied to the Node version that installed it
> (`~/.nvm/versions/node/v24.*/bin/chub`). If you later switch Node major
> versions with nvm, re-run the install.

**[Context7](https://context7.com) — for TBD docs only.** These
are the frameworks `chub` doesn't index, so Context7 covers the gap and
nothing more (`CLAUDE.md`'s Rules section says as much, to keep agents from
burning calls on libraries `chub` already has).

It's already configured as a **project-scoped** MCP server in `.mcp.json`
(committed), so there's nothing to install — Claude Code will ask you to approve
it on first run in this repo. Verify with:

```bash
claude mcp list        # context7 should be listed and connected once approved
```

**[RTK](https://github.com/rtk-ai/rtk)** — a CLI proxy that compresses verbose
command output (test runs, git, linters, etc.) before an AI coding agent
reads it, to cut token usage. Installed **project-scoped** (no `-g`), so it
only touches this repo — it does not patch global Claude Code config or
install any automatic hook that rewrites your commands.

```bash
curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"   # add to your shell profile to persist

rtk init   # project-scoped: adds an RTK section to CLAUDE.md + .rtk/filters.toml
           # (no -g, so no global config touched, no auto-rewrite hook installed)
```

This appends an "RTK" usage-instructions block to `CLAUDE.md` (agents are told
to prefix commands like `rtk git status` / `rtk test <cmd>` to get compact
output) and creates `.rtk/filters.toml` (empty project-filter template, safe
to commit). Review the `CLAUDE.md` diff before committing it — this becomes
a standing instruction for every future agent session in this repo, not just
a local convenience. Remove with `rtk init --uninstall`.

#### RTK CLI reference

```bash
rtk gain                # view token savings statistics
rtk gain --history      # command history with savings
rtk discover            # analyze past sessions for missed RTK usage
rtk proxy <cmd>         # run a command through unfiltered, for debugging
```
## Configuration

All configuration is via environment variables (`.env` file locally, system env in production):

| Variable | Description |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `OPENROUTER_BASE_URL` | OpenRouter API base URL (default: `https://openrouter.ai/api/v1`) |
| `OPENROUTER_SUMMARIZATION_MODEL` | LLM for summaries (default: `google/gemma-3-27b-it`) |
| `OPENROUTER_STAGE_SUMMARIZATION_MODEL` | LLM for `--stage summarize` (defaults to `OPENROUTER_SUMMARIZATION_MODEL`) |
| `SUMMARIZER_CONTENT_CHARS` | Chars of raw text sent to the summarization LLM (default: `15000`) |
| `LONG_SUMMARY_COMPANIES` | Comma-separated company keys whose sites are firewall-blocked; these get longer, self-contained summaries (default: `anthropic,openai,mistral`) |
| `SUMMARIZER_CONTENT_CHARS_LONG` | Chars of raw text sent for those companies (default: `30000`) |
| `LONG_SUMMARY_TARGET_CHARS` | Target summary length for those companies, in output characters — prose, not bullets (default: `1000`) |
| `SQLITE_DB_PATH` | Path to SQLite database (default: `data/ai_digest.db`, inside the project; `data/` is gitignored) |
| `GITHUB_TOKEN` | GitHub PAT for pushing to gh-pages |
| `GITHUB_REPO` | Target GitHub repo for Pages (e.g., `username/ai-digest`) |
| `GITHUB_PAGES_BRANCH` | Branch to publish to (default: `gh-pages`) |
| `LOG_LEVEL` | Root log level (default: `INFO`) |
| `MAX_ARTICLE_AGE_DAYS` | How far back to index articles (code default: `30`; `.env.example` ships `2` for daily runs) |
| `FEED_MAX_PAGES` | Max `?paged=N` pages walked per feed; only reached during a `--since` backfill (default: `10`) |
| `INDEX_PER_COMPANY` | Articles per vendor company shown on the index page (default: `3`) |
| `INDEX_PER_COMPANY_PRESS` | Articles per press company shown on the index page (default: `1`) |
| `INDEX_PAGE_LIMIT` | Unused; kept for backward compatibility (default: `10`) |
| `COMPANY_PAGE_LIMIT` | Max articles shown on each company page on GitHub Pages (default: `30`) |
| `MAX_API_RETRIES` | Max retry attempts for LLM API calls (default: `5`) |
| `DISCORD_NOTIFY` | Post a per-company summary to Discord after each run (default: `true`; set `false` to disable) |
| `DISCORD_NOTIFY_METHOD` | Only `webhook` is supported (default: `webhook`) |
| `DISCORD_WEBHOOK_URL` | Discord webhook URL; required when `DISCORD_NOTIFY_METHOD=webhook` |

### Discord run-completion notification

After each daily run the digest posts a per-company summary to Discord (**on by default**).

Create a webhook in your Discord channel settings and set `DISCORD_WEBHOOK_URL`. This is a
one-way notification — there is no bot and nothing to ask questions of.

The message looks like:

```
**Daily digest complete**
TBD
```

Notification errors are logged as warnings and never interrupt the pipeline. Set `DISCORD_NOTIFY=false` to disable.

### Model recommendations
As of 2026-05-30, evaluated by Claude Sonnet 4.6.

**Summarization** (`OPENROUTER_SUMMARIZATION_MODEL` / `OPENROUTER_STAGE_SUMMARIZATION_MODEL`):

| Model | Input | Output | Notes |
|---|---|---|---|
| `google/gemma-4-26b-a4b-it:free` | free | free | Rate-limited; good for `--stage summarize` testing |
| `google/gemma-3-12b-it` | $0.04/M | $0.13/M | Budget pick; solid quality |
| `google/gemma-3-27b-it` | $0.08/M | $0.16/M | **Best bang-for-buck; default** |
| `deepseek/deepseek-v4-flash` | $0.10/M | $0.20/M | Fast; 1M context window |
| `deepseek/deepseek-v3.2` | $0.25/M | $0.38/M | Higher quality step-up |
| `anthropic/claude-haiku-4-5` | ~$1/M | ~$5/M | Reference point; 6–30× pricier than Gemma |

**Expected cost with `google/gemma-3-27b-it`:** a typical article (2,000 token input, 300 token output) costs roughly $0.0002.

## Usage

```bash
uv run digest                   # full pipeline: scrape → summarize → publish
uv run digest --site anthropic  # run only the Anthropic scraper (default: all)
uv run digest --publish         # rebuild full site from DB and push to GitHub Pages

# Count discoverable URLs without scraping or writing to the DB
uv run digest --count                          # all companies, default 30-day window
uv run digest --count --site openai            # one company

# Override the article age cutoff for any command (overrides MAX_ARTICLE_AGE_DAYS)
uv run digest --since 2026-01-01               # full pipeline back to Jan 1
uv run digest --stage scrape --since 2026-01-01 --site microsoft   # backfill scrape
```

## Tests

Tests run against a **dedicated database** (`TBD` by default, or
`DB_DATABASE_TEST`) so dev/seed data can never contaminate them. Create it once

## Code quality

[Ruff](https://docs.astral.sh/ruff/) handles linting. Config lives in
`pyproject.toml` under `[tool.ruff]`.

```bash
make lint      # ruff check .          — must be clean before committing
make fix       # ruff check --fix .    — auto-fix imports, sorting, simple rules
make format    # ruff format .         — opt-in, NOT part of `make lint`
```

Enabled rule groups: `F` (pyflakes), `E`/`W` (pycodestyle), `I` (isort),
`UP` (pyupgrade), `B` (bugbear), `C4`, `SIM`, `RUF`, plus two chosen to enforce
project conventions:

- **`G`** — `G004` rejects f-strings in logging calls, keeping log formatting lazy.
- **`DTZ`** — `DTZ005` rejects `datetime.now()` without a timezone; the pipeline is UTC-only.

Line length is 120, matching the existing style. Formatting is deliberately not
enforced so that code ported from `product-update-digest` doesn't arrive as a
whole-file reformat diff.

**Ruff is installed twice, on purpose:** as a dev dependency (used by
`make lint`) and as a global tool via `uv tool install ruff@0.14.14`, which is
what `rtk ruff` invokes since it resolves `ruff` from `PATH`. Keep the two
versions in sync when bumping.

## Production 

TBD
