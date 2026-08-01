"""Settings tolerates stray env vars it doesn't declare.

pydantic-settings forbids undeclared keys by default, which turns any such
entry into a startup crash.
"""
from digest.config import Settings


def test_unknown_env_vars_are_ignored(monkeypatch) -> None:
    """An undeclared env var must not break Settings."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("SOME_UNRELATED_TOOLS_SETTING", "whatever")

    settings = Settings(_env_file=None)

    assert settings.openrouter_api_key == "test-key"
    assert not hasattr(settings, "some_unrelated_tools_setting")


def test_declared_settings_still_load(monkeypatch) -> None:
    """extra='ignore' must not stop real settings from being read."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "real-key")
    monkeypatch.setenv("INDEX_PAGE_LIMIT", "7")

    settings = Settings(_env_file=None)

    assert settings.openrouter_api_key == "real-key"
    assert settings.index_page_limit == 7
