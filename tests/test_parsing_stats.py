"""Section 7 observability: the stats contract.

The load-bearing test here is invariant S7-M1 -- **no value in `stats` is ever
derived from log text**. It is enforced twice: a *content* test (plant a unique
sentinel on every line, assert it appears nowhere in json.dumps(stats)) and a
*structural* test (walk stats recursively, assert every leaf is a permitted
scalar or a member of its declared closed set).

The structural one is the important one: it catches "someone put a message in
stats" even when no sentinel happens to be present.
"""

import json
from pathlib import Path

import pytest

from agentic_pr_analyzer.github.models import load_raw_log
from agentic_pr_analyzer.parsing import parse_log
from agentic_pr_analyzer.parsing.model import LogSource
from agentic_pr_analyzer.parsing.parsers import PARSER_NAMES
from agentic_pr_analyzer.parsing.sanitize import SECRET_CLASSES

ANCHOR = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")

EXPECTED_KEYS = {
    # volume
    "logs_processed",
    "lines_processed",
    "bytes_processed",
    "sections",
    "unknown_sections",
    # detection
    "diagnostics_detected",
    "diagnostics_deduplicated",
    "clusters_built",
    "parser_selected",
    "parsers_fired",
    "fallback_parser_used",
    "parse_failures",
    # limits / degradation (Section 6)
    "truncated_lines",
    "lines_over_limit",
    "diagnostics_truncated",
    "truncated_chars",
    "evidence_truncated",
    "tail_rescued",
    "peak_lines_retained",
    "fatal",
    # security
    "secrets_masked",
    "secrets_masked_by_class",
    # non-deterministic bucket, excluded from golden snapshots
    "runtime",
}

TS = "2026-08-27T12:00:00.0000000Z "


def _anchor_report():
    raw = load_raw_log(ANCHOR)
    return parse_log(raw.content, LogSource.from_raw_log(raw))


# ---------------------------------------------------------------- key set


def test_stats_has_exactly_the_documented_key_set():
    assert set(_anchor_report().stats) == EXPECTED_KEYS


def test_fatal_report_has_the_same_stats_key_set(monkeypatch):
    """A consumer must never have to branch on key presence -- today
    stats["fatal"] exists only on the fatal path."""
    import agentic_pr_analyzer.parsing.pipeline as pipeline

    def boom(*args, **kwargs):
        raise RuntimeError("induced")

    monkeypatch.setattr(pipeline, "normalize", boom)
    report = parse_log(TS + "anything\n")

    assert set(report.stats) == EXPECTED_KEYS
    assert report.stats["fatal"] is True
    assert report.stats["logs_processed"] == 1


def test_success_path_reports_fatal_false():
    assert _anchor_report().stats["fatal"] is False


# ---------------------------------------------------------------- runtime bucket


def test_stats_runtime_bucket_is_present_and_shaped():
    """Keeps the golden-snapshot exclusion honest: exactly one key is dropped
    from the comparison, so its shape has to be asserted somewhere else."""
    runtime = _anchor_report().stats["runtime"]
    assert set(runtime) == {"parse_ms"}
    assert isinstance(runtime["parse_ms"], int)
    assert runtime["parse_ms"] >= 0


# ---------------------------------------------------------------- S7-M1


