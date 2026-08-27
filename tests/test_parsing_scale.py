"""Section 6: scale & robustness. Fast, offline, runs by default.

The perf measurements live in test_parsing_perf.py behind the `perf` marker.
This file holds the correctness half: the bounded split's exact equivalence to
str.splitlines(), the evidence cap, the tail rescue, and the degradation ladder.

The single highest-risk thing in this section is `_bounded_splitlines`
reproducing str.splitlines()'s exact 9-boundary set. Every line number,
evidence tuple, SourceRange, StackFrame.raw_lineno and LogSection bound rests
on it. T-P1 below compares against the REAL str.splitlines() rather than a
hardcoded expectation, so a future CPython change fails the test rather than
silently corrupting the product.
"""

import json
import random
from pathlib import Path

from agentic_pr_analyzer.github.models import load_raw_log
from agentic_pr_analyzer.parsing import ParseLimits, parse_log, to_json
from agentic_pr_analyzer.parsing.model import DiagnosticType, LogSource
from agentic_pr_analyzer.parsing.normalizer import _bounded_splitlines

ANCHOR = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")
GOLDEN = Path("tests/fixtures/parsed/pallets/click/32472305359_96741461054.json")

# The 9 characters/sequences str.splitlines() breaks on, plus ordinary text.
BREAKS = ["\r\n", "\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


def _anchor_content() -> str:
    return ANCHOR.read_bytes().decode("utf-8", errors="replace")


def _anchor_report(limits=None):
    raw = load_raw_log(ANCHOR)
    return parse_log(raw.content, LogSource.from_raw_log(raw), limits or ParseLimits())


# ---------------------------------------------------------------- D2 bounded split


def test_bounded_splitlines_matches_splitlines_when_under_cap():
    s = "a\nb\nc"
    assert _bounded_splitlines(s, 100) == (s.splitlines(), False)


def test_bounded_splitlines_stops_at_cap_without_materializing_rest():
    s = "\n".join(str(i) for i in range(1000))
    lines, truncated = _bounded_splitlines(s, 10)
    assert lines == s.splitlines()[:10]
    assert truncated is True


def test_bounded_splitlines_treats_crlf_as_one_break():
    """CRLF must be ONE break, not two -- hence \\r\\n is first in the pattern."""
    assert _bounded_splitlines("a\r\nb", 100)[0] == ["a", "b"]
    assert _bounded_splitlines("a\r\nb\r\nc", 2)[0] == ["a", "b"]


def test_bounded_splitlines_honours_exotic_breaks():
    s = "a\x0bb\x0cc\x1cd\x85e"
    assert _bounded_splitlines(s, 100)[0] == s.splitlines()
    assert len(s.splitlines()) == 5


def test_bounded_splitlines_on_empty_string():
    assert _bounded_splitlines("", 10) == ([], False)


def test_max_total_chars_truncates_and_flags():
    content = "2026-01-01T00:00:00.0000000Z " + ("x" * 5000) + "\nsecond line\n"
    report = parse_log(content, limits=ParseLimits(max_total_chars=100))
    assert report.stats["truncated_chars"] is True
    assert report.truncated is True


def test_max_total_chars_default_does_not_truncate_anchor():
    report = _anchor_report()
    assert report.stats["truncated_chars"] is False
    assert report.raw_line_count == 2323


# ---------------------------------------------------------------- D4 evidence cap


def test_evidence_capped_to_max_context_lines():
    report = _anchor_report(ParseLimits(max_context_lines=3))
    for diagnostic in report.diagnostics:
        assert len(diagnostic.evidence) <= 3
    assert report.stats["evidence_truncated"] is True


def test_evidence_cap_preserves_first_entry():
    """Reconciliation C3. Section 5 ranks on min(evidence) and derives
    section_id from evidence[0], so the cap MUST keep the head. A tail-biased
    or sampled cap would silently change clustering."""
    full = _anchor_report()
    capped = _anchor_report(ParseLimits(max_context_lines=2))

    by_type = {d.type: d for d in full.diagnostics}
    for diagnostic in capped.diagnostics:
        original = by_type[diagnostic.type]
        if original.evidence:
            assert diagnostic.evidence[0] == original.evidence[0]
            assert min(diagnostic.evidence) == min(original.evidence)


def test_source_range_survives_evidence_cap():
    """The extent lives in source_range; evidence is only a sample. Capping the
    sample must not shrink the extent."""
    full = _anchor_report()
    capped = _anchor_report(ParseLimits(max_context_lines=2))
    assert [d.source_range for d in capped.diagnostics] == [
        d.source_range for d in full.diagnostics
    ]


def test_default_evidence_cap_is_noop_on_anchor():
    report = _anchor_report()
    assert sorted(len(d.evidence) for d in report.diagnostics) == [1, 25]
    assert report.stats["evidence_truncated"] is False


def test_report_size_bounded_by_max_context_lines():
    """A giant FAILURES block used to produce a 7.2 MB JSON report from a ~6 MB
    log, because nothing bounded evidence *size* -- only diagnostic count."""
    header = [
        "2026-01-01T00:00:00.0000000Z ============================= test session starts ==============================",
        "2026-01-01T00:00:00.0000000Z ================================== FAILURES ===================================",
        "2026-01-01T00:00:00.0000000Z ________________________________ test_big _____________________________________",
        "2026-01-01T00:00:00.0000000Z tests\\test_big.py:1: in test_big",
    ]
    body = ["2026-01-01T00:00:00.0000000Z     filler line %d" % i for i in range(150_000)]
    tail = [
        "2026-01-01T00:00:00.0000000Z E   AssertionError: boom",
        "2026-01-01T00:00:00.0000000Z ##[error]Process completed with exit code 1.",
    ]
    report = parse_log("\n".join(header + body + tail))
    assert len(to_json(report)) < 2_000_000


# ---------------------------------------------------------------- D5 tail rescue


def test_head_truncation_still_recovers_exit_code_from_tail():
    """CI's highest-value artifacts live at the tail, which head truncation is
    guaranteed to drop. Before the rescue this returned exit_code None."""
    report = _anchor_report(ParseLimits(max_total_lines=2290))
    assert report.truncated is True
    assert report.exit_code == 1
    assert report.stats["tail_rescued"] is True


def test_tail_rescued_diagnostic_makes_no_line_claims():
    """The rescued diagnostic is normalized from a raw tail slice whose line
    numbers do not correspond to the report's. It therefore claims none --
    that is what keeps line-number fidelity exact."""
    report = _anchor_report(ParseLimits(max_total_lines=2290))
    rescued = [
        d for d in report.diagnostics if d.metadata.get("from_truncated_tail") is True
    ]
    assert len(rescued) == 1
    assert rescued[0].evidence == ()
    assert rescued[0].source_range is None
    assert rescued[0].type is DiagnosticType.PROCESS_FAILURE


def test_no_tail_rescue_when_not_truncated():
    report = _anchor_report()
    assert report.stats["tail_rescued"] is False
    assert all(not d.metadata.get("from_truncated_tail") for d in report.diagnostics)


def test_no_duplicate_process_failure_when_head_already_has_one():
    """Truncated, but the head already contains the ##[error] line -- the
    rescue must not fire and add a second one."""
    report = _anchor_report(ParseLimits(max_total_lines=2310))
    assert report.truncated is True
    assert report.stats["tail_rescued"] is False
    process_failures = [
        d for d in report.diagnostics if d.type is DiagnosticType.PROCESS_FAILURE
    ]
    assert len(process_failures) == 1


# ---------------------------------------------------------------- degradation ladder


def test_truncated_mid_traceback_still_locates_the_file():
    report = _anchor_report(ParseLimits(max_total_lines=2290))
    test_failures = [d for d in report.diagnostics if d.type is DiagnosticType.TEST_FAILURE]
    assert len(test_failures) == 1
    assert test_failures[0].file == "tests\\test_types.py"


def test_truncated_mid_traceback_degrades_message_not_crashes():
    """Documents the accuracy loss rather than pretending it doesn't happen:
    with the short-summary line cut off, `message` falls back to the stack's
    exception message."""
    full = _anchor_report()
    truncated = _anchor_report(ParseLimits(max_total_lines=2290))
    full_msg = next(d.message for d in full.diagnostics if d.type is DiagnosticType.TEST_FAILURE)
    trunc_msg = next(
        d.message for d in truncated.diagnostics if d.type is DiagnosticType.TEST_FAILURE
    )
    assert trunc_msg is not None
    assert trunc_msg != full_msg


def test_binary_bytes_produce_splitlines_boundaries_not_newline_boundaries():
    """Locks §1.4 so a future streaming attempt fails loudly: these 256 bytes
    contain ONE \\n but str.splitlines() finds 9 lines. Stream iteration would
    find 2 -- a determinism break against parse_log."""
    content = bytes(range(256)).decode("latin-1")
    assert content.count("\n") == 1
    assert len(content.splitlines()) == 9
    report = parse_log(content)
    assert report.raw_line_count == 9


def test_parse_log_total_on_exotic_line_breaks():
    report = parse_log("a\x0bb\x0cc\x1cd\x1de\x1ef\x85g")
    assert report.raw_line_count == 7
    json.loads(to_json(report))


def test_parse_log_total_on_whitespace_only():
    assert parse_log("   \t  \t   ") is not None


def test_parse_log_total_on_newlines_only():
    report = parse_log("\n\n\n\n")
    assert report.raw_line_count == 4


# ---------------------------------------------------------------- golden / determinism


def test_bounded_split_path_matches_reference_splitlines_on_anchor():
    """T-G1."""
    content = _anchor_content()
    assert _bounded_splitlines(content, 200_000) == (content.splitlines(), False)
    assert _bounded_splitlines(content, 3)[0] == content.splitlines()[:3]


def test_golden_snapshot_diagnostics_subtree_unchanged_by_section6():
    """T-G2. Section 6 regenerates the golden snapshot three times. This is
    what stops a regeneration from hiding a regression: only `stats` may move.
    Everything that describes the actual failure must be untouched."""
    raw = load_raw_log(ANCHOR)
    actual = json.loads(to_json(parse_log(raw.content, LogSource.from_raw_log(raw))))
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))

    for key in ("diagnostics", "sections", "clusters", "exit_code", "raw_line_count"):
        assert actual[key] == expected[key], f"Section 6 must not change {key!r}"


