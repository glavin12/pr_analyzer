"""Section 5 end-to-end: what clustering does to each committed fixture.

These are the acceptance criteria in executable form. The click anchor is the
only REAL capture; the eight SYNTHETIC ones are hand-written (see
tests/fixtures/raw_logs/SYNTHETIC/README.md for why, and which to replace
first).
"""

from pathlib import Path

from agentic_pr_analyzer.parsing import parse_log
from agentic_pr_analyzer.parsing.model import DiagnosticRole, DiagnosticType

ANCHOR = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")
SYNTHETIC = Path("tests/fixtures/raw_logs/SYNTHETIC")


def _report(path: Path):
    return parse_log(path.read_bytes().decode("utf-8", errors="replace"))


def _synthetic(name: str):
    return _report(SYNTHETIC / name / "sample.log")


def _role_of(cluster, diagnostic):
    """Role lookup by identity, not by dict key.

    `Diagnostic` is `@dataclass(frozen=True)` with a plain `metadata: dict`
    field, so its auto-generated `__hash__` raises TypeError for every
    instance -- `dict(zip(cluster.related, cluster.related_roles))` cannot
    work. `related_roles` is index-aligned with `related` by contract, which
    is what makes this lookup exact.
    """
    index = next(i for i, d in enumerate(cluster.related) if d is diagnostic)
    return cluster.related_roles[index]


# ---------------------------------------------------------------- click (REAL)


def test_click_anchor_collapses_to_one_cluster_with_a_consequence():
    report = _report(ANCHOR)
    assert len(report.diagnostics) == 2
    assert len(report.clusters) == 1
    assert report.primary_cluster.related_roles == (DiagnosticRole.CONSEQUENCE,)
    assert report.primary_cluster.related[0].exit_code == 1
    assert report.primary_cluster.primary.type is DiagnosticType.TEST_FAILURE
    assert report.exit_code == 1


# ---------------------------------------------------------------- eslint


def test_eslint_fixture_groups_by_file_and_tool():
    report = _synthetic("eslint-sample")
    assert len(report.diagnostics) == 4
    assert len(report.clusters) == 2


def test_eslint_fixture_warning_is_secondary_not_primary():
    report = _synthetic("eslint-sample")
    index_cluster = next(c for c in report.clusters if c.primary.file == "src/index.js")
    assert index_cluster.primary.severity.value == "error"
    warning = next(d for d in index_cluster.related if d.severity.value == "warning")
    assert _role_of(index_cluster, warning) is DiagnosticRole.SECONDARY


# ---------------------------------------------------------------- tsc


def test_tsc_fixture_two_files_stay_two_clusters():
    """Anti-over-merge: C2b requires the same file, so two errors in two files
    must not collapse into one."""
    report = _synthetic("tsc-sample")
    assert len(report.diagnostics) == 3
    assert len(report.clusters) == 2
    assert {c.primary.file for c in report.clusters} == {"src/auth.ts", "src/utils.ts"}


# ---------------------------------------------------------------- jest / vitest


def test_jest_fixture_process_failure_is_a_consequence():
    report = _synthetic("jest-sample")
    assert len(report.clusters) == 1
    assert report.primary_cluster.related_roles == (DiagnosticRole.CONSEQUENCE,)


def test_vitest_fixture_process_failure_is_a_consequence():
    report = _synthetic("vitest-sample")
    assert len(report.clusters) == 1
    assert report.primary_cluster.related_roles == (DiagnosticRole.CONSEQUENCE,)


# ---------------------------------------------------------------- multi-test


def test_multi_test_fixture_one_cluster_per_failing_test():
    report = _synthetic("multi-test-sample")
    assert len(report.diagnostics) == 4
    assert len(report.clusters) == 3


def test_multi_test_fixture_same_file_different_tests_do_not_merge():
    """G1 end-to-end: test_subtract and test_divide are both in
    tests/test_math.py, and must still land in separate clusters."""
    report = _synthetic("multi-test-sample")
    for cluster in report.clusters:
        ids = {
            d.test_id
            for d in (cluster.primary,) + cluster.related
            if d.test_id is not None
        }
        assert len(ids) <= 1, f"cluster mixes test ids: {ids}"


# ---------------------------------------------------------------- duplicate sections


def test_duplicate_sections_fixture_collapses_cross_parser_process_failure():
    report = _synthetic("duplicate-sections-sample")
    assert report.stats["diagnostics_deduplicated"] == 1

    roles = [role for c in report.clusters for role in c.related_roles]
    assert roles.count(DiagnosticRole.DUPLICATE) == 1
    assert roles.count(DiagnosticRole.CONSEQUENCE) == 1


def test_duplicate_sections_fixture_keeps_both_diagnostics_in_report():
    """Clustering is non-lossy: dedup is expressed as cluster membership plus a
    role, never by deleting evidence from report.diagnostics."""
    report = _synthetic("duplicate-sections-sample")
    process_failures = [
        d for d in report.diagnostics if d.type is DiagnosticType.PROCESS_FAILURE
    ]
    assert len(process_failures) == 2
    assert {d.parser for d in process_failures} == {"pytest", "compiler"}


def test_duplicate_sections_fixture_exit_code_still_one():
    assert _synthetic("duplicate-sections-sample").exit_code == 1


# ---------------------------------------------------------------- cascade


def test_cascade_fixture_produces_one_cluster_per_failing_unit():
    report = _synthetic("cascade-sample")
    assert len(report.diagnostics) == 5
    assert len(report.clusters) == 3


def test_cascade_fixture_process_failure_attaches_only_to_the_primary_cluster():
    report = _synthetic("cascade-sample")
    carrying = [
        c
        for c in report.clusters
        if any(d.type is DiagnosticType.PROCESS_FAILURE for d in c.related)
    ]
    assert len(carrying) == 1
    assert carrying[0] is report.primary_cluster
    assert report.primary_cluster.primary.type is DiagnosticType.TEST_FAILURE


# ---------------------------------------------------------------- summary echo


def test_summary_echo_fixture_marks_marker_sourced_diagnostic_as_summary():
    report = _synthetic("summary-echo-sample")
    assert len(report.clusters) == 1
    assert DiagnosticRole.SUMMARY in report.primary_cluster.related_roles


def test_summary_echo_fixture_primary_is_the_unpolluted_tool_diagnostic():
    """Rule S1 matches on normalized message + line + column, never on `file` --
    which is what keeps it robust to the known _TSC_NONPRETTY_RE bug that
    swallows the "##[error]" prefix into Diagnostic.file (filed separately)."""
    report = _synthetic("summary-echo-sample")
    assert report.primary_cluster.primary.file == "src/auth.ts"

    echo = next(
        d for d in report.primary_cluster.related if d.file == "##[error]src/auth.ts"
    )
    assert _role_of(report.primary_cluster, echo) is DiagnosticRole.SUMMARY
