from .base import LogProvider
from .generic import GenericProvider
from .github_actions import GitHubActionsProvider

# Fixed priority order; GenericProvider is the terminal fallback (always detects).
PROVIDERS: tuple[LogProvider, ...] = (GitHubActionsProvider(), GenericProvider())


def detect_provider(sample: str) -> LogProvider:
    # PROVIDERS ends in GenericProvider, whose detect() is always True, so the
    # loop always returns; no separate fallback return is reachable.
    for provider in PROVIDERS:
        if provider.detect(sample):
            return provider


__all__ = ["LogProvider", "GenericProvider", "GitHubActionsProvider", "PROVIDERS", "detect_provider"]
