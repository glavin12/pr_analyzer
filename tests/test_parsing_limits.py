from agentic_pr_analyzer.parsing.limits import ParseLimits
from agentic_pr_analyzer.parsing.normalizer import normalize
from agentic_pr_analyzer.parsing.pipeline import parse_log
from agentic_pr_analyzer.parsing.providers.generic import GenericProvider


def test_over_long_line_is_truncated_and_flagged():
    limits = ParseLimits(max_line_length=10)
    lines, stats = normalize("x" * 500, GenericProvider(), limits)
    assert len(lines[0].text) <= 10
    assert stats["lines_over_limit"] == 1


def test_too_many_lines_is_truncated_and_flagged():
    limits = ParseLimits(max_total_lines=5)
    content = "\n".join(f"line {i}" for i in range(20))
    lines, stats = normalize(content, GenericProvider(), limits)
    assert len(lines) == 5
    assert stats["truncated_lines"] is True


def test_default_limits_do_not_truncate_normal_input():
    lines, stats = normalize(
        "short line\nanother line\n", GenericProvider(), ParseLimits()
    )
    assert stats["truncated_lines"] is False
    assert stats["lines_over_limit"] == 0


def test_parse_log_reports_truncated_when_line_count_limit_exceeded():
    limits = ParseLimits(max_total_lines=3)
    content = "\n".join(f"line {i}" for i in range(10))
    report = parse_log(content, limits=limits)
    assert report.truncated is True
    assert report.raw_line_count == 3


def test_parse_log_never_crashes_when_limits_exceeded_badly():
    limits = ParseLimits(max_total_lines=1, max_line_length=1, max_diagnostics=0)
    report = parse_log("some long first line\nsecond line\n", limits=limits)
    assert report is not None
    assert report.truncated is True