def _walk_leaves(value, path="stats"):
    if isinstance(value, dict):
        for key, sub in value.items():
            assert isinstance(key, str), f"{path}: non-str key {key!r}"
            yield from _walk_leaves(sub, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            yield from _walk_leaves(sub, f"{path}[{i}]")
    else:
        yield path, value


def test_stats_leaf_types_are_scalars_or_closed_set_strings():
    """S7-M1, structural."""
    stats = _anchor_report().stats
    closed_strings = set(PARSER_NAMES) | {"specialized"}

    for path, leaf in _walk_leaves(stats):
        assert isinstance(leaf, (int, float, bool, type(None), str)), f"{path}={leaf!r}"
        if isinstance(leaf, str):
            assert leaf in closed_strings, f"{path} carries free-form string {leaf!r}"

    for cls in stats["secrets_masked_by_class"]:
        assert cls in SECRET_CLASSES


def test_stats_never_contains_log_text():
    """S7-M1, content. Every line carries a unique sentinel; none may appear in
    the serialized stats."""
    lines = [f"{TS}SENTINEL{i}ZZ line body here" for i in range(50)]
    lines.append(TS + "##[error]Process completed with exit code 1.")
    report = parse_log("\n".join(lines))

    serialized = json.dumps(report.stats)
    for i in range(50):
        assert f"SENTINEL{i}ZZ" not in serialized


# ---------------------------------------------------------------- parser attribution


def test_parsers_fired_names_specialized_parsers():
    raw = load_raw_log(ANCHOR)
    report = parse_log(raw.content, LogSource.from_raw_log(raw))
    assert report.stats["parsers_fired"] == ["pytest"]
    assert report.stats["fallback_parser_used"] is False
    assert report.stats["parser_selected"] == "specialized"


def test_fallback_parser_used_true_for_generic_only_log():
    report = parse_log(TS + "src/main.py:42:7: error: unexpected token\n")
    assert report.stats["fallback_parser_used"] is True
    assert report.stats["parsers_fired"] == []
    assert report.stats["parser_selected"] == "generic"


def test_unknown_sections_counts_anonymous_groups():
    """Pins the CHOSEN definition: a LogSection whose title is None, i.e. an
    anonymous or unbalanced ##[group]. The engine has no section taxonomy, so
    the other reading of "unknown" would be meaningless."""
    anonymous = parse_log(TS + "##[group]\n" + TS + "body\n" + TS + "##[endgroup]\n")
    assert anonymous.stats["unknown_sections"] == 1

    titled = parse_log(TS + "##[group]Run x\n" + TS + "body\n" + TS + "##[endgroup]\n")
    assert titled.stats["unknown_sections"] == 0


# ---------------------------------------------------------------- volume / security


def test_bytes_processed_is_input_length():
    content = TS + "hello\n"
    assert parse_log(content).stats["bytes_processed"] == len(content)


def test_peak_lines_retained_equals_lines_processed():
    """Section 6 declined streaming, so this permanently equals
    lines_processed. It stays as the deterministic memory proxy alongside
    bytes_processed, and says so honestly rather than promising a
    designed-away future."""
    stats = _anchor_report().stats
    assert stats["peak_lines_retained"] == stats["lines_processed"]


def test_secrets_masked_counts_match_planted_secrets():
    token = "ghp_" + "A" * 36
    report = parse_log(
        f"{TS}first {token}\n{TS}second {token} and {token}\n"
    )
    assert report.stats["secrets_masked"] == 3
    assert report.stats["secrets_masked_by_class"] == {"github_token": 3}


def test_secrets_masked_on_the_anchor_is_exactly_githubs_own_placeholder():
    """Guards against a pattern over-firing on ordinary CI text.

    The real anchor contains ONE credential-shaped span across 2323 lines:
    line 79's `AUTHORIZATION: basic ***`, where the `***` is GitHub Actions'
    own masking of a registered secret. Rule 4 matching it is correct, and it
    is harmless -- that line is a `[command]` echo, so it never becomes a
    LogSection.title or a Diagnostic.message (proved by the byte-identical
    text canary in test_parsing_golden_snapshot.py).

    Asserting zero here would contradict the design's own measured baseline
    of "1 match, 0 false positives" over the committed corpus. Any OTHER
    class appearing, or a second match, means a rule started over-firing.
    """
    report = _anchor_report()
    assert report.stats["secrets_masked_by_class"] == {"http_auth": 1}
    assert report.stats["secrets_masked"] == 1


def test_secrets_masked_is_zero_for_a_log_with_no_credential_shapes():
    report = parse_log(TS + "Successfully set up CPython (3.14.7)\n")
    assert report.stats["secrets_masked"] == 0
    assert report.stats["secrets_masked_by_class"] == {}


def test_secrets_masked_is_not_double_counted_across_text_and_raw_text():
    """text and raw_text cover the same spans, so counts merge as a per-class
    MAX, not a sum -- otherwise every masked line double-counts."""
    token = "ghp_" + "A" * 36
    report = parse_log(f"{TS}\x1b[31mvalue={token}\x1b[0m\n")
    assert report.stats["secrets_masked"] == 1


# ---------------------------------------------------------------- reconciliations


def test_section5_owned_stats_keys_are_not_clobbered():
    """Reconciliation C2. _base_stats() supplies DEFAULTS for these two;
    the clustering stage OVERWRITES them. Assigning them unconditionally after
    clustering would zero out Section 5's work."""
    report = _anchor_report()
    assert report.stats["clusters_built"] == len(report.clusters)
    assert report.stats["clusters_built"] == 1
    assert report.stats["diagnostics_deduplicated"] == 0

    duplicates = parse_log(
        Path("tests/fixtures/raw_logs/SYNTHETIC/duplicate-sections-sample/sample.log")
        .read_text(encoding="utf-8")
    )
    assert duplicates.stats["diagnostics_deduplicated"] == 1
    assert duplicates.stats["clusters_built"] == len(duplicates.clusters)


def test_bytes_and_peak_lines_are_deterministic_across_runs():
    raw = load_raw_log(ANCHOR)
    a = parse_log(raw.content, LogSource.from_raw_log(raw)).stats
    b = parse_log(raw.content, LogSource.from_raw_log(raw)).stats
    assert {k: v for k, v in a.items() if k != "runtime"} == {
        k: v for k, v in b.items() if k != "runtime"
    }


@pytest.mark.parametrize("key", sorted(EXPECTED_KEYS - {"runtime"}))
def test_every_documented_stats_key_is_json_serializable(key):
    json.dumps(_anchor_report().stats[key])
