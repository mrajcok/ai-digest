import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from tenacity import Retrying, before_sleep_log, stop_after_attempt, wait_exponential

from digest.config import settings
from digest.sources import company_label
from digest.storage.models import ScrapedPage

logger = logging.getLogger(__name__)

# One entry per category in storage.models.CATEGORIES. A missing key silently
# takes the generic fallback in summarize(), so tests assert full coverage.
_CATEGORY_INSTRUCTIONS: dict[str, str] = {
    "blog": "Focus on the technical insight or capability being introduced.",
    "research": (
        "Lead with the claim or result, give the method in one clause, and say what is "
        "new relative to prior work. Preserve model, benchmark, and dataset names exactly, "
        "along with any reported numbers. Do not overstate a result the paper hedges."
    ),
    "engineering": (
        "Focus on the system or technique described, the problem it solves, and any "
        "concrete numbers — latency, throughput, cost, or scale. Name the components "
        "and tools involved exactly."
    ),
    "news": (
        "Focus on what was announced, by whom, and the stated impact. Attribute claims to "
        "the outlet or the announcing organization rather than asserting them as fact, and "
        "keep figures (funding amounts, model names, dates) exact."
    ),
    "press_release": (
        "Focus on what was announced, with whom, and the stated "
        "business or technical impact."
    ),
    "product": (
        "Focus on what capabilities exist, what's new or highlighted, "
        "and any pricing or availability signals."
    ),
    "release_notes": (
        "Focus on what changed in this release: new features, behavior changes, "
        "deprecations, and version-compatibility notes. Preserve exact model, API, "
        "and feature names. Extract the individual changes into your own bullet list, "
        "one bullet per distinct change, rather than describing how the source document "
        "is organized or referring to its section headings."
    ),
}

_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are an industry analyst tracking the major AI labs and the press that covers "
        "them, for a team of software engineers and architects. "
        "Write summaries that help a reader decide whether the item is relevant to them. "
        "Output only the summary text in markdown, without any commentary or explanation. "
        "If the content is too long to summarize effectively, produce a concise summary of the most "
        "important details and include a note that the original content should be consulted for more information. ",
    ),
    (
        "human",
        "Write a summary of the following {category} from {company}.\n"
        "{length_guidance}\n\n"
        "{category_instruction}\n\n"
        "Always include: specific model, product, or feature names, version numbers if "
        "present, and the core technical claim or announcement.\n"
        "Never include: generic marketing phrases, \"click here\" calls to action, "
        "speculation beyond what the content states, or repetition of the article title.\n\n"
        "Title: {title}\n\n"
        "Content:\n{content}",
    ),
])


def _bullet_guidance(n: int, what: str) -> str:
    return (
        f"One short lead sentence, then up to {n} bullet points for the key {what}. "
        "Each bullet must start on its own line with '* '."
    )


def _length_guidance(char_count: int, category: str = "") -> str:
    """Release notes get roughly double the summary length of blog/press/product content.

    Release notes are inherently an itemized list of discrete changes, so — unlike
    blog/press/product — they should default to bullets well before the 4000-char
    mark that triggers bullets for prose content.
    """
    if category == "release_notes":
        if char_count < 300:
            return "One sentence."
        if char_count < 800:
            return _bullet_guidance(3, "changes")
        if char_count < 2000:
            return _bullet_guidance(5, "changes")
        if char_count < 4000:
            return _bullet_guidance(7, "changes")
        return _bullet_guidance(8, "technical details")
    if char_count < 500:
        return "One sentence."
    if char_count < 2000:
        return "2-3 sentences."
    if char_count < 4000:
        return "3-5 sentences."
    return _bullet_guidance(4, "technical details")


class Summarizer:
    """LangChain chain that summarizes a ScrapedPage via OpenRouter or a local LM Studio server."""

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None) -> None:
        llm = ChatOpenAI(
            model=model or settings.openrouter_summarization_model,
            api_key=SecretStr(api_key or settings.openrouter_api_key),
            base_url=base_url or "https://openrouter.ai/api/v1",
        )
        self._chain = _PROMPT | llm | StrOutputParser()

    def summarize(self, page: ScrapedPage) -> str:
        content = page.raw_text[:settings.summarizer_content_chars]
        inputs = {
            # The display label, not the key — "Ars Technica" reads better than
            # "arstechnica" in the prompt, and matters for press attribution.
            "company": company_label(page.company),
            "category": page.category,
            "title": page.title,
            "content": content,
            "length_guidance": _length_guidance(len(page.raw_text), page.category),
            "category_instruction": _CATEGORY_INSTRUCTIONS.get(
                page.category,
                "Focus on what changed or was announced and why it matters.",
            ),
        }
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(settings.max_api_retries),
                wait=wait_exponential(multiplier=1, min=2, max=30),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                reraise=True,
            ):
                with attempt:
                    return self._chain.invoke(inputs)
        except Exception as exc:
            logger.error(
                "Summarization failed after %d attempts (%s) — using raw text fallback",
                settings.max_api_retries, exc,
            )
            return page.raw_text[:300]
        raise AssertionError("unreachable")
