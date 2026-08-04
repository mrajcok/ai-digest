"""Daily overview ("summary of summaries") — docs/plan.md Step 7a."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from digest.main import _generate_overview
from digest.publisher.github_pages import GitHubPagesPublisher
from digest.storage.db import ArticleDB
from digest.storage.models import ArticleRecord, DailyOverview, daily_overview_source_hash

TODAY = datetime.now(UTC).date().isoformat()
YESTERDAY = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


def _record(company: str, n: int, published_date: str, category: str = "news") -> ArticleRecord:
    return ArticleRecord(
        url=f"https://example.com/{company}/{n}",
        normalized_url=f"https://example.com/{company}/{n}",
        company=company,
        source="",
        category=category,
        title=f"{company} article {n}",
        published_date=published_date[:10],
        first_scraped_at=f"{TODAY}T10:00:00+00:00",
        last_scraped_at=f"{TODAY}T10:00:00+00:00",
        content_hash="hash",
        summary="summary text",
    )


@pytest.fixture
def db():
    with ArticleDB(":memory:") as database:
        yield database


def test_articles_published_on_filters_by_published_date(db: ArticleDB) -> None:
    db.upsert(_record("anthropic", 0, TODAY))
    db.upsert(_record("anthropic", 1, YESTERDAY))

    records = db.articles_published_on(TODAY, ["anthropic"])

    assert len(records) == 1
    assert records[0].url.endswith("/0")


def test_articles_published_on_ignores_stale_backfill(db: ArticleDB) -> None:
    """A `--since` backfill scrapes old posts today; they must not count as today's news."""
    old = _record("microsoft", 0, "2026-07-23")
    db.upsert(old.model_copy(update={"first_scraped_at": f"{TODAY}T10:00:00+00:00"}))

    assert db.articles_published_on(TODAY, ["microsoft"]) == []


def test_articles_published_on_excludes_other_companies(db: ArticleDB) -> None:
    db.upsert(_record("anthropic", 0, TODAY))
    db.upsert(_record("techcrunch", 0, TODAY))

    records = db.articles_published_on(TODAY, ["anthropic"])

    assert len(records) == 1
    assert records[0].company == "anthropic"


def test_articles_published_on_respects_limit(db: ArticleDB) -> None:
    for i in range(5):
        db.upsert(_record("anthropic", i, TODAY))

    records = db.articles_published_on(TODAY, ["anthropic"], limit=2)

    assert len(records) == 2


def test_articles_published_on_excludes_missing_summary(db: ArticleDB) -> None:
    record = _record("anthropic", 0, TODAY)
    record = record.model_copy(update={"summary": ""})
    db.upsert(record)

    assert db.articles_published_on(TODAY, ["anthropic"]) == []


def test_articles_published_between_is_start_exclusive_end_inclusive(db: ArticleDB) -> None:
    two_days_ago = (datetime.now(UTC).date() - timedelta(days=2)).isoformat()
    db.upsert(_record("anthropic", 0, two_days_ago))  # on the boundary — excluded
    db.upsert(_record("anthropic", 1, YESTERDAY))  # inside the window
    db.upsert(_record("anthropic", 2, TODAY))  # on the boundary — included

    records = db.articles_published_between(two_days_ago, TODAY, ["anthropic"])

    assert {r.url for r in records} == {
        "https://example.com/anthropic/1",
        "https://example.com/anthropic/2",
    }


def test_articles_published_between_respects_limit(db: ArticleDB) -> None:
    for i in range(5):
        db.upsert(_record("anthropic", i, TODAY))

    records = db.articles_published_between(YESTERDAY, TODAY, ["anthropic"], limit=2)

    assert len(records) == 2


def test_daily_overview_round_trips(db: ArticleDB) -> None:
    overview = DailyOverview(
        day=TODAY, window_start=TODAY, text="Today...", article_count=3,
        source_hash="abc", model="m", generated_at=f"{TODAY}T12:00:00+00:00",
    )
    db.upsert_daily_overview(overview)

    fetched = db.get_daily_overview(TODAY)
    assert fetched is not None
    assert fetched.text == "Today..."


def test_daily_overview_upsert_overwrites(db: ArticleDB) -> None:
    db.upsert_daily_overview(
        DailyOverview(
            day=TODAY, window_start=TODAY, text="v1", article_count=1,
            source_hash="a", model="m", generated_at="t",
        )
    )
    db.upsert_daily_overview(
        DailyOverview(
            day=TODAY, window_start=TODAY, text="v2", article_count=2,
            source_hash="b", model="m", generated_at="t2",
        )
    )

    fetched = db.get_daily_overview(TODAY)
    assert fetched is not None
    assert fetched.text == "v2"
    assert fetched.source_hash == "b"


