from pathlib import Path

from digest.publisher.github_pages import GitHubPagesPublisher, _top_per_company
from digest.storage.db import ArticleDB
from digest.storage.models import ArticleRecord


def _record(company: str, n: int, source: str = "") -> ArticleRecord:
    return ArticleRecord(
        url=f"https://example.com/{company}/{n}",
        normalized_url=f"https://example.com/{company}/{n}",
        company=company,
        source=source,
        category="news",
        title=f"{company} article {n}",
        first_scraped_at="2026-08-01T00:00:00+00:00",
        last_scraped_at="2026-08-01T00:00:00+00:00",
        content_hash="hash",
        summary="summary",
    )


def test_top_per_company_caps_press_lower_than_vendor() -> None:
    company_updates = {
        "anthropic": [_record("anthropic", i) for i in range(5)],
        "techcrunch": [_record("techcrunch", i) for i in range(5)],
    }

    top = _top_per_company(company_updates, index_per_company=3, index_per_company_press=1)

    assert sum(1 for r in top if r.company == "anthropic") == 3
    assert sum(1 for r in top if r.company == "techcrunch") == 1


def test_index_splits_vendor_and_press_sections(tmp_path: Path) -> None:
    db = ArticleDB(":memory:")
    db.upsert(_record("anthropic", 0, "anthropic-sitemap"))
    db.upsert(_record("techcrunch", 0, "techcrunch-ai"))

    publisher = GitHubPagesPublisher(db)
    publisher.render_from_db(tmp_path)

    index_html = (tmp_path / "index.html").read_text()
    vendor_pos = index_html.index(">Vendors<")
    press_pos = index_html.index(">Press<")
    anthropic_pos = index_html.index("anthropic article 0")
    techcrunch_pos = index_html.index("techcrunch article 0")

    assert vendor_pos < anthropic_pos < press_pos < techcrunch_pos


def test_company_page_shows_source_badge_only_for_multi_source_companies(tmp_path: Path) -> None:
    db = ArticleDB(":memory:")
    db.upsert(_record("google", 0, "google-ai-blog"))
    db.upsert(_record("google", 1, "google-deepmind"))
    db.upsert(_record("anthropic", 0, "anthropic-sitemap"))

    publisher = GitHubPagesPublisher(db)
    publisher.render_from_db(tmp_path)

    google_html = (tmp_path / "google" / "index.html").read_text()
    assert "Google AI Blog" in google_html
    assert "DeepMind Blog" in google_html

    anthropic_html = (tmp_path / "anthropic" / "index.html").read_text()
    assert '<span class="source">' not in anthropic_html
