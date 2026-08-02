from digest.publisher.github_pages import _top_per_company
from digest.storage.models import ArticleRecord


def _record(company: str, n: int) -> ArticleRecord:
    return ArticleRecord(
        url=f"https://example.com/{company}/{n}",
        normalized_url=f"https://example.com/{company}/{n}",
        company=company,
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
