"""Settings tolerates env vars that belong to other tooling.

`.env` is shared with the autopilot harness, whose EMAIL_*/SMTP_* settings the
pipeline knows nothing about. pydantic-settings forbids undeclared keys by
default, which turned any such entry into a startup crash.
"""
from digest.config import Settings


def test_unknown_env_vars_are_ignored(monkeypatch) -> None:
    """Autopilot's mail settings in the environment must not break Settings."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "someone@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "hunter2")
    monkeypatch.setenv("EMAIL_FROM", "someone@example.com")
    monkeypatch.setenv("EMAIL_TO", "someone@example.com")

    settings = Settings(_env_file=None)

    assert settings.openrouter_api_key == "test-key"
    assert not hasattr(settings, "smtp_host")


def test_declared_settings_still_load(monkeypatch) -> None:
    """extra='ignore' must not stop real settings from being read."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "real-key")
    monkeypatch.setenv("INDEX_PAGE_LIMIT", "7")

    settings = Settings(_env_file=None)

    assert settings.openrouter_api_key == "real-key"
    assert settings.index_page_limit == 7
