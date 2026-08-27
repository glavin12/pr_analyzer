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


# ==========================================================================
# Section 7: property-style fuzzing of the expanded matrix. Deterministic
# pseudo-randomness via a seeded random.Random -- property-based *style*
# without adding hypothesis (CLAUDE.md §6: keep the dependency surface small).
# ==========================================================================

import random
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from secret_examples import EXAMPLE_SECRETS  # noqa: E402

from agentic_pr_analyzer.parsing.sanitize import mask  # noqa: E402

ANCHOR = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")

_MARKER_PREFIXES = ["", "##[group]", "##[error]", "##[command]", "[command]"]
_ANSI_NOISE = ["", "\x1b[31m", "\x1b[0m", "\x1b[1;32m"]


def _needle(cls: str, literal: str) -> str:
    # rule 8 deliberately preserves the variable NAME and masks only the value.
    return literal.split("=", 1)[1] if cls == "env_secret" else literal


def test_planted_secret_never_survives_random_surroundings():
    rng = random.Random(20260827)
    classes = sorted(EXAMPLE_SECRETS)

    for _ in range(500):
        cls = rng.choice(classes)
        literal = EXAMPLE_SECRETS[cls]
        payload = (
            rng.choice(_MARKER_PREFIXES)
            + rng.choice(["", "prefix ", "Run deploy --token="])
            + rng.choice(_ANSI_NOISE)
            + literal
            + rng.choice(_ANSI_NOISE)
            + rng.choice(["", " suffix", '" trailing'])
        )
        timestamp = "2026-08-27T13:00:00.0000000Z " if rng.random() < 0.8 else ""
        report = parse_log(timestamp + payload + "\n")
        assert _needle(cls, literal) not in to_json(report), (cls, payload)


def test_planted_secret_never_survives_random_line_position():
    rng = random.Random(20260828)
    base = ANCHOR.read_bytes().decode("utf-8", errors="replace").splitlines()[:200]
    classes = sorted(EXAMPLE_SECRETS)

    for _ in range(200):
        cls = rng.choice(classes)
        literal = EXAMPLE_SECRETS[cls]
        lines = list(base)
        lines.insert(
            rng.randrange(len(lines) + 1),
            "2026-08-27T13:00:00.0000000Z echo " + literal,
        )
        report = parse_log("\n".join(lines))
        assert _needle(cls, literal) not in to_json(report), cls


def test_mask_never_raises_on_random_bytes():
    rng = random.Random(20260829)
    for _ in range(1000):
        s = "".join(chr(rng.randint(0, 255)) for _ in range(rng.randint(0, 80)))
        assert isinstance(mask(s), str)


def test_parse_log_still_never_raises_with_full_matrix():
    """The total-function guarantee, re-asserted against the expanded matrix."""
    rng = random.Random(20260830)
    for _ in range(300):
        s = "".join(
            chr(rng.choice([rng.randint(0, 0x10FFFF), rng.randint(0, 0x7F), 0xDCFF]))
            for _ in range(rng.randint(0, 50))
        )
        report = parse_log(s)
        assert report is not None
        to_json(report)


@pytest.mark.parametrize(
    "adversarial",
    [
        "a" * 20_000,
        "A" * 10_000 + "=",
        "://" + "a" * 10_000,
        "Bearer " + "A" * 19_000,
        "-----BEGIN " + "A" * 19_000,
        "TOKEN=" + "x" * 19_000,
    ],
)
def test_no_rule_exhibits_pathological_backtracking(adversarial):
    """max_line_length is 20,000, so that is the real worst case. Generous
    bound: this catches catastrophic backtracking, not slow machines."""
    start = time.perf_counter()
    mask(adversarial)
    assert time.perf_counter() - start < 1.0
