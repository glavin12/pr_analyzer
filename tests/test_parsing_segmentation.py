from pathlib import Path

from agentic_pr_analyzer.parsing.limits import ParseLimits
from agentic_pr_analyzer.parsing.normalizer import normalize
from agentic_pr_analyzer.parsing.providers.github_actions import GitHubActionsProvider
from agentic_pr_analyzer.parsing.segmentation import build_sections

FIXTURE = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")


def test_nested_groups_form_a_tree():
    content = (
        "2026-01-01T00:00:00.0000000Z ##[group]Outer\n"
        "2026-01-01T00:00:01.0000000Z ##[group]Inner\n"
        "2026-01-01T00:00:02.0000000Z inside inner\n"
        "2026-01-01T00:00:03.0000000Z ##[endgroup]\n"
        "2026-01-01T00:00:04.0000000Z back in outer\n"
        "2026-01-01T00:00:05.0000000Z ##[endgroup]\n"
    )
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    new_lines, sections = build_sections(lines)

    assert len(sections) == 2
    outer = next(s for s in sections if s.title == "Outer")
    inner = next(s for s in sections if s.title == "Inner")
    assert outer.parent_id is None
    assert inner.parent_id == outer.id

    inner_line = next(line for line in new_lines if line.text == "inside inner")
    assert inner_line.section_id == inner.id
    outer_line = next(line for line in new_lines if line.text == "back in outer")
    assert outer_line.section_id == outer.id


def test_unbalanced_group_auto_closes_at_eof():
    content = (
        "2026-01-01T00:00:00.0000000Z ##[group]Unclosed\n"
        "2026-01-01T00:00:01.0000000Z line inside\n"
    )
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    _, sections = build_sections(lines)
    assert len(sections) == 1
    assert sections[0].end_lineno == 2


def test_lines_outside_any_group_have_no_section():
    lines, _ = normalize(
        "2026-01-01T00:00:00.0000000Z no group here\n",
        GitHubActionsProvider(),
        ParseLimits(),
    )
    new_lines, sections = build_sections(lines)
    assert sections == ()
    assert new_lines[0].section_id is None


def test_real_fixture_has_18_top_level_sections():
    content = FIXTURE.read_bytes().decode("utf-8")
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    _, sections = build_sections(lines)
    assert len(sections) == 18
    assert all(s.parent_id is None for s in sections)
    assert all(s.kind == "group" for s in sections)