# ---------------------------------------------------------------- property-based

# Seeded stdlib random, not hypothesis. These properties are DIFFERENTIAL (new
# impl == stdlib impl) over a TINY alphabet, where seeded random is
# near-exhaustive rather than a weaker approximation. Hypothesis's real value is
# shrinking a failure to a minimal example -- worth a dependency when properties
# are complex or stateful. Revisit if a later section needs stateful or
# multi-argument properties.


def test_bounded_splitlines_equals_splitlines_over_random_break_soup():
    """T-P1. The differential guard on the highest-risk change in Section 6.
    Compares against the REAL str.splitlines(), so a CPython boundary-set change
    fails this test rather than silently corrupting every line number."""
    rng = random.Random(20260827)
    alphabet = ["a", "b", " "] + BREAKS + [""]
    for _ in range(20_000):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 25)))
        for cap in (1, 2, 3, 5, 100):
            lines, _ = _bounded_splitlines(s, cap)
            assert lines == s.splitlines()[:cap], (repr(s), cap)


def test_parse_log_is_total_over_random_byte_soup():
    """T-P2."""
    rng = random.Random(20260828)
    for _ in range(2000):
        s = "".join(
            chr(rng.choice([rng.randint(0, 0x10FFFF), rng.randint(0, 0x7F), 0xFEFF, 0xDCFF]))
            for _ in range(rng.randint(0, 40))
        )
        report = parse_log(s)
        json.loads(to_json(report))


