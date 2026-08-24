import requests

from ..exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubForbiddenError,
    GitHubNotFoundError,
)


class GitHubClient:
    """Thin authenticated wrapper around only the GitHub REST endpoints this
    project needs — not a full API client. Add methods only as later slices
    need them.
    """

    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: str,
        session: requests.Session | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        self._timeout = timeout

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self._timeout)
        response = self._session.request(method, f"{self.BASE_URL}{path}", **kwargs)
        if response.status_code == 401:
            raise GitHubAuthError(f"{method} {path} -> 401: {_message(response)}")
        if response.status_code == 403:
            raise GitHubForbiddenError(
                f"{method} {path} -> 403: {_message(response)}",
                retry_after=response.headers.get("Retry-After"),
                rate_limit_reset=response.headers.get("X-RateLimit-Reset"),
            )
        if response.status_code == 404:
            raise GitHubNotFoundError(f"{method} {path} -> 404: {_message(response)}")
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise GitHubAPIError(
                f"{method} {path} -> {response.status_code}: {_message(response)}"
            ) from e
        return response

    def verify_access(self) -> dict:
        """GET /rate_limit — the smallest possible authenticated call.

        This endpoint does NOT count against the rate limit, so it's free
        to call. Confirms the token works and returns quota info.
        """
        return self._request("GET", "/rate_limit").json()

    def list_workflow_runs(
        self, owner: str, repo: str, *, status: str = "failure", per_page: int = 10
    ) -> list[dict]:
        """GET /repos/{owner}/{repo}/actions/runs?status=failure&per_page=...

        `status=failure` is a documented, supported filter value.
        """
        resp = self._request(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs",
            params={"status": status, "per_page": per_page},
        )
        return resp.json().get("workflow_runs", [])

    def list_jobs_for_run(self, owner: str, repo: str, run_id: int) -> list[dict]:
        resp = self._request(
            "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        )
        return resp.json().get("jobs", [])

    def get_job_log(self, owner: str, repo: str, job_id: int) -> str:
        """GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs

        GitHub returns a 302 to a short-lived signed blob-storage URL
        (expires in ~1 minute) — `requests` follows it automatically in the
        same call, which is both simplest and necessary given that expiry.
        Also relevant: requests/urllib3 automatically strip the
        Authorization header when a redirect crosses hosts, so the GitHub
        PAT is never sent to the blob storage host. Do not pass
        allow_redirects=False and manually re-attach the header — that
        would leak the token to a third-party host.
        """
        resp = self._request(
            "GET", f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        )
        # GitHub Actions logs are UTF-8, but this endpoint's blob response has no
        # charset in its Content-Type, so requests falls back to Latin-1 and
        # mojibakes every non-ASCII byte. Decode the raw bytes as UTF-8 ourselves.
        # errors="replace" keeps a pathological non-UTF-8 byte from crashing ingestion.
        return resp.content.decode("utf-8", errors="replace")


def _message(response: requests.Response) -> str:
    try:
        return response.json().get("message", response.text[:200])
    except ValueError:
        return response.text[:200]
