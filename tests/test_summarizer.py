"""Prompt construction — docs/plan.md Step 2f. No LLM call: everything here is pure."""

from digest.storage.models import CATEGORIES
from digest.summarizer import _CATEGORY_INSTRUCTIONS, _PROMPT, _length_guidance


def _prompt_text() -> str:
    return " ".join(m.prompt.template for m in _PROMPT.messages)  # type: ignore[union-attr]


def test_every_category_has_an_instruction() -> None:
    """A missing key takes the generic fallback silently — this is what catches that."""
    assert set(_CATEGORY_INSTRUCTIONS) == set(CATEGORIES)


def test_prompt_is_no_longer_about_the_old_vendors() -> None:
    text = _prompt_text().lower()
    for stale in ("cribl", "ocient", "xsiam", "data-infrastructure"):
        assert stale not in text
    assert "ai labs" in text


def test_output_contract_is_intact() -> None:
    """The markdown-only / no-commentary / too-long contract survives the rewrite."""
    text = _prompt_text()
    assert "Output only the summary text in markdown" in text
    assert "without any commentary or explanation" in text
    assert "too long to summarize effectively" in text


def test_ai_instructions_name_what_to_preserve() -> None:
    assert "benchmark" in _CATEGORY_INSTRUCTIONS["research"]
    assert "latency" in _CATEGORY_INSTRUCTIONS["engineering"]
    assert "Attribute claims to" in _CATEGORY_INSTRUCTIONS["news"]


def test_release_notes_instruction_is_domain_neutral() -> None:
    """Kept in the Literal, but its Ocient-docs wording is gone."""
    assert "SQL statement" not in _CATEGORY_INSTRUCTIONS["release_notes"]
    assert "catalog object" not in _CATEGORY_INSTRUCTIONS["release_notes"]


def test_length_guidance_scales_with_content() -> None:
    assert _length_guidance(100) == "One sentence."
    assert "2-3 sentences" in _length_guidance(1000)
    assert "3-5 sentences" in _length_guidance(3000)
    assert "bullet points" in _length_guidance(9000)


def test_release_notes_get_bullets_sooner() -> None:
    assert "bullet points" in _length_guidance(1000, "release_notes")
    assert "bullet points" not in _length_guidance(1000, "blog")
