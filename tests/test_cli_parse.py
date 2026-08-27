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


# ---------------------------------------------------------------- Section 7

SECRETS_FIXTURE = Path("tests/fixtures/raw_logs/SYNTHETIC/secrets-sample/sample.log")


def test_cli_parse_output_is_pure_ascii_and_contains_no_planted_secret(capsys):
    """Closes the "nothing unmasked reaches stdout" claim end to end.

    Pure-ASCII matters for more than tidiness: the mask token uses guillemets
    («»), and a cp1252 Windows console raises UnicodeEncodeError on those. It
    is safe only because to_json uses ensure_ascii=True -- which is exactly why
    no code may print a raw Diagnostic.message or LogSection.title.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from secret_examples import EXAMPLE_SECRETS

    exit_code = main(["parse", str(SECRETS_FIXTURE)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert all(ord(ch) < 128 for ch in captured.out)
    json.loads(captured.out)

    for cls, literal in EXAMPLE_SECRETS.items():
        needle = literal.split("=", 1)[1] if cls == "env_secret" else literal
        assert needle not in captured.out, f"{cls} reached stdout"
