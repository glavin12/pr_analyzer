from .base import LogProvider
from .generic import GenericProvider
from .github_actions import GitHubActionsProvider

# Fixed priority order; GenericProvider is the terminal fallback (always detects).
PROVIDERS: tuple[LogProvider, ...] = (GitHubActionsProvider(), GenericProvider())


def detect_provider(sample: str) -> LogProvider:
    for provider in PROVIDERS:
        if provider.detect(sample):
            return provider
    return GenericProvider()


__all__ = ["LogProvider", "GenericProvider", "GitHubActionsProvider", "PROVIDERS", "detect_provider"]
