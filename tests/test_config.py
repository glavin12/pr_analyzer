import pytest

from agentic_pr_analyzer.config import load_settings
from agentic_pr_analyzer.exceptions import ConfigError


def test_missing_token_raises_config_error(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    # Pass a nonexistent env_file explicitly rather than relying on CWD:
    # load_dotenv()'s default (no path) searches upward from config.py's own
    # location regardless of CWD, so it would find this repo's real .env
    # once one exists — an explicit missing path is the only reliable way
    # to test the "no token anywhere" case.
    with pytest.raises(ConfigError):
        load_settings(env_file=tmp_path / ".env")


def test_blank_token_raises_config_error(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "   ")
    with pytest.raises(ConfigError):
        load_settings()


def test_token_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "not-a-real-secret")
    assert load_settings().github_token == "not-a-real-secret"


def test_token_is_stripped_of_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "  not-a-real-secret  ")
    assert load_settings().github_token == "not-a-real-secret"
