from datetime import datetime, timezone
from pathlib import Path

from ..exceptions import NoFailedJobsFoundError, NoFailedRunsFoundError
from .client import GitHubClient
from .models import RawLog, save_raw_log


def resolve_latest_failed_run(client: GitHubClient, owner: str, repo: str) -> dict:
    """Discovers run_id itself — never manually supplied. Picks max by
    created_at client-side rather than trusting per_page alone: GitHub's
    default sort for this endpoint is newest-first in practice, but it is
    not formally documented/guaranteed, so this is a cheap correctness
    hardening for what is otherwise the central discovery step of Slice 1.
    """
    runs = client.list_workflow_runs(owner, repo, status="failure", per_page=10)
    if not runs:
        raise NoFailedRunsFoundError(owner, repo)
    return max(runs, key=lambda r: r["created_at"])


def list_failed_jobs(client: GitHubClient, owner: str, repo: str, run_id: int) -> list[dict]:
    jobs = client.list_jobs_for_run(owner, repo, run_id)
    failed = [j for j in jobs if j.get("conclusion") == "failure"]
    if not failed:
        raise NoFailedJobsFoundError(owner, repo, run_id)
    return failed


def build_raw_log(
    client: GitHubClient, owner: str, repo: str, run: dict, job: dict
) -> RawLog:
    content = client.get_job_log(owner, repo, job["id"])
    return RawLog(
        owner=owner,
        repo=repo,
        run_id=run["id"],
        run_attempt=run.get("run_attempt", 1),
        job_id=job["id"],
        job_name=job["name"],
        workflow_name=run.get("name") or "",
        conclusion=job["conclusion"],
        head_sha=run["head_sha"],
        html_url=job["html_url"],
        fetched_at=datetime.now(timezone.utc),
        content=content,
    )


def ingest_latest_failure(
    client: GitHubClient, owner: str, repo: str, fixtures_dir: Path
) -> list[Path]:
    """Slice 1 orchestrator: resolve -> filter -> fetch -> save.

    Does not filter by trigger event (push/pull_request/schedule) — takes
    the single most recent failed run regardless of trigger, since
    PR-specific correlation is explicitly Slice 3's job, not this slice's.
    """
    run = resolve_latest_failed_run(client, owner, repo)
    jobs = list_failed_jobs(client, owner, repo, run["id"])
    saved_paths = []
    for job in jobs:
        log = build_raw_log(client, owner, repo, run, job)
        saved_paths.append(save_raw_log(log, fixtures_dir))
    return saved_paths
