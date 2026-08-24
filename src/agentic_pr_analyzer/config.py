import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .exceptions import ConfigError


@dataclass(frozen=True)
class Settings:
    github_token: str


def load_settings(env_file: Path | None = None) -> Settings:
    """Loads .env (if present) then reads GITHUB_TOKEN from the environment.

    Real environment variables always win over .env (load_dotenv's default,
    override=False) — deliberate, so CI/shell-set values aren't shadowed.
    Never logs or includes the token value in any exception message.
    """
    load_dotenv(dotenv_path=env_file)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "GITHUB_TOKEN is not set. Copy .env.example to .env and add a "
            "GitHub Personal Access Token (see .env.example for scope guidance)."
        )
    return Settings(github_token=token)
