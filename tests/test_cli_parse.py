import json
from pathlib import Path

from agentic_pr_analyzer.cli import main

FIXTURE = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")


def test_parse_command_prints_failure_report_json(capsys):
    exit_code = main(["parse", str(FIXTURE)])
    captured = capsys.readouterr()
    assert exit_code == 0
    report = json.loads(captured.out)
    assert report["provider"] == "github_actions"
    assert report["raw_line_count"] == 2323


def test_parse_command_does_not_require_github_token(capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    exit_code = main(["parse", str(FIXTURE)])
    assert exit_code == 0


def test_parse_command_missing_file_returns_4(capsys, tmp_path):
    missing = tmp_path / "does_not_exist.log"
    exit_code = main(["parse", str(missing)])
    captured = capsys.readouterr()
    assert exit_code == 4
    assert "not found" in captured.err
