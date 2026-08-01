# Full Installation Guide

Complete, ordered steps to install ai-digest on a VPS.

## Prerequisites

- Ubuntu 24.04 VPS
- `sudo` access from your regular user account

## Security Architecture

Everything lives in one mode-700 directory owned by your user:

```
/home/$USER/ai-digest/   mode 700  $USER:$USER
    Source code, .env (API keys, GitHub token)
    .venv/                — Python 3.13 venv
    data/ai_digest.db     — SQLite store (gitignored)
    logs/agent.log        — rotating application log
    logs/last_run.log     — overwritten on each cron run
```

There is no shared group or `/opt` directory. `product-update-digest` needed one
so a second account could read its DB; this digest is single-account, so the
whole tree stays under mode 700 and nothing outside it is readable.

## Step 1 — Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Add to `~/.bashrc` or `~/.profile` if not already added by the installer:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Verify:

```bash
uv --version
```

## Step 2 — Clone the repo and lock it down

```bash
git clone https://github.com/mrajcok/ai-digest.git ~/ai-digest
chmod 700 ~/ai-digest
cd ~/ai-digest
cp .env.example .env
chmod 600 .env
```

## Step 3 — Fill in .env

Edit `~/ai-digest/.env`. Required values:

```
SQLITE_DB_PATH=data/ai_digest.db
OPENROUTER_API_KEY=<your OpenRouter key>
GITHUB_TOKEN=<GitHub PAT with repo + pages write scope>
GITHUB_REPO=<your-github-username>/ai-digest
```

All other values can remain at their defaults from `.env.example`.

## Step 4 — Install project dependencies

```bash
cd ~/ai-digest
make sync
```

This creates `.venv` inside the project directory (inside the mode 700 tree).

## Step 5 — Set up the daily cron job

```bash
crontab -e
```

Add (replacing `$HOME` with your actual home directory path, e.g. `/home/yourname`):

```
0 6 * * * cd $HOME/ai-digest && $HOME/.local/bin/uv run digest > logs/last_run.log 2>&1
```

Uses the full path to `uv` to avoid PATH issues in cron's minimal environment.
Output overwrites `logs/last_run.log` on each run; the `cd` makes that path
resolve inside the project.

## Step 6 — GitHub Pages — First-Time Setup

The digest pipeline pushes the generated HTML to a `gh-pages` branch. Create it once
as an orphan branch:

```bash
git clone https://<token>@github.com/<your-repo>.git /tmp/pages-init
cd /tmp/pages-init
git checkout --orphan gh-pages
git rm -rf .
echo "<h1>Coming soon</h1>" > index.html
git add index.html
git commit -m "Init gh-pages"
git push origin gh-pages
rm -rf /tmp/pages-init
```

Then enable GitHub Pages in the repo settings → **Pages** → Source: `gh-pages` branch,
`/ (root)`.

## Step 7 — Run the pipeline once manually

```bash
cd ~/ai-digest
uv run digest --site anthropic   # one vendor, full pipeline — quick sanity check
uv run digest                    # full run
```

Verify the database was created:

```bash
ls -la ~/ai-digest/data/ai_digest.db
# expected: -rw-r--r-- $USER $USER ...
```

`ArticleDB` creates `data/` on first run if it does not exist.

## Verification Checklist

```bash
# 1. DB created inside the project
ls -la ~/ai-digest/data/ai_digest.db

# 2. Cron log after 6 am
cat ~/ai-digest/logs/last_run.log
```

## Logs

Two log destinations, both under `logs/` in the project directory:

- **`logs/last_run.log`** — overwritten on each cron run (stdout + stderr)
- **`logs/agent.log`** — rotating file written by `setup_logging()` (5 MB max, 3 backups); persists across runs

```bash
tail -f ~/ai-digest/logs/last_run.log
tail -f ~/ai-digest/logs/agent.log
```

Both are gitignored (`logs/*.log`).

## Updating

```bash
cd ~/ai-digest
git pull
make sync          # reinstall deps if pyproject.toml/uv.lock changed
```

No database migrations needed — `CREATE TABLE IF NOT EXISTS` is idempotent. This is a
greenfield schema with no migration path; if the schema changes before first
deployment, delete `data/ai_digest.db` and re-run rather than migrating.
