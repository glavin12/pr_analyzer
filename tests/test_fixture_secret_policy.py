"""Section 7: the executable half of the fixture-vetting policy (P3).

`save_raw_log` writes captured logs **byte-faithful and unmasked** to disk --
deliberately, because the parser needs messy real input. That is exactly why
this policy is load-bearing: a secret that survives into a committed fixture is
published to a public GitHub repo, permanently, in git history, and cannot be
un-published.

**P3 is a shape rule, not an allowlist.** There is no per-file exemption list to
go stale: a match is acceptable only if it is GitHub's own mask literal `***`
or an F2-compliant obviously-fake literal. A new fixture carrying a
live-shaped secret fails the suite the moment it is added.

**P4 is the human step, and it is not automated.** P3 cannot see what the
matrix cannot match: an internal hostname, a customer name, a private S3
bucket, an employee email. Whoever commits a new capture reads its diff first.
Saying that plainly here beats pretending the test covers it.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from secret_examples import EXAMPLE_SECRETS, is_obviously_fake  # noqa: E402

from agentic_pr_analyzer.parsing import parse_log, to_json  # noqa: E402
from agentic_pr_analyzer.parsing.model import DiagnosticType  # noqa: E402
from agentic_pr_analyzer.parsing.sanitize import SECRET_CLASSES, mask_counted  # noqa: E402

FIXTURES = Path("tests/fixtures")
RAW_LOGS = FIXTURES / "raw_logs"
PARSED = FIXTURES / "parsed"
SECRETS_FIXTURE = RAW_LOGS / "SYNTHETIC" / "secrets-sample" / "sample.log"


def _rules():
    """The shipped matrix, as (class_name, compiled_pattern) pairs."""
    from agentic_pr_analyzer.parsing import sanitize

    return [(name, pattern) for name, pattern, _ in sanitize._RULES]


def _matches_in(text: str):
    for name, pattern in _rules():
        for match in pattern.finditer(text):
            yield name, match.group(0)


def _acceptable(span: str) -> bool:
    # GitHub Actions already masks registered secrets to *** at the source.
    return "***" in span or is_obviously_fake(span)


@pytest.mark.parametrize(
    "path", sorted(RAW_LOGS.rglob("*.log")), ids=lambda p: str(p.relative_to(RAW_LOGS))
)
def test_committed_raw_log_fixtures_contain_no_live_shaped_secrets(path):
    text = path.read_bytes().decode("utf-8", errors="replace")
    offenders = [
        (name, span) for name, span in _matches_in(text) if not _acceptable(span)
    ]
    assert not offenders, f"{path}: live-shaped secret(s) {offenders}"


@pytest.mark.parametrize(
    "path", sorted(PARSED.rglob("*.json")), ids=lambda p: str(p.relative_to(PARSED))
)
def test_committed_parsed_fixtures_contain_no_live_shaped_secrets(path):
    """The *output* fixtures -- the ones published in a public repo."""
    text = path.read_text(encoding="utf-8")
    offenders = [
        (name, span) for name, span in _matches_in(text) if not _acceptable(span)
    ]
    assert not offenders, f"{path}: live-shaped secret(s) {offenders}"


def test_synthetic_secret_fixture_contains_only_obviously_fake_secrets():
    """F3. If someone pastes a real token into this fixture, the suite fails
    before the commit."""
    text = SECRETS_FIXTURE.read_text(encoding="utf-8")
    for name, span in _matches_in(text):
        # Same predicate as the committed-fixture scan above, deliberately:
        # `***` is GitHub Actions' own masking of a registered secret, not a
        # planted literal, so it is acceptable wherever it appears. F2 governs
        # the literals WE plant.
        assert _acceptable(span), f"{name}: {span!r} is not obviously fake"


def test_synthetic_secret_fixture_exercises_every_shipped_rule():
    """Prevents a rule shipping without a fixture. Failing here tells you
    exactly which rules the fixture is still missing."""
    text = SECRETS_FIXTURE.read_text(encoding="utf-8")
    fired = {name for name, _ in _matches_in(text)}
    assert fired == set(SECRET_CLASSES), f"never fired: {sorted(set(SECRET_CLASSES) - fired)}"


def test_synthetic_secret_fixture_parses_to_a_fully_masked_report():
    """End-to-end: masking preserved parseability *and* let nothing through."""
    report = parse_log(SECRETS_FIXTURE.read_text(encoding="utf-8"))
    output = to_json(report)

    for cls, literal in EXAMPLE_SECRETS.items():
        needle = literal.split("=", 1)[1] if cls == "env_secret" else literal
        assert needle not in output, f"{cls} survived into the parsed report"

    assert report.exit_code == 1
    assert any(d.type is DiagnosticType.PROCESS_FAILURE for d in report.diagnostics)


def test_synthetic_secret_fixture_preserves_negative_controls():
    """Over-masking is the top risk in Section 7. These are evidence, and they
    must survive verbatim into the report."""
    from secret_examples import NEGATIVE_CONTROLS

    masked = mask_counted(SECRETS_FIXTURE.read_text(encoding="utf-8"))[0]
    for name, literal in NEGATIVE_CONTROLS.items():
        assert literal in masked, f"{name} was over-masked"


def test_masked_counts_are_reported_for_the_secrets_fixture():
    text = SECRETS_FIXTURE.read_text(encoding="utf-8")
    _, counts = mask_counted(text)
    assert set(counts) == set(SECRET_CLASSES)
