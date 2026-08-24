import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RawLog:
    """The 'GitHub API -> RawLog' boundary (evidence model).

    Content is stored byte-for-byte as GitHub returned it — no
    ANSI-stripping, no normalization. That cleanup is Slice 2's job
    (the parser needs genuinely messy input); blurring this boundary here
    would leak Slice 2 concerns into ingestion.
    """

    owner: str
    repo: str
    run_id: int
    run_attempt: int
    job_id: int
    job_name: str
    workflow_name: str
    conclusion: str
    head_sha: str
    html_url: str
    fetched_at: datetime
    content: str

    def metadata_dict(self) -> dict:
        """Everything except `content` — explicit field list (not
        asdict()-minus-content) so a future field addition can't
        accidentally leak into the sidecar without a conscious decision.
        """
        return {
            "owner": self.owner,
            "repo": self.repo,
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
            "job_id": self.job_id,
            "job_name": self.job_name,
            "workflow_name": self.workflow_name,
            "conclusion": self.conclusion,
            "head_sha": self.head_sha,
            "html_url": self.html_url,
            "fetched_at": self.fetched_at.isoformat(),
        }


def save_raw_log(log: RawLog, base_dir: Path) -> Path:
    """Writes <base_dir>/<owner>/<repo>/<run_id>_<job_id>.log (raw text)
    and a sibling .json metadata sidecar. Always UTF-8 explicitly — on
    Windows the platform-default text encoding is NOT UTF-8, and CI logs
    can contain non-ASCII bytes that would otherwise raise
    UnicodeEncodeError or get silently mangled. `newline=""` disables
    Python's universal-newline re-translation, which would otherwise rewrite
    GitHub's `\r\n` line endings to `\r\r\n` on Windows — we preserve the
    log's bytes exactly as GitHub returned them. Overwrites on repeat runs
    for the same run_id/job_id (deterministic historical data, no
    versioning needed).
    """
    out_dir = base_dir / log.owner / log.repo
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{log.run_id}_{log.job_id}"
    log_path = out_dir / f"{stem}.log"
    log_path.write_text(log.content, encoding="utf-8", newline="")
    (out_dir / f"{stem}.json").write_text(
        json.dumps(log.metadata_dict(), indent=2), encoding="utf-8", newline=""
    )
    return log_path
