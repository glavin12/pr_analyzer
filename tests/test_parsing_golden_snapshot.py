"""Golden-snapshot regression test for Section 1's FailureReport output.

Regenerate the committed snapshot after an intentional output-format change:

    uv run python -c "
    from pathlib import Path
    from agentic_pr_analyzer.github.models import load_raw_log
    from agentic_pr_analyzer.parsing import to_json
    from agentic_pr_analyzer.parsing.model import LogSource
    from agentic_pr_analyzer.parsing.pipeline import parse_log
    raw = load_raw_log(Path('tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log'))
    report = parse_log(raw.content, LogSource.from_raw_log(raw))
    Path('tests/fixtures/parsed/pallets/click/32472305359_96741461054.json').write_text(to_json(report), encoding='utf-8')
    "
"""

import json
from pathlib import Path

from agentic_pr_analyzer.github.models import load_raw_log
from agentic_pr_analyzer.parsing import to_json
from agentic_pr_analyzer.parsing.model import DiagnosticType, LogSource
from agentic_pr_analyzer.parsing.pipeline import parse_log

LOG_PATH = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")
GOLDEN_PATH = Path("tests/fixtures/parsed/pallets/click/32472305359_96741461054.json")


def _parse_fixture():
    raw = load_raw_log(LOG_PATH)
    return parse_log(raw.content, LogSource.from_raw_log(raw))


def test_golden_snapshot_matches_committed_json():
    actual = json.loads(to_json(_parse_fixture()))
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert actual == expected


def test_key_facts_about_the_real_failure():
    report = _parse_fixture()
    assert report.raw_line_count == 2323
    assert report.provider == "github_actions"
    assert report.schema_version == "1.0"

    process_failures = [
        d for d in report.diagnostics if d.type == DiagnosticType.PROCESS_FAILURE
    ]
    assert len(process_failures) == 1
    assert process_failures[0].exit_code == 1
    assert process_failures[0].evidence == (2307,)
    assert report.exit_code == 1


def test_json_output_is_pure_ascii_and_valid():
    text = to_json(_parse_fixture())
    assert all(ord(ch) < 128 for ch in text)
    json.loads(text)
