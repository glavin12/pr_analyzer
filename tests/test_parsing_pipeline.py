from agentic_pr_analyzer.parsing.model import DiagnosticRole, DiagnosticType
from agentic_pr_analyzer.parsing.pipeline import parse_log

# Section 5 deleted `test_parse_log_builds_trivial_one_cluster_per_diagnostic`
# by name. That test asserted `len(clusters) == len(diagnostics)` -- the
# placeholder contract this section is chartered to replace. Its own name said
# "trivial". It was removed, not weakened for convenience.

_TWO_DIAGNOSTIC_LOG = (
    "2026-01-01T00:00:00.0000000Z src/main.py:42:7: error: unexpected token\n"
    "2026-01-01T00:00:01.0000000Z ##[error]Process completed with exit code 1.\n"
)


def test_parse_log_primary_cluster_prefers_located_diagnostic_over_bare_process_exit():
    report = parse_log(_TWO_DIAGNOSTIC_LOG)
    assert report.primary_cluster is not None
    assert report.primary_cluster.primary.file == "src/main.py"


def test_parse_log_stats_and_metadata_populated():
    report = parse_log("2026-01-01T00:00:00.0000000Z hello world\n")
    assert report.stats["lines_processed"] == 1
    assert report.stats["parser_selected"] == "generic"
    assert report.provider == "github_actions"
    assert report.schema_version == "1.3"


def test_parse_log_with_no_diagnostics_has_no_primary_cluster():
    report = parse_log("2026-01-01T00:00:00.0000000Z all good here\n")
    assert report.diagnostics == ()
    assert report.primary_cluster is None


def test_parse_log_exit_code_bundled_from_process_failure_diagnostic():
    content = "2026-01-01T00:00:00.0000000Z ##[error]Process completed with exit code 1.\n"
    report = parse_log(content)
    assert report.exit_code == 1


# ---------------------------------------------------------------- Section 5


def test_parse_log_collapses_process_failure_into_the_primary_cluster():
    report = parse_log(_TWO_DIAGNOSTIC_LOG)
    assert len(report.diagnostics) == 2
    assert len(report.clusters) == 1
    assert report.primary_cluster.primary.file == "src/main.py"
    assert report.primary_cluster.related_roles == (DiagnosticRole.CONSEQUENCE,)


def test_parse_log_bare_process_failure_is_its_own_job_level_cluster():
    """No parseable tool output -- the red job itself is the only evidence.
    "Job-level" describes that condition; it is not a role or a type."""
    report = parse_log(
        "2026-01-01T00:00:00.0000000Z ##[error]Process completed with exit code 1.\n"
    )
    assert len(report.clusters) == 1
    assert report.primary_cluster.primary.type is DiagnosticType.PROCESS_FAILURE
    assert report.primary_cluster.related == ()


def test_parse_log_every_diagnostic_belongs_to_exactly_one_cluster():
    content = (
        "2026-01-01T00:00:00.0000000Z src/main.py:42:7: error: unexpected token\n"
        "2026-01-01T00:00:01.0000000Z src/other.py:9:1: error: bad thing\n"
        "2026-01-01T00:00:02.0000000Z ##[error]Process completed with exit code 1.\n"
    )
    report = parse_log(content)
    members = [d for c in report.clusters for d in (c.primary,) + c.related]
    assert sorted(map(id, members)) == sorted(map(id, report.diagnostics))