def test_latest_daily_overview_returns_most_recent(db: ArticleDB) -> None:
    db.upsert_daily_overview(
        DailyOverview(
            day=YESTERDAY, window_start=YESTERDAY, text="old", article_count=1,
            source_hash="a", model="m", generated_at="t",
        )
    )
    db.upsert_daily_overview(
        DailyOverview(
            day=TODAY, window_start=TODAY, text="new", article_count=1,
            source_hash="b", model="m", generated_at="t",
        )
    )

    fetched = db.latest_daily_overview()
    assert fetched is not None
    assert fetched.day == TODAY


def test_latest_daily_overview_none_when_empty(db: ArticleDB) -> None:
    assert db.latest_daily_overview() is None


def test_source_hash_stable_for_same_urls_different_order() -> None:
    a = [_record("anthropic", 0, "t"), _record("anthropic", 1, "t")]
    b = list(reversed(a))
    assert daily_overview_source_hash(a) == daily_overview_source_hash(b)


def test_source_hash_changes_with_article_set() -> None:
    a = [_record("anthropic", 0, "t")]
    b = [_record("anthropic", 0, "t"), _record("anthropic", 1, "t")]
    assert daily_overview_source_hash(a) != daily_overview_source_hash(b)


# ---------------------------------------------------------------------------
# _generate_overview (main.py) — stubs Summarizer to avoid a real LLM call
# ---------------------------------------------------------------------------

class _StubSummarizer:
    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self.calls = 0

    def summarize_day(self, records) -> str:
        self.calls += 1
        return "a synthesized overview"


def test_generate_overview_skips_when_no_vendor_articles(db: ArticleDB, monkeypatch: pytest.MonkeyPatch) -> None:
    db.upsert(_record("techcrunch", 0, f"{TODAY}T10:00:00+00:00"))  # press only
    stub = _StubSummarizer()
    monkeypatch.setattr("digest.main.Summarizer", lambda model=None: stub)

    _generate_overview(db, stage=True)

    assert db.get_daily_overview(TODAY) is None
    assert stub.calls == 0


def test_generate_overview_writes_row(db: ArticleDB, monkeypatch: pytest.MonkeyPatch) -> None:
    db.upsert(_record("anthropic", 0, f"{TODAY}T10:00:00+00:00"))
    stub = _StubSummarizer()
    monkeypatch.setattr("digest.main.Summarizer", lambda model=None: stub)

    _generate_overview(db, stage=True)

    overview = db.get_daily_overview(TODAY)
    assert overview is not None
    assert overview.text == "a synthesized overview"
    assert overview.article_count == 1
    assert overview.window_start == TODAY
    assert stub.calls == 1


