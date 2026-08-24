from unittest.mock import Mock

import pytest

from agentic_pr_analyzer.exceptions import NoFailedJobsFoundError, NoFailedRunsFoundError
from agentic_pr_analyzer.github.client import GitHubClient
from agentic_pr_analyzer.github.ingestion import (
    build_raw_log,
    ingest_latest_failure,
    list_failed_jobs,
    resolve_latest_failed_run,
)
from agentic_pr_analyzer.github.models import RawLog, save_raw_log


def _make_raw_log(content: str) -> RawLog:
    from datetime import datetime, timezone

    return RawLog(
        owner="owner",
        repo="repo",
        run_id=1,
        run_attempt=1,
        job_id=11,
        job_name="test",
        workflow_name="CI",
        conclusion="failure",
        head_sha="abc",
        html_url="https://x",
        fetched_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        content=content,
    )


def test_resolve_raises_when_no_failed_runs():
    client = Mock(spec=GitHubClient)
    client.list_workflow_runs.return_value = []
    with pytest.raises(NoFailedRunsFoundError):
        resolve_latest_failed_run(client, "owner", "repo")


def test_resolve_picks_max_created_at_not_just_first():
    client = Mock(spec=GitHubClient)
    client.list_workflow_runs.return_value = [
        {"id": 1, "created_at": "2026-08-01T00:00:00Z"},
        {"id": 2, "created_at": "2026-08-20T00:00:00Z"},
        {"id": 3, "created_at": "2026-08-10T00:00:00Z"},
    ]
    run = resolve_latest_failed_run(client, "owner", "repo")
    assert run["id"] == 2


def test_list_failed_jobs_filters_by_conclusion():
    client = Mock(spec=GitHubClient)
    client.list_jobs_for_run.return_value = [
        {"id": 1, "name": "lint", "conclusion": "success"},
        {"id": 2, "name": "test", "conclusion": "failure"},
    ]
    failed = list_failed_jobs(client, "owner", "repo", 99)
    assert [j["id"] for j in failed] == [2]


def test_list_failed_jobs_raises_when_none_failed():
    client = Mock(spec=GitHubClient)
    client.list_jobs_for_run.return_value = [{"id": 1, "conclusion": "success"}]
    with pytest.raises(NoFailedJobsFoundError):
        list_failed_jobs(client, "owner", "repo", 99)


def test_build_raw_log_fetches_content_and_preserves_it_verbatim():
    client = Mock(spec=GitHubClient)
    client.get_job_log.return_value = "  weird\tspacing\n\x1b[31mANSI stays as-is\x1b[0m"
    run = {"id": 42, "run_attempt": 1, "name": "CI", "head_sha": "abc123"}
    job = {"id": 7, "name": "test", "conclusion": "failure", "html_url": "https://example.invalid"}
    log = build_raw_log(client, "owner", "repo", run, job)
    assert log.content == "  weird\tspacing\n\x1b[31mANSI stays as-is\x1b[0m"
    assert log.run_id == 42
    assert log.job_id == 7


def test_ingest_latest_failure_saves_one_fixture_per_failed_job(tmp_path):
    client = Mock(spec=GitHubClient)
    client.list_workflow_runs.return_value = [
        {"id": 1, "created_at": "2026-08-20T00:00:00Z", "run_attempt": 1, "name": "CI", "head_sha": "abc"}
    ]
    client.list_jobs_for_run.return_value = [
        {"id": 10, "name": "lint", "conclusion": "success", "html_url": "https://x"},
        {"id": 11, "name": "test", "conclusion": "failure", "html_url": "https://x"},
    ]
    client.get_job_log.return_value = "boom\r\n"

    saved = ingest_latest_failure(client, "owner", "repo", tmp_path)

    assert len(saved) == 1
    assert saved[0].name == "1_11.log"
    # Assert on bytes: the CRLF content must land on disk unchanged.
    assert saved[0].read_bytes() == b"boom\r\n"
    assert (tmp_path / "owner" / "repo" / "1_11.json").exists()


def test_save_raw_log_preserves_crlf_without_doubling(tmp_path):
    # Python's default text mode would rewrite \r\n -> \r\r\n on Windows.
    # save_raw_log opens with newline="" so GitHub's bytes survive intact.
    path = save_raw_log(_make_raw_log("a\r\nb\r\n"), tmp_path)
    assert path.read_bytes() == b"a\r\nb\r\n"


def test_save_raw_log_preserves_bom_and_accents_byte_for_byte(tmp_path):
    # A UTF-8 BOM + accented char + CRLF must round-trip to exact UTF-8 bytes.
    path = save_raw_log(_make_raw_log("﻿Café\r\n"), tmp_path)
    assert path.read_bytes() == "﻿Café\r\n".encode("utf-8")
