"""Longer summaries for firewall-blocked companies — docs/plan.md Step 2g.

All offline: `_length_guidance()` is pure, and the one `summarize()` test stubs
the chain rather than calling an LLM.
"""

import pytest

from digest.config import Settings, settings
from digest.sources import COMPANIES
from digest.storage.models import ScrapedPage
from digest.summarizer import Summarizer, _length_guidance


def _page(company: str, chars: int = 5000) -> ScrapedPage:
    return ScrapedPage(
        url=f"https://example.com/{company}",
        company=company,
        source=f"{company}-x",
        category="news",
        title="T",
        raw_text="x" * chars,
    )


# --- config parsing --------------------------------------------------------

def test_csv_env_parses_without_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare CSV value would raise without NoDecode + the validator."""
    monkeypatch.setenv("LONG_SUMMARY_COMPANIES", "a,b")
    assert Settings().long_summary_companies == ["a", "b"]  # type: ignore[call-arg]


def test_csv_env_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LONG_SUMMARY_COMPANIES", " Anthropic , OpenAI ,, ")
    assert Settings().long_summary_companies == ["anthropic", "openai"]  # type: ignore[call-arg]


def test_empty_env_disables_the_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LONG_SUMMARY_COMPANIES", "")
    assert Settings().long_summary_companies == []  # type: ignore[call-arg]


def test_default_names_only_real_companies() -> None:
    """Read the declared default, not a Settings() instance — a local .env may override it."""
    default = Settings.model_fields["long_summary_companies"].default
    assert default == ["anthropic", "openai", "mistral"]
    for key in default:
        assert key in COMPANIES, f"{key} is not a company key"


# --- length guidance -------------------------------------------------------

def test_long_guidance_is_bigger_at_every_size() -> None:
    for chars in (200, 600, 1500, 3000, 9000):
        short = _length_guidance(chars)
        long = _length_guidance(chars, long=True)
        assert short != long
        assert len(long) > len(short)


def test_long_guidance_asks_for_a_standalone_summary() -> None:
    """The whole point: the reader may never see the article."""
    assert "stand on its own" in _length_guidance(5000, long=True)
    assert "stand on its own" not in _length_guidance(5000)


def test_long_guidance_switches_to_bullets_sooner() -> None:
    assert "bullet points" in _length_guidance(600, long=True)
    assert "bullet points" not in _length_guidance(600)


def test_long_guidance_raises_the_bullet_count() -> None:
    assert "up to 10 bullet points" in _length_guidance(9000, long=True)
    assert "up to 4 bullet points" in _length_guidance(9000)


def test_short_content_still_short_when_long() -> None:
    assert _length_guidance(100, long=True).startswith("2-3 sentences.")


# --- selection + input budget ---------------------------------------------

def test_listed_company_gets_long_treatment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(settings, "long_summary_companies", ["anthropic"])
    monkeypatch.setattr(settings, "summarizer_content_chars", 100)
    monkeypatch.setattr(settings, "summarizer_content_chars_long", 400)

    summarizer = Summarizer.__new__(Summarizer)
    summarizer._chain = type("Chain", (), {"invoke": lambda _self, inputs: captured.update(inputs) or "ok"})()

    assert summarizer.summarize(_page("anthropic")) == "ok"
    assert len(captured["content"]) == 400
    assert "stand on its own" in captured["length_guidance"]

    captured.clear()
    assert summarizer.summarize(_page("aws")) == "ok"
    assert len(captured["content"]) == 100
    assert "stand on its own" not in captured["length_guidance"]