def test_generate_overview_widens_window_on_quiet_morning(db: ArticleDB, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing published today yet, but yesterday's article was never covered — window widens to include it."""
    db.upsert(_record("anthropic", 0, YESTERDAY))
    stub = _StubSummarizer()
    monkeypatch.setattr("digest.main.Summarizer", lambda model=None: stub)

    _generate_overview(db, stage=True)

    overview = db.get_daily_overview(TODAY)
    assert overview is not None
    assert overview.window_start == YESTERDAY
    assert overview.article_count == 1
    assert stub.calls == 1


def test_generate_overview_watermark_excludes_already_covered_articles(
    db: ArticleDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An article already summarized in yesterday's overview must not be pulled into today's."""
    covered = _record("anthropic", 0, YESTERDAY)
    db.upsert(covered)
    db.upsert_daily_overview(
        DailyOverview(
            day=YESTERDAY, window_start=YESTERDAY, text="yesterday's news", article_count=1,
            source_hash=daily_overview_source_hash([covered]), model="m",
            generated_at=f"{YESTERDAY}T12:00:00+00:00",
        )
    )
    stub = _StubSummarizer()
    monkeypatch.setattr("digest.main.Summarizer", lambda model=None: stub)

    _generate_overview(db, stage=True)

    assert db.get_daily_overview(TODAY) is None
    assert stub.calls == 0


def test_generate_overview_lookback_is_capped(db: ArticleDB, monkeypatch: pytest.MonkeyPatch) -> None:
    db.upsert(_record("anthropic", 0, "2020-01-01"))  # far outside overview_max_lookback_days
    stub = _StubSummarizer()
    monkeypatch.setattr("digest.main.Summarizer", lambda model=None: stub)

    _generate_overview(db, stage=True)

    assert db.get_daily_overview(TODAY) is None
    assert stub.calls == 0


def test_generate_overview_skips_regeneration_when_unchanged(db: ArticleDB, monkeypatch: pytest.MonkeyPatch) -> None:
    db.upsert(_record("anthropic", 0, f"{TODAY}T10:00:00+00:00"))
    stub = _StubSummarizer()
    monkeypatch.setattr("digest.main.Summarizer", lambda model=None: stub)

    _generate_overview(db, stage=True)
    _generate_overview(db, stage=True)

    assert stub.calls == 1


def test_generate_overview_regenerates_when_article_set_changes(
    db: ArticleDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later same-day run must see BOTH articles, not just the one added since the first run —
    the watermark must never advance to today itself, or the second run would miss article 0."""
    db.upsert(_record("anthropic", 0, f"{TODAY}T10:00:00+00:00"))
    stub = _StubSummarizer()
    monkeypatch.setattr("digest.main.Summarizer", lambda model=None: stub)
    _generate_overview(db, stage=True)

    db.upsert(_record("anthropic", 1, f"{TODAY}T11:00:00+00:00"))
    _generate_overview(db, stage=True)

    assert stub.calls == 2
    overview = db.get_daily_overview(TODAY)
    assert overview is not None
    assert overview.article_count == 2


def test_overview_excludes_press_by_default(db: ArticleDB, monkeypatch: pytest.MonkeyPatch) -> None:
    db.upsert(_record("anthropic", 0, f"{TODAY}T10:00:00+00:00"))
    db.upsert(_record("techcrunch", 0, f"{TODAY}T10:00:00+00:00"))
    captured = {}

    class CapturingStub(_StubSummarizer):
        def summarize_day(self, records) -> str:
            captured["records"] = records
            return super().summarize_day(records)

    stub = CapturingStub()
    monkeypatch.setattr("digest.main.Summarizer", lambda model=None: stub)

    _generate_overview(db, stage=True)

    assert {r.company for r in captured["records"]} == {"anthropic"}


# ---------------------------------------------------------------------------
# Template rendering — three cases
# ---------------------------------------------------------------------------

def test_template_omits_overview_block_when_none(db: ArticleDB, tmp_path: Path) -> None:
    db.upsert(_record("anthropic", 0, f"{TODAY}T10:00:00+00:00"))
    publisher = GitHubPagesPublisher(db)
    publisher.render_from_db(tmp_path)

    index_html = (tmp_path / "index.html").read_text()
    assert '<section class="overview">' not in index_html
    assert "Today in AI" not in index_html


def test_template_renders_todays_overview(db: ArticleDB, tmp_path: Path) -> None:
    db.upsert(_record("anthropic", 0, f"{TODAY}T10:00:00+00:00"))
    db.upsert_daily_overview(
        DailyOverview(
            day=TODAY, window_start=TODAY, text="Today's big news.", article_count=1,
            source_hash="x", model="m", generated_at=f"{TODAY}T12:00:00+00:00",
        )
    )

    publisher = GitHubPagesPublisher(db)
    publisher.render_from_db(tmp_path)

    index_html = (tmp_path / "index.html").read_text()
    assert "Today&#39;s big news." in index_html or "Today's big news." in index_html
    assert "Today in AI" in index_html
    assert "Overview for" not in index_html
    assert "— since" not in index_html


def test_template_renders_widened_window_since_date(db: ArticleDB, tmp_path: Path) -> None:
    """A quiet-morning window (watermark older than today) shows "since <date>", not a bare "Today in AI"."""
    db.upsert(_record("anthropic", 0, f"{YESTERDAY}T10:00:00+00:00"))
    db.upsert_daily_overview(
        DailyOverview(
            day=TODAY, window_start=YESTERDAY, text="Quiet morning news.", article_count=1,
            source_hash="x", model="m", generated_at=f"{TODAY}T06:15:00+00:00",
        )
    )

    publisher = GitHubPagesPublisher(db)
    publisher.render_from_db(tmp_path)

    index_html = (tmp_path / "index.html").read_text()
    assert f"since {YESTERDAY}" in index_html
    assert "Overview for" not in index_html


def test_template_falls_back_to_most_recent_overview_labelled(db: ArticleDB, tmp_path: Path) -> None:
    db.upsert(_record("anthropic", 0, f"{YESTERDAY}T10:00:00+00:00"))
    db.upsert_daily_overview(
        DailyOverview(
            day=YESTERDAY, window_start=YESTERDAY, text="Yesterday's news.", article_count=1,
            source_hash="x", model="m", generated_at=f"{YESTERDAY}T12:00:00+00:00",
        )
    )

    publisher = GitHubPagesPublisher(db)
    publisher.render_from_db(tmp_path)

    index_html = (tmp_path / "index.html").read_text()
    assert "Yesterday" in index_html
    assert f"Overview for {YESTERDAY}" in index_html