def test_no_parsing_regex_is_superlinear():
    """T-P3. Auto-discovers every compiled pattern reachable from parsing.*
    module globals, so a regex added by a future parser is covered for free.
    50 ms ceiling against a measured worst case of 1.72 ms -- catches
    catastrophic backtracking without being CI-flaky."""
    import importlib
    import pkgutil
    import re
    import time

    import agentic_pr_analyzer.parsing as parsing_pkg

    patterns = []
    for mod_info in pkgutil.walk_packages(parsing_pkg.__path__, parsing_pkg.__name__ + "."):
        module = importlib.import_module(mod_info.name)
        patterns.extend(v for v in vars(module).values() if isinstance(v, re.Pattern))

    assert patterns, "expected to discover compiled patterns in parsing.*"

    rng = random.Random(20260829)
    charset = "aA0 =:/\\-_.\x1b[]()<>'\"#"
    for size in (2_000, 8_000, 20_000):
        adversarial = [
            "a" * size,
            "A" * (size // 2) + "=",
            "://" + "a" * size,
            "".join(rng.choice(charset) for _ in range(size)),
        ]
        for pattern in patterns:
            for text in adversarial:
                start = time.perf_counter()
                pattern.match(text)
                pattern.search(text)
                elapsed = time.perf_counter() - start
                assert elapsed < 0.05, f"{pattern.pattern!r} took {elapsed:.3f}s at size {size}"
