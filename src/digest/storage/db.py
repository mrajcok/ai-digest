import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from digest.storage.models import ArticleRecord, DailyOverview, normalize_url

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scraped_articles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    url               TEXT NOT NULL UNIQUE,
    normalized_url    TEXT NOT NULL UNIQUE,
    company           TEXT NOT NULL,
    source            TEXT NOT NULL DEFAULT '',
    category          TEXT NOT NULL,
    title             TEXT,
    first_scraped_at  TEXT NOT NULL,
    last_scraped_at   TEXT NOT NULL,
    content_hash      TEXT,
    published_date    TEXT,
    summary           TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'ok'
);
CREATE INDEX IF NOT EXISTS idx_company       ON scraped_articles(company);
CREATE INDEX IF NOT EXISTS idx_source        ON scraped_articles(source);
CREATE INDEX IF NOT EXISTS idx_last_scraped  ON scraped_articles(last_scraped_at);
CREATE TABLE IF NOT EXISTS article_text (
    normalized_url  TEXT PRIMARY KEY,
    raw_text        TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    FOREIGN KEY (normalized_url) REFERENCES scraped_articles(normalized_url) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS daily_overview (
    day            TEXT PRIMARY KEY,
    text           TEXT NOT NULL,
    article_count  INTEGER NOT NULL,
    source_hash    TEXT NOT NULL,
    model          TEXT NOT NULL,
    generated_at   TEXT NOT NULL
);
"""

_UPSERT_SQL = """
INSERT INTO scraped_articles
    (url, normalized_url, company, source, category, title,
     first_scraped_at, last_scraped_at, content_hash,
     published_date, summary, status)
VALUES
    (:url, :normalized_url, :company, :source, :category, :title,
     :first_scraped_at, :last_scraped_at, :content_hash,
     :published_date, :summary, :status)
ON CONFLICT(normalized_url) DO UPDATE SET
    url             = excluded.url,
    source          = excluded.source,
    title           = excluded.title,
    last_scraped_at = excluded.last_scraped_at,
    content_hash    = excluded.content_hash,
    published_date  = excluded.published_date,
    summary         = excluded.summary,
    status          = excluded.status
    -- first_scraped_at intentionally preserved on conflict
"""


def _row_to_record(row: sqlite3.Row) -> ArticleRecord:
    return ArticleRecord(
        url=row["url"],
        normalized_url=row["normalized_url"],
        company=row["company"],
        source=row["source"] or "",
        category=row["category"],
        title=row["title"] or "",
        first_scraped_at=row["first_scraped_at"],
        last_scraped_at=row["last_scraped_at"],
        content_hash=row["content_hash"] or "",
        published_date=row["published_date"],
        summary=row["summary"] or "",
        status=row["status"],
    )


class ArticleDB:
    """SQLite-backed store for scrape history and deduplication state."""

    def __init__(self, db_path: str) -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("ArticleDB opened at %r", db_path)

    def get_by_url(self, url: str) -> ArticleRecord | None:
        """Look up by normalized URL so minor variations don't create duplicates."""
        nurl = normalize_url(url)
        row = self._conn.execute(
            "SELECT * FROM scraped_articles WHERE normalized_url = ?", (nurl,)
        ).fetchone()
        return _row_to_record(row) if row else None

    def upsert(self, record: ArticleRecord) -> None:
        self._conn.execute(_UPSERT_SQL, record.model_dump())
        self._conn.commit()
        logger.debug("DB upsert: url=%s status=%s", record.url, record.status)

    def save_text(self, normalized_url: str, raw_text: str) -> None:
        fetched_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO article_text (normalized_url, raw_text, fetched_at)
               VALUES (?, ?, ?)
               ON CONFLICT(normalized_url) DO UPDATE SET
                   raw_text   = excluded.raw_text,
                   fetched_at = excluded.fetched_at""",
            (normalized_url, raw_text, fetched_at),
        )
        self._conn.commit()

    def get_text(self, normalized_url: str) -> str | None:
        row = self._conn.execute(
            "SELECT raw_text FROM article_text WHERE normalized_url = ?", (normalized_url,)
        ).fetchone()
        return row["raw_text"] if row else None

    def delete_text(self, normalized_url: str) -> None:
        self._conn.execute("DELETE FROM article_text WHERE normalized_url = ?", (normalized_url,))
        self._conn.commit()

    def latest_article_with_text(self, company: str, category: str | None = None) -> ArticleRecord | None:
        """Return the most recently published article for company that has cached raw_text."""
        results = self.articles_with_text(company, category=category, limit=1)
        return results[0] if results else None

    def articles_with_text(
        self, company: str, category: str | None = None, limit: int | None = 1
    ) -> list[ArticleRecord]:
        """Return recently published articles for company that have cached raw_text (all if limit is None)."""
        clause = "WHERE sa.company = ?"
        params: list = [company]
        if category:
            clause += " AND sa.category = ?"
            params.append(category)
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(
            f"""SELECT sa.* FROM scraped_articles sa
               JOIN article_text at ON at.normalized_url = sa.normalized_url
               {clause}
               ORDER BY COALESCE(sa.published_date, sa.last_scraped_at) DESC
               {limit_clause}""",
            params,
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def get_all(self, company: str | None = None, category: str | None = None) -> list[ArticleRecord]:
        clauses, params = [], []
        if company:
            clauses.append("company = ?")
            params.append(company)
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM scraped_articles {where} ORDER BY last_scraped_at DESC",
            params,
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def articles_first_seen_on(
        self, day: str, companies: list[str], limit: int | None = None
    ) -> list[ArticleRecord]:
        """Return `ok` records with a summary whose first_scraped_at date is `day` (UTC).

        `first_scraped_at`, not `published_date` — a backfilled old post is still new
        to this digest on the day it first appears (docs/plan.md Step 7a).
        """
        if not companies:
            return []
        placeholders = ", ".join("?" for _ in companies)
        params: list = [day, *companies]
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(
            f"""SELECT * FROM scraped_articles
               WHERE date(first_scraped_at) = ? AND company IN ({placeholders})
                 AND status = 'ok' AND summary != ''
               ORDER BY first_scraped_at DESC
               {limit_clause}""",
            params,
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def get_daily_overview(self, day: str) -> DailyOverview | None:
        row = self._conn.execute(
            "SELECT * FROM daily_overview WHERE day = ?", (day,)
        ).fetchone()
        return DailyOverview(**dict(row)) if row else None

    def latest_daily_overview(self, before_or_on: str | None = None) -> DailyOverview | None:
        """Most recent overview, optionally restricted to `day <= before_or_on`."""
        if before_or_on is not None:
            row = self._conn.execute(
                "SELECT * FROM daily_overview WHERE day <= ? ORDER BY day DESC LIMIT 1",
                (before_or_on,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM daily_overview ORDER BY day DESC LIMIT 1"
            ).fetchone()
        return DailyOverview(**dict(row)) if row else None

    def upsert_daily_overview(self, overview: DailyOverview) -> None:
        self._conn.execute(
            """INSERT INTO daily_overview
                (day, text, article_count, source_hash, model, generated_at)
               VALUES (:day, :text, :article_count, :source_hash, :model, :generated_at)
               ON CONFLICT(day) DO UPDATE SET
                   text          = excluded.text,
                   article_count = excluded.article_count,
                   source_hash   = excluded.source_hash,
                   model         = excluded.model,
                   generated_at  = excluded.generated_at""",
            overview.model_dump(),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ArticleDB":
        return self

    def __exit__(self, *_) -> None:
        self.close()
