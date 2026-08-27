"""Leak-channel regression tests.

LEAK-1: `LogLine.marker_body` was computed from the raw, unmasked payload and
flowed into `LogSection.title` (segmentation.py) and `Diagnostic.message`
(process_failure.py), putting raw credentials into `to_json`. Fixed in the
Step 0 hotfix; these tests are the permanent regression home for it.

Every test here needs timestamp-prefixed lines: `detect_provider` requires a
timestamp within the first 50 lines, otherwise it falls through to
`GenericProvider`, `##[...]` markers are never parsed, and a marker-dependent
leak cannot fire at all. A test without timestamps passes vacuously.
"""

import dataclasses

from agentic_pr_analyzer.parsing import parse_log, to_json
from agentic_pr_analyzer.parsing.model import LogLine

# ghp_ + a 36-char run of one character: matches the token regex by shape but
# fails GitHub's trailing CRC32 checksum, so it cannot be a real credential and
# will not trip a secret scanner. Same convention as test_parsing_sanitize.py.
FAKE_TOKEN = "ghp_" + "A" * 36

TS = "2026-08-27T10:00:0"


def _log(*payloads: str) -> str:
    return "\n".join(f"{TS}{i % 10}.0000000Z {p}" for i, p in enumerate(payloads))


def test_group_marker_body_is_masked_in_section_title():
    report = parse_log(
        _log(
            f'##[group]Run curl -H "Authorization: Bearer {FAKE_TOKEN}"',
            "some output",
            "##[endgroup]",
        )
    )

    assert report.sections, "expected the ##[group] to open a section"
    title = report.sections[0].title
    assert FAKE_TOKEN not in title
    assert "REDACTED" in title
    assert FAKE_TOKEN not in to_json(report)


def test_error_marker_body_is_masked_in_process_failure_message():
    """Masked *and* the exit code is still extracted -- masking must not eat
    the structure `find_process_failure` keys on."""
    report = parse_log(
        _log(
            "running the thing",
            f"##[error]Process completed with exit code 1 token={FAKE_TOKEN}.",
        )
    )

    assert report.exit_code == 1
    messages = [d.message or "" for d in report.diagnostics]
    assert any("REDACTED" in m for m in messages)
    assert all(FAKE_TOKEN not in m for m in messages)
    assert FAKE_TOKEN not in to_json(report)


def test_every_logline_str_field_is_masked():
    """Structural guard: iterate the dataclass fields rather than naming the
    three we know about, so adding a fourth text field without masking it
    fails here instead of shipping."""
    from agentic_pr_analyzer.parsing.normalizer import normalize
    from agentic_pr_analyzer.parsing.limits import ParseLimits
    from agentic_pr_analyzer.parsing.providers import detect_provider

    content = _log(
        f"##[group]Run deploy --token={FAKE_TOKEN}",
        f"plain line with {FAKE_TOKEN}",
        f"##[error]failed with {FAKE_TOKEN}",
    )
    provider = detect_provider(content[:4000])
    lines, _ = normalize(content, provider, ParseLimits())

    str_fields = [f.name for f in dataclasses.fields(LogLine)]
    for line in lines:
        for name in str_fields:
            value = getattr(line, name)
            if isinstance(value, str):
                assert FAKE_TOKEN not in value, f"unmasked secret in LogLine.{name}"


# ==========================================================================
# Section 7. The three tests above shipped with the Step 0 hotfix and are kept
# as permanent regressions. These extend them to the full 13-rule matrix.
# ==========================================================================

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from secret_examples import EXAMPLE_SECRETS  # noqa: E402

from agentic_pr_analyzer.parsing.sanitize import MASK_TOKENS  # noqa: E402

# The five positions a planted literal has to survive. The marker positions
# exist because ##[group]/##[command]/##[error] bodies were the LEAK-1 channel;
# the pytest FAILURES block reaches Diagnostic.message and StackFrame.raw_text.
PLANT_POSITIONS = ["plain", "group", "error", "command", "pytest_failures"]


def _log_with_secret_at(position: str, literal: str) -> str:
    if position == "plain":
        payloads = ["some output", f"echo {literal}", "more output"]
    elif position == "group":
        payloads = [f"##[group]Run deploy --token={literal}", "output", "##[endgroup]"]
    elif position == "error":
        payloads = ["output", f"##[error]failed using {literal}"]
    elif position == "command":
        payloads = [f"##[command]curl -H {literal}", "output"]
    elif position == "pytest_failures":
        payloads = [
            "============================= test session starts ==============================",
            "tests/test_auth.py::test_login FAILED                                     [100%]",
            "================================== FAILURES ===================================",
            "________________________________ test_login ___________________________________",
            "tests\\test_auth.py:10: in test_login",
            f"    connect({literal})",
            f"E   RuntimeError: refused with {literal}",
            "=========================== short test summary info ===========================",
            f"FAILED tests/test_auth.py::test_login - RuntimeError: refused with {literal}",
            "##[error]Process completed with exit code 1.",
        ]
    else:
        raise AssertionError(position)
    return _log(*payloads)


def test_command_marker_body_is_masked():
    literal = EXAMPLE_SECRETS["http_auth"]
    report = parse_log(_log(f'##[command]git config --global http.extraheader "{literal}"'))
    assert "EXAMPLEBASE64BLOB" not in to_json(report)


def test_command_marker_masking_does_not_break_tool_detection():
    """CompilerParser.detect and JsTestParser.detect read marker_body looking
    for tool keywords. Masking must not eat those."""
    report = parse_log(
        _log(
            "##[command]npx tsc --noEmit",
            "src/auth.ts:42:7 - error TS2339: Property 'x' does not exist on type 'User'.",
            "##[error]Process completed with exit code 2.",
        )
    )
    assert any(d.type.value == "compiler_error" for d in report.diagnostics)


@pytest.mark.parametrize("cls", sorted(EXAMPLE_SECRETS))
@pytest.mark.parametrize("position", PLANT_POSITIONS)
def test_no_known_secret_literal_survives_into_to_json(cls, position):
    """The headline invariant: 13 classes x 5 positions = 65 cases."""
    literal = EXAMPLE_SECRETS[cls]
    report = parse_log(_log_with_secret_at(position, literal))
    output = to_json(report)

    # The variable body is what must not survive. For env_secret the rule
    # deliberately preserves the variable NAME, so check the value half.
    needle = literal.split("=", 1)[1] if cls == "env_secret" else literal
    assert needle not in output, f"{cls} leaked via {position}"


def test_masking_survives_ansi_escape_inside_token():
    """ANSI is stripped BEFORE masking so an escape embedded inside a token
    cannot split it past the pattern. This is the test that catches getting
    the single-mask fast path's order backwards."""
    split_token = "ghp_" + "A" * 18 + "\x1b[0m" + "A" * 18
    report = parse_log(_log(f"token={split_token}"))
    output = to_json(report)
    assert ("ghp_" + "A" * 36) not in output
    assert "A" * 18 not in output


def test_process_failure_still_parses_when_error_body_is_masked():
    literal = EXAMPLE_SECRETS["github_token"]
    report = parse_log(
        _log(f"##[error]Process completed with exit code 1. token={literal}")
    )
    assert report.exit_code == 1
    assert MASK_TOKENS["github_token"] in (report.diagnostics[0].message or "")
