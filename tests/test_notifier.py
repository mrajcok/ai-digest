"""Discord notifier — webhook only, no hermes. docs/plan.md Step 8."""

import httpx
import respx

from digest.config import settings
from digest.notifier import post_discord_summary


def test_webhook_message_format(monkeypatch) -> None:
    monkeypatch.setattr(settings, "discord_notify", True)
    monkeypatch.setattr(settings, "discord_notify_method", "webhook")
    monkeypatch.setattr(settings, "discord_webhook_url", "https://discord.example/webhook")

    stats = {
        "anthropic": {"found": 2, "processed": 2},
        "openai": {"found": 1, "processed": 1},
        "google": {"found": 3, "processed": 3},
    }

    with respx.mock:
        route = respx.post("https://discord.example/webhook").mock(return_value=httpx.Response(200))
        post_discord_summary(stats)

    assert route.called
    sent = route.calls.last.request.content.decode()
    assert "Daily AI digest complete" in sent
    assert "anthropic: 2 new article" in sent
    assert "Total: 6 found, 6 processed" in sent


def test_partial_failure_reports_failed_count(monkeypatch) -> None:
    monkeypatch.setattr(settings, "discord_notify", True)
    monkeypatch.setattr(settings, "discord_notify_method", "webhook")
    monkeypatch.setattr(settings, "discord_webhook_url", "https://discord.example/webhook")

    stats = {"anthropic": {"found": 3, "processed": 1}}

    with respx.mock:
        route = respx.post("https://discord.example/webhook").mock(return_value=httpx.Response(200))
        post_discord_summary(stats)

    sent = route.calls.last.request.content.decode()
    assert "3 found, 1 processed (2 failed)" in sent


def test_notify_disabled_skips_webhook(monkeypatch) -> None:
    monkeypatch.setattr(settings, "discord_notify", False)

    with respx.mock:
        route = respx.post("https://discord.example/webhook").mock(return_value=httpx.Response(200))
        post_discord_summary({"anthropic": {"found": 1, "processed": 1}})

    assert not route.called


def test_unsupported_method_skips_webhook(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "discord_notify", True)
    monkeypatch.setattr(settings, "discord_notify_method", "hermes")

    with respx.mock:
        route = respx.post("https://discord.example/webhook").mock(return_value=httpx.Response(200))
        post_discord_summary({"anthropic": {"found": 1, "processed": 1}})

    assert not route.called
