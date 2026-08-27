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


# --------------------------------------------------------------------------
# Step 0 hotfix: build_sections was O(n*depth) -- it rewrote end_lineno for
# every section on the open stack, for every line. These pin the linear
# rewrite's behaviour to the old algorithm's, exactly.
# --------------------------------------------------------------------------

import random
import time

import dataclasses as _dc

from agentic_pr_analyzer.parsing.model import LogLine, LogSection, WorkflowMarker


def _reference_build_sections(lines):
    """Frozen verbatim copy of the pre-hotfix O(n*depth) algorithm.

    The differential property test below compares the linear implementation
    against *this*, not against a hand-written expectation, so the rewrite is
    proven equivalent rather than merely plausible.
    """
    built: dict[int, dict] = {}
    open_stack: list[int] = []
    next_id = 0
    new_lines: list[LogLine] = []

    for line in lines:
        if line.marker == WorkflowMarker.GROUP:
            parent_id = open_stack[-1] if open_stack else None
            sec_id = next_id
            next_id += 1
            built[sec_id] = {
                "id": sec_id,
                "title": line.marker_body,
                "kind": "group",
                "start_lineno": line.raw_lineno,
                "end_lineno": line.raw_lineno,
                "parent_id": parent_id,
            }
            open_stack.append(sec_id)
            for open_id in open_stack:
                built[open_id]["end_lineno"] = line.raw_lineno
            new_lines.append(_dc.replace(line, section_id=sec_id))
            continue

        if line.marker == WorkflowMarker.ENDGROUP and open_stack:
            for open_id in open_stack:
                built[open_id]["end_lineno"] = line.raw_lineno
            sec_id = open_stack.pop()
            new_lines.append(_dc.replace(line, section_id=sec_id))
            continue

        if open_stack:
            for open_id in open_stack:
                built[open_id]["end_lineno"] = line.raw_lineno
            new_lines.append(_dc.replace(line, section_id=open_stack[-1]))
        else:
            new_lines.append(line)

    sections = tuple(LogSection(**built[i]) for i in sorted(built))
    return tuple(new_lines), sections


def _lines_from(payloads):
    content = "\n".join(f"2026-01-01T00:00:00.0000000Z {p}" for p in payloads)
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    return lines


def test_build_sections_is_linear_in_deep_nesting():
    """20,000 nested groups. The old algorithm took ~5.8s here because each
    line rewrote the whole open stack; the linear one is milliseconds. The
    ceiling is deliberately loose -- it exists to catch the reintroduction of
    a quadratic scan, not to benchmark the machine."""
    lines = _lines_from(["##[group]G"] * 20_000)
    start = time.perf_counter()
    _, sections = build_sections(lines)
    elapsed = time.perf_counter() - start

    assert len(sections) == 20_000
    assert elapsed < 2.0, f"build_sections took {elapsed:.2f}s -- quadratic scan is back"


def test_build_sections_end_lineno_matches_last_line_in_scope():
    lines = _lines_from(
        ["##[group]Outer", "##[group]Inner", "inside", "##[endgroup]", "outer tail", "##[endgroup]"]
    )
    _, sections = build_sections(lines)
    outer = next(s for s in sections if s.title == "Outer")
    inner = next(s for s in sections if s.title == "Inner")

    # end_lineno is the last line seen while the section was open -- for a
    # closed section that is its own ##[endgroup] line.
    assert (inner.start_lineno, inner.end_lineno) == (2, 4)
    assert (outer.start_lineno, outer.end_lineno) == (1, 6)


def test_build_sections_unbalanced_open_closes_at_last_line():
    lines = _lines_from(["##[group]Unclosed", "a", "b", "c"])
    _, sections = build_sections(lines)
    assert len(sections) == 1
    assert sections[0].end_lineno == 4


def test_build_sections_unbalanced_close_is_ignored():
    lines = _lines_from(["##[endgroup]", "after"])
    new_lines, sections = build_sections(lines)
    assert sections == ()
    assert all(line.section_id is None for line in new_lines)


def test_linear_segmentation_matches_reference_over_random_marker_soup():
    """Differential property test over 3000 seeded random marker traces.

    Full tuple equality against the frozen reference -- lines *and* sections,
    so section_id assignment, id numbering, parent links and both line bounds
    are all covered.
    """
    rng = random.Random(20260827)
    alphabet = ["##[group]G", "##[endgroup]", "##[error]boom", "[command]run", "plain"]

    for _ in range(3000):
        payloads = [rng.choice(alphabet) for _ in range(rng.randint(0, 25))]
        lines = _lines_from(payloads) if payloads else []
        assert build_sections(lines) == _reference_build_sections(lines)
