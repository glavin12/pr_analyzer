import json

from agentic_pr_analyzer.parsing import to_json
from agentic_pr_analyzer.parsing.model import (
    SCHEMA_VERSION,
    Diagnostic,
    DiagnosticType,
    FailureReport,
    Severity,
)
from agentic_pr_analyzer.parsing.pipeline import parse_log


def test_to_json_handles_lone_surrogate_without_crashing():
    diag = Diagnostic(
        type=DiagnosticType.UNKNOWN,
        severity=Severity.ERROR,
        tool=None,
        message="bad path \udcff",
        file=None,
        line=None,
        column=None,
        source_range=None,
        stack_trace=None,
        test_id=None,
        exit_code=None,
        confidence=0.4,
        evidence=(),
        metadata={},
        parser="generic",
    )
    report = FailureReport(
        schema_version=SCHEMA_VERSION,
        source=None,
        provider="generic",
        sections=(),
        diagnostics=(diag,),
        clusters=(),
        primary_cluster=None,
        exit_code=None,
        raw_line_count=1,
        truncated=False,
        stats={},
    )
    text = to_json(report)
    assert all(ord(ch) < 128 for ch in text)
    json.loads(text)


def test_parse_log_never_raises_on_random_bytes():
    garbage = bytes(range(256)).decode("latin-1")
    report = parse_log(garbage)
    assert report is not None
    to_json(report)


def test_parse_log_never_raises_on_lone_surrogate_input():
    report = parse_log("bad line \udcff more text")
    assert report is not None
    to_json(report)


def test_parse_log_never_raises_on_empty_input():
    report = parse_log("")
    assert report.raw_line_count == 0


def test_parse_log_never_raises_on_one_huge_line():
    report = parse_log("x" * 5_000_000)
    assert report is not None


def test_parse_log_never_raises_on_pure_ansi_noise():
    report = parse_log("\x1b[31m\x1b[1m\x1b[0m" * 1000)
    assert report is not None


def test_parse_log_never_raises_on_arbitrary_unicode():
    report = parse_log("café \U0001f600 ☃ ﻿﻿")
    assert report is not None
    to_json(report)


def test_parse_log_masks_planted_github_token():
    content = "2026-01-01T00:00:00.0000000Z token=ghp_" + "a" * 36
    report = parse_log(content)
    text = to_json(report)
    assert ("ghp_" + "a" * 36) not in text
