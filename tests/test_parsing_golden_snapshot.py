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
from agentic_pr_analyzer.parsing.model import DiagnosticRole, DiagnosticType, LogSource
from agentic_pr_analyzer.parsing.pipeline import parse_log

LOG_PATH = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")
GOLDEN_PATH = Path("tests/fixtures/parsed/pallets/click/32472305359_96741461054.json")


TEXT_CANARY_PATH = Path("tests/fixtures/parsed/pallets/click/pre_section7_text.json")


def _drop_nondeterministic(report_dict: dict) -> dict:
    """stats["runtime"] is the ONE bucket holding wall-clock/host-dependent
    readings. Its *shape* is asserted separately (see
    test_stats_runtime_bucket_is_present_and_shaped); only its numeric values
    are excluded here. Everything else is still compared exactly.

    Excluding exactly one key BY NAME, rather than by a suffix convention
    like `*_ms`, keeps this greppable and stops it silently swallowing a
    future field someone happens to name that way.

    The committed snapshot therefore carries a stale `runtime.parse_ms` from
    whenever it was last regenerated. That is deliberate and ignored.
    """
    report_dict["stats"].pop("runtime", None)
    return report_dict


def _parse_fixture():
    raw = load_raw_log(LOG_PATH)
    return parse_log(raw.content, LogSource.from_raw_log(raw))


def test_golden_snapshot_matches_committed_json():
    actual = _drop_nondeterministic(json.loads(to_json(_parse_fixture())))
    expected = _drop_nondeterministic(json.loads(GOLDEN_PATH.read_text(encoding="utf-8")))
    assert actual == expected


def test_golden_snapshot_masked_text_is_unchanged_by_section_7():
    """The anti-over-masking canary, on real data.

    Section 7 took masking from 3 rules to 13. This is what proves the
    expansion did not quietly rewrite the anchor's evidence: every
    LogSection.title and every Diagnostic.message must be BYTE-IDENTICAL to
    its pre-Section-7 value. Over-masking destroys evidence, and that is a
    bug, not a tradeoff -- if this fails, fix the rule, never the fixture.
    """
    report = _parse_fixture()
    expected = json.loads(TEXT_CANARY_PATH.read_text(encoding="utf-8"))

    assert [s.title for s in report.sections] == expected["section_titles"]
    assert [d.message for d in report.diagnostics] == expected["diagnostic_messages"]


def test_key_facts_about_the_real_failure():
    report = _parse_fixture()
    assert report.raw_line_count == 2323
    assert report.provider == "github_actions"
    assert report.schema_version == "1.3"

    process_failures = [
        d for d in report.diagnostics if d.type == DiagnosticType.PROCESS_FAILURE
    ]
    assert len(process_failures) == 1
    assert process_failures[0].exit_code == 1
    assert process_failures[0].evidence == (2307,)
    assert report.exit_code == 1

    test_failures = [d for d in report.diagnostics if d.type == DiagnosticType.TEST_FAILURE]
    assert len(test_failures) == 1
    primary = test_failures[0]
    assert primary.test_id == "tests/test_types.py::test_file_surrogates[type1]"
    assert primary.file == "tests\\test_types.py"
    assert primary.line == 288
    assert report.primary_cluster is not None
    assert report.primary_cluster.primary is primary

    assert len(report.clusters) == 1
    assert report.primary_cluster.related_roles == (DiagnosticRole.CONSEQUENCE,)
    assert report.primary_cluster.related[0].exit_code == 1
    # Section 7 makes `fatal` always present (False on the success path) so no
    # consumer has to branch on key presence. Asserting the value is strictly
    # stronger than the pre-Section-7 "key is absent" check it replaces.
    assert report.stats["fatal"] is False


def test_json_output_is_pure_ascii_and_valid():
    text = to_json(_parse_fixture())
    assert all(ord(ch) < 128 for ch in text)
    json.loads(text)
