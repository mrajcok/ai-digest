import logging

import httpx

from digest.config import settings

logger = logging.getLogger(__name__)


def post_discord_summary(stats: dict[str, dict[str, int]]) -> None:
    if not settings.discord_notify:
        return
    lines = ["**Daily AI digest complete**"]
    total_found = 0
    total_processed = 0
    for company, counts in stats.items():
        found = counts["found"]
        processed = counts["processed"]
        total_found += found
        total_processed += processed
        if found == 0:
            lines.append(f"• {company}: 0 new articles")
        elif found == processed:
            lines.append(f"• {company}: {found} new article{'s' if found != 1 else ''}")
        else:
            failed = found - processed
            lines.append(f"• {company}: {found} found, {processed} processed ({failed} failed)")
    lines.append(f"**Total: {total_found} found, {total_processed} processed**")
    message = "\n".join(lines)
    if settings.discord_notify_method == "webhook":
        _post_via_webhook(message)
    else:
        logger.warning(
            "Unsupported discord_notify_method %r — only 'webhook' is supported; skipping",
            settings.discord_notify_method,
        )


def _post_via_webhook(message: str) -> None:
    if not settings.discord_webhook_url:
        logger.warning("discord_notify_method=webhook but DISCORD_WEBHOOK_URL not set — skipping")
        return
    try:
        resp = httpx.post(settings.discord_webhook_url, json={"content": message}, timeout=10.0)
        resp.raise_for_status()
        logger.info("Discord notification sent via webhook")
    except Exception as exc:
        logger.warning("Discord webhook notification failed: %s", exc)
