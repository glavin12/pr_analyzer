class AgenticPRAnalyzerError(Exception):
    """Base class for all project-specific exceptions."""


class ConfigError(AgenticPRAnalyzerError):
    """Required configuration (e.g. GITHUB_TOKEN) is missing or invalid."""


class GitHubAPIError(AgenticPRAnalyzerError):
    """Base class for non-2xx GitHub API responses."""


class GitHubAuthError(GitHubAPIError):
    """401 — token missing, invalid, expired, or revoked."""


class GitHubForbiddenError(GitHubAPIError):
    """403 — rate-limited (primary or secondary) or a genuine permissions problem."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: str | None = None,
        rate_limit_reset: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limit_reset = rate_limit_reset


class GitHubNotFoundError(GitHubAPIError):
    """404 — repo/run/job not found or inaccessible."""


class NoFailedRunsFoundError(AgenticPRAnalyzerError):
    """Not an API error: the repo simply has no failed workflow runs."""

    def __init__(self, owner: str, repo: str) -> None:
        super().__init__(f"No failed workflow runs found for {owner}/{repo}.")


class NoFailedJobsFoundError(AgenticPRAnalyzerError):
    """Edge case: the run is failed but no job has conclusion == 'failure'."""

    def __init__(self, owner: str, repo: str, run_id: int) -> None:
        super().__init__(
            f"Run {run_id} in {owner}/{repo} has no job with conclusion 'failure'."
        )
