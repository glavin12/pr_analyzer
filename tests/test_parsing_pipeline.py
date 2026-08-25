from agentic_pr_analyzer.parsing.pipeline import parse_log


def test_parse_log_builds_trivial_one_cluster_per_diagnostic():
    content = (
        "2026-01-01T00:00:00.0000000Z src/main.py:42:7: error: unexpected token\n"
        "2026-01-01T00:00:01.0000000Z ##[error]Process completed with exit code 1.\n"
    )
    report = parse_log(content)
    assert len(report.clusters) == len(report.diagnostics)
    assert len(report.diagnostics) == 2


def test_parse_log_primary_cluster_prefers_located_diagnostic_over_bare_process_exit():
    content = (
        "2026-01-01T00:00:00.0000000Z src/main.py:42:7: error: unexpected token\n"
        "2026-01-01T00:00:01.0000000Z ##[error]Process completed with exit code 1.\n"
    )
    report = parse_log(content)
    assert report.primary_cluster is not None
    assert report.primary_cluster.primary.file == "src/main.py"


def test_parse_log_stats_and_metadata_populated():
    report = parse_log("2026-01-01T00:00:00.0000000Z hello world\n")
    assert report.stats["lines_processed"] == 1
    assert report.stats["parser_selected"] == "generic"
    assert report.provider == "github_actions"
    assert report.schema_version == "1.0"


def test_parse_log_with_no_diagnostics_has_no_primary_cluster():
    report = parse_log("2026-01-01T00:00:00.0000000Z all good here\n")
    assert report.diagnostics == ()
    assert report.primary_cluster is None


def test_parse_log_exit_code_bundled_from_process_failure_diagnostic():
    content = "2026-01-01T00:00:00.0000000Z ##[error]Process completed with exit code 1.\n"
    report = parse_log(content)
    assert report.exit_code == 1
