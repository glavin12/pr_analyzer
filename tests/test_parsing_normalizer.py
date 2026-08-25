from pathlib import Path

from agentic_pr_analyzer.parsing.limits import ParseLimits
from agentic_pr_analyzer.parsing.model import WorkflowMarker
from agentic_pr_analyzer.parsing.normalizer import normalize
from agentic_pr_analyzer.parsing.providers.github_actions import GitHubActionsProvider

FIXTURE = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")


def _load_fixture_text() -> str:
    return FIXTURE.read_bytes().decode("utf-8")


def test_normalize_bom_is_stripped():
    content = "﻿2026-08-21T10:22:10.5332965Z hello"
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    assert lines[0].text == "hello"
    assert not lines[0].raw_text.startswith("﻿")


def test_normalize_real_fixture_line_count_is_2323():
    lines, _ = normalize(_load_fixture_text(), GitHubActionsProvider(), ParseLimits())
    assert len(lines) == 2323


def test_normalize_raw_lineno_is_stable_and_sequential():
    lines, _ = normalize(_load_fixture_text(), GitHubActionsProvider(), ParseLimits())
    assert [line.raw_lineno for line in lines] == list(range(1, 2324))


def test_normalize_continuation_line_has_no_timestamp():
    lines, _ = normalize(_load_fixture_text(), GitHubActionsProvider(), ParseLimits())
    # raw_lineno 153: a cache-dependency-glob continuation line with no timestamp prefix.
    line = lines[152]
    assert line.raw_lineno == 153
    assert line.timestamp is None
    assert line.text == "**/*requirements*.in"


def test_normalize_timestamped_line_has_timestamp():
    lines, _ = normalize(_load_fixture_text(), GitHubActionsProvider(), ParseLimits())
    assert lines[0].timestamp == "2026-08-21T10:22:10.5332965Z"


def test_normalize_strips_real_ansi_escape():
    lines, _ = normalize(_load_fixture_text(), GitHubActionsProvider(), ParseLimits())
    line_226 = lines[225]
    assert line_226.raw_lineno == 226
    assert "\x1b" not in line_226.text
    assert line_226.text == "uv run --locked --no-default-groups --group dev tox run"


def test_normalize_preserves_literal_backslash_x1b_in_test_ids():
    lines, _ = normalize(_load_fixture_text(), GitHubActionsProvider(), ParseLimits())
    line = lines[2269]
    assert line.raw_lineno == 2270
    assert "\\x1b[38:2:255:0:0m" in line.text


def test_normalize_dual_raw_text_and_text_differ_when_ansi_present():
    lines, _ = normalize(_load_fixture_text(), GitHubActionsProvider(), ParseLimits())
    line_226 = lines[225]
    assert line_226.raw_text != line_226.text
    assert "\x1b" in line_226.raw_text


def test_normalize_sets_marker_and_marker_body():
    lines, _ = normalize(_load_fixture_text(), GitHubActionsProvider(), ParseLimits())
    line2 = lines[1]
    assert line2.raw_lineno == 2
    assert line2.marker == WorkflowMarker.GROUP
    assert line2.marker_body == "Runner Image Provisioner"


def test_normalize_section_id_is_none_before_segmentation():
    lines, _ = normalize(_load_fixture_text(), GitHubActionsProvider(), ParseLimits())
    assert all(line.section_id is None for line in lines)
