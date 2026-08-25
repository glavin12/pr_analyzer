from datetime import datetime, timezone
from pathlib import Path

from agentic_pr_analyzer.github.models import RawLog, load_raw_log, save_raw_log


def _make_log(content: str) -> RawLog:
    return RawLog(
        owner="pallets",
        repo="click",
        run_id=1,
        run_attempt=1,
        job_id=2,
        job_name="Windows",
        workflow_name="Tests",
        conclusion="failure",
        head_sha="abc123",
        html_url="https://example.invalid/1",
        fetched_at=datetime(2026, 8, 24, 6, 58, 2, 621818, tzinfo=timezone.utc),
        content=content,
    )


def test_load_raw_log_round_trips_save_raw_log(tmp_path: Path):
    original = _make_log("line one\r\nline two\r\n")
    log_path = save_raw_log(original, tmp_path)
    assert load_raw_log(log_path) == original


def test_load_raw_log_preserves_crlf_bytes_exactly(tmp_path: Path):
    original = _make_log("a\r\nb\r\n")
    log_path = save_raw_log(original, tmp_path)
    assert load_raw_log(log_path).content == "a\r\nb\r\n"


def test_load_raw_log_preserves_non_ascii_content(tmp_path: Path):
    original = _make_log("café \U0001f600\n")
    log_path = save_raw_log(original, tmp_path)
    assert load_raw_log(log_path).content == original.content
