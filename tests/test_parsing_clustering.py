"""Section 5 unit tests: message/path normalization, the dedup key, the two
ranking ladders, the G1 guard, and corpus-wide invariants.

Several tests here pin *deliberate non-rules* -- things the design decided NOT
to do, each with a stated reason. They are marked in the test docstring. A
future reader who "improves" one of them should have to delete an explicit
assertion saying why not.
"""

import json
from pathlib import Path

import pytest

from agentic_pr_analyzer.parsing import parse_log, to_json
from agentic_pr_analyzer.parsing.clustering import (
    build_clusters,
    dedup_key,
    norm_path,
    normalize_message,
)
from agentic_pr_analyzer.parsing.model import (
    Diagnostic,
    DiagnosticRole,
    DiagnosticType,
    Severity,
    SourceRange,
    StackFrame,
    StackTrace,
)

ANCHOR = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")
SYNTHETIC = Path("tests/fixtures/raw_logs/SYNTHETIC")

ALL_FIXTURE_LOGS = [ANCHOR] + sorted(SYNTHETIC.glob("*/sample.log"))


def diag(**overrides) -> Diagnostic:
    """Diagnostic factory -- every field defaulted, override only what a test
    is actually about."""
    base = dict(
        type=DiagnosticType.COMPILER_ERROR,
        severity=Severity.ERROR,
        tool="tsc",
        message="boom",
        file="src/a.ts",
        line=1,
        column=2,
        source_range=SourceRange(1, 1),
        stack_trace=None,
        test_id=None,
        exit_code=None,
        confidence=0.9,
        evidence=(1,),
        metadata={},
        parser="compiler",
    )
    base.update(overrides)
    return Diagnostic(**base)


def frame(path: str, lineno: int, in_project: bool = True) -> StackFrame:
    return StackFrame(
        file_path=path,
        line_number=lineno,
        column=None,
        function="f",
        raw_lineno=lineno,
        in_project=in_project,
        raw_text=path,
    )


def trace(*frames: StackFrame) -> StackTrace:
    return StackTrace(exception_type="E", message="m", frames=tuple(frames))


# ---------------------------------------------------------------- normalize_message


def test_normalize_message_none_is_empty_string():
    assert normalize_message(None) == ""


def test_normalize_message_collapses_windows_and_posix_separators():
    assert normalize_message(r"in tests\test_types.py") == normalize_message(
        "in tests/test_types.py"
    )


def test_normalize_message_masks_timestamp():
    a = normalize_message("failed at 2026-08-27T09:00:00.123Z during run")
    b = normalize_message("failed at 2026-01-02T11:22:33.999Z during run")
    assert a == b


def test_normalize_message_masks_duration():
    assert normalize_message("finished in 6.41s") == normalize_message("finished in 9.02s")


def test_normalize_message_masks_hex():
    a = normalize_message("object at 0xdeadbeef99 collided")
    b = normalize_message("object at 0xcafebabe11 collided")
    assert a == b


def test_normalize_message_masks_tmp_path():
    a = normalize_message("wrote /tmp/pytest-of-x/pytest-21/case0/out.txt")
    b = normalize_message("wrote /tmp/pytest-of-x/pytest-99/case7/out.txt")
    assert a == b


def test_normalize_message_masks_pid():
    assert normalize_message("worker pid=3332 died") == normalize_message("worker pid=9781 died")


def test_normalize_message_keeps_bare_integers():
    """Pins a DELIBERATE non-rule. Stripping bare integers would collide
    "expected 3 to be -3" with "expected 4 to be -4", and every numeric-bearing
    lint message. Rules 3-7 already remove all per-run variance in the corpus."""
    assert normalize_message("expected 3") != normalize_message("expected 4")


def test_normalize_message_never_raises_on_lone_surrogate():
    assert isinstance(normalize_message("bad \udcff message"), str)


def test_normalize_message_collapses_whitespace_and_casefolds():
    assert normalize_message("  Boom   Failed \t Here ") == normalize_message("boom failed here")


# ---------------------------------------------------------------- norm_path


def test_norm_path_is_separator_and_case_insensitive():
    assert norm_path(r"Tests\Test_Types.py") == norm_path("tests/test_types.py")


def test_norm_path_none_is_empty_string():
    assert norm_path(None) == ""


def test_norm_path_strips_leading_dot_slash_and_collapses_doubles():
    assert norm_path("./src//a.ts") == norm_path("src/a.ts")


# ---------------------------------------------------------------- dedup_key


def test_dedup_key_ignores_parser_field():
    """THE rule this section exists for. All four parsers call
    find_process_failure, so a log tripping two parsers emits the identical
    exit-code diagnostic twice, differing only in `parser`."""
    assert dedup_key(diag(parser="pytest")) == dedup_key(diag(parser="compiler"))


def test_dedup_key_ignores_evidence_and_source_range():
    a = diag(evidence=(1, 2, 3), source_range=SourceRange(1, 3))
    b = diag(evidence=(90,), source_range=SourceRange(90, 90))
    assert dedup_key(a) == dedup_key(b)


def test_dedup_key_ignores_confidence():
    assert dedup_key(diag(confidence=0.9)) == dedup_key(diag(confidence=0.4))


def test_dedup_key_separates_different_severity():
    assert dedup_key(diag(severity=Severity.ERROR)) != dedup_key(diag(severity=Severity.WARNING))


def test_dedup_key_separates_different_error_code():
    assert dedup_key(diag(metadata={"code": "TS2339"})) != dedup_key(
        diag(metadata={"code": "TS2345"})
    )


def test_dedup_key_separates_different_test_id():
    assert dedup_key(diag(test_id="a::x")) != dedup_key(diag(test_id="a::y"))


def test_dedup_key_excludes_stack_signature():
    """Pins a DELIBERATE exclusion. Adding a stack signature can only make the
    key MORE selective -- i.e. cause real duplicates (one occurrence truncated,
    one not) to fail to collapse. Frames are still used, as a frame set, by C3."""
    a = diag(stack_trace=trace(frame("src/a.ts", 10)))
    b = diag(stack_trace=trace(frame("src/zzz.ts", 999)))
    assert dedup_key(a) == dedup_key(b)


def test_dedup_key_collapses_windows_and_posix_file_spelling():
    assert dedup_key(diag(file=r"src\a.ts")) == dedup_key(diag(file="src/a.ts"))


# ---------------------------------------------------------------- ranking ladders


def _clusters_of(*diagnostics):
    clusters, primary = build_clusters(tuple(diagnostics), [])
    return clusters, primary


def test_cluster_rank_error_beats_warning():
    warn = diag(severity=Severity.WARNING, file="src/w.ts", evidence=(1,))
    err = diag(severity=Severity.ERROR, file="src/e.ts", evidence=(2,))
    clusters, primary = _clusters_of(warn, err)
    assert primary is clusters[0]
    assert primary.primary is err


def test_cluster_rank_test_failure_beats_compiler_error():
    comp = diag(type=DiagnosticType.COMPILER_ERROR, file="src/a.ts", evidence=(1,))
    test = diag(
        type=DiagnosticType.TEST_FAILURE, tool="pytest", test_id="t::x", file="t.py", evidence=(2,)
    )
    clusters, primary = _clusters_of(comp, test)
    assert primary.primary is test


def test_cluster_rank_located_beats_unlocated():
    unlocated = diag(file=None, line=None, column=None, evidence=(1,))
    located = diag(file="src/a.ts", evidence=(2,))
    clusters, primary = _clusters_of(unlocated, located)
    assert primary.primary is located


def test_cluster_rank_more_members_wins_on_tie():
    """Two indistinguishable single-member clusters plus one two-member cluster
    -- corroboration (discriminator 5) breaks the tie."""
    a1 = diag(file="src/a.ts", line=1, evidence=(1,))
    a2 = diag(file="src/a.ts", line=9, evidence=(2,))  # joins a1 via C2b
    b1 = diag(file="src/b.ts", line=1, evidence=(3,))
    clusters, primary = _clusters_of(a1, a2, b1)
    assert len(clusters) == 2
    assert primary.primary is a1
    assert len(primary.related) == 1


def test_cluster_rank_is_stable_for_indistinguishable_clusters():
    """Both ladders end in an index-based total order, so genuinely equal
    clusters keep emission order rather than dict/set iteration order."""
    a = diag(file="src/a.ts", evidence=(1,))
    b = diag(file="src/b.ts", evidence=(1,))
    clusters, primary = _clusters_of(a, b)
    assert [c.primary for c in clusters] == [a, b]
    assert primary.primary is a


def test_member_rank_error_beats_warning_in_same_cluster():
    warn = diag(severity=Severity.WARNING, file="src/a.ts", line=5, evidence=(1,))
    err = diag(severity=Severity.ERROR, file="src/a.ts", line=9, evidence=(2,))
    clusters, _ = _clusters_of(warn, err)
    assert len(clusters) == 1
    assert clusters[0].primary is err
    assert clusters[0].related == (warn,)
    assert clusters[0].related_roles == (DiagnosticRole.SECONDARY,)


def test_consequence_and_summary_and_duplicate_are_never_primary():
    """A PROCESS_FAILURE has the worst type rank and attaches as a consequence;
    an exact duplicate attaches as a duplicate. Neither may become a primary
    while a real `member` exists."""
    real = diag(type=DiagnosticType.COMPILER_ERROR, file="src/a.ts", evidence=(1,))
    pf = diag(
        type=DiagnosticType.PROCESS_FAILURE,
        severity=Severity.ERROR,
        tool=None,
        file=None,
        line=None,
        column=None,
        message="Process completed with exit code 1.",
        exit_code=1,
        evidence=(9,),
        parser="pytest",
    )
    pf_dupe = diag(
        type=DiagnosticType.PROCESS_FAILURE,
        severity=Severity.ERROR,
        tool=None,
        file=None,
        line=None,
        column=None,
        message="Process completed with exit code 1.",
        exit_code=1,
        evidence=(9,),
        parser="compiler",
    )
    clusters, primary = _clusters_of(real, pf, pf_dupe)

    assert len(clusters) == 1
    assert clusters[0].primary is real
    assert set(clusters[0].related_roles) == {
        DiagnosticRole.CONSEQUENCE,
        DiagnosticRole.DUPLICATE,
    }


def test_bare_process_failure_becomes_its_own_job_level_cluster():
    """When pass 1 produces no clusters, a PROCESS_FAILURE opens its own -- a
    red job whose tool output the engine could not parse. "Job-level" is a
    description of that condition, not a role or a type."""
    pf = diag(
        type=DiagnosticType.PROCESS_FAILURE,
        tool=None,
        file=None,
        line=None,
        column=None,
        message="Process completed with exit code 1.",
        exit_code=1,
        evidence=(9,),
    )
    clusters, primary = _clusters_of(pf)
    assert len(clusters) == 1
    assert clusters[0].primary is pf
    assert clusters[0].related == ()
    assert primary is clusters[0]


# ---------------------------------------------------------------- guards


def test_different_test_ids_never_merge_via_c2b():
    """G1, driven through C2b: same file, same tool, but two different tests."""
    a = diag(type=DiagnosticType.TEST_FAILURE, tool="pytest", file="t.py", line=1, test_id="t.py::x", evidence=(1,))
    b = diag(type=DiagnosticType.TEST_FAILURE, tool="pytest", file="t.py", line=2, test_id="t.py::y", evidence=(2,))
    clusters, _ = _clusters_of(a, b)
    assert len(clusters) == 2


def test_different_test_ids_never_merge_via_c3_stack_overlap():
    """G1, driven through C3: two tests sharing an in-project helper frame."""
    shared = frame("src/helper.py", 10)
    a = diag(type=DiagnosticType.TEST_FAILURE, tool="pytest", file="t.py", line=1, test_id="t.py::x", stack_trace=trace(shared), evidence=(1,))
    b = diag(type=DiagnosticType.TEST_FAILURE, tool="pytest", file="t.py", line=2, test_id="t.py::y", stack_trace=trace(shared), evidence=(2,))
    clusters, _ = _clusters_of(a, b)
    assert len(clusters) == 2


def test_c3_merges_on_shared_in_project_frame_when_test_ids_absent():
    shared = frame("src/helper.py", 10)
    a = diag(type=DiagnosticType.EXCEPTION, tool=None, file="x.py", line=1, stack_trace=trace(shared), evidence=(1,))
    b = diag(type=DiagnosticType.EXCEPTION, tool=None, file="y.py", line=2, stack_trace=trace(shared), evidence=(2,))
    clusters, _ = _clusters_of(a, b)
    assert len(clusters) == 1


def test_c3_ignores_frames_that_are_not_in_project():
    vendor = frame("node_modules/lib/index.js", 5, in_project=False)
    a = diag(type=DiagnosticType.EXCEPTION, tool=None, file="x.py", line=1, stack_trace=trace(vendor), evidence=(1,))
    b = diag(type=DiagnosticType.EXCEPTION, tool=None, file="y.py", line=2, stack_trace=trace(vendor), evidence=(2,))
    clusters, _ = _clusters_of(a, b)
    assert len(clusters) == 2


# ---------------------------------------------------------------- edge cases (step 9)


def test_build_clusters_on_empty_diagnostics():
    assert build_clusters((), []) == ((), None)


def test_build_clusters_tolerates_empty_evidence():
    """`_fatal_report` emits evidence=(). min(()) would raise ValueError."""
    clusters, primary = _clusters_of(diag(evidence=()), diag(evidence=(), file="src/b.ts"))
    assert len(clusters) == 2
    assert primary is not None


def test_build_clusters_tolerates_none_source_range():
    clusters, _ = _clusters_of(diag(source_range=None))
    assert len(clusters) == 1


def test_build_clusters_tolerates_all_files_none():
    clusters, primary = _clusters_of(
        diag(file=None, line=None, evidence=(1,)), diag(file=None, line=None, message="other", evidence=(2,))
    )
    assert len(clusters) == 2
    assert primary is not None


def test_all_warning_report_with_no_process_failure_still_ranks():
    a = diag(severity=Severity.WARNING, file="src/a.ts", evidence=(1,))
    b = diag(severity=Severity.WARNING, file="src/b.ts", evidence=(2,))
    clusters, primary = _clusters_of(a, b)
    assert len(clusters) == 2
    assert primary is clusters[0]


def test_two_process_failures_with_different_exit_codes_both_attach():
    real = diag(file="src/a.ts", evidence=(1,))
    pf1 = diag(type=DiagnosticType.PROCESS_FAILURE, tool=None, file=None, line=None, column=None, message="exit code 1", exit_code=1, evidence=(8,))
    pf2 = diag(type=DiagnosticType.PROCESS_FAILURE, tool=None, file=None, line=None, column=None, message="exit code 2", exit_code=2, evidence=(9,))
    clusters, _ = _clusters_of(real, pf1, pf2)
    assert len(clusters) == 1
    assert clusters[0].related_roles == (DiagnosticRole.CONSEQUENCE, DiagnosticRole.CONSEQUENCE)


def test_lone_surrogate_survives_normalize_dedup_and_json():
    report = parse_log("2026-01-01T00:00:00.0000000Z bad \udcff line\n")
    assert normalize_message("bad \udcff line")
    to_json(report)


def test_cluster_key_is_the_dedup_key_of_its_primary():
    d = diag(file="src/a.ts", evidence=(1,))
    clusters, _ = _clusters_of(d)
    assert clusters[0].key == dedup_key(d)


# ---------------------------------------------------------------- corpus invariants


@pytest.fixture(params=ALL_FIXTURE_LOGS, ids=lambda p: p.parent.name)
def fixture_report(request):
    return parse_log(request.param.read_bytes().decode("utf-8", errors="replace"))


def test_invariant_every_diagnostic_appears_exactly_once_across_clusters(fixture_report):
    seen = [
        id(d)
        for cluster in fixture_report.clusters
        for d in (cluster.primary,) + cluster.related
    ]
    assert sorted(seen) == sorted(id(d) for d in fixture_report.diagnostics)


def test_invariant_related_roles_length_matches_related_length(fixture_report):
    for cluster in fixture_report.clusters:
        assert len(cluster.related_roles) == len(cluster.related)


def test_invariant_primary_cluster_is_clusters_zero_when_clusters_nonempty(fixture_report):
    if fixture_report.clusters:
        assert fixture_report.primary_cluster is fixture_report.clusters[0]
    else:
        assert fixture_report.primary_cluster is None


def test_invariant_no_cluster_primary_has_a_non_primary_role(fixture_report):
    for cluster in fixture_report.clusters:
        assert DiagnosticRole.PRIMARY not in cluster.related_roles


def _without_runtime(report) -> str:
    """stats["runtime"] is the one bucket holding wall-clock readings, so it
    is excluded by name here exactly as the golden snapshot excludes it.
    Everything else must be byte-identical run to run."""
    payload = json.loads(to_json(report))
    payload["stats"].pop("runtime", None)
    return json.dumps(payload, sort_keys=True)


def test_invariant_clustering_is_deterministic():
    for path in ALL_FIXTURE_LOGS:
        content = path.read_bytes().decode("utf-8", errors="replace")
        assert _without_runtime(parse_log(content)) == _without_runtime(parse_log(content))


def test_invariant_no_fixture_produces_a_fatal_report(fixture_report):
    """The most dangerous failure mode in this section: a clustering exception
    swallowed by parse_log's total-function guard silently degrades the whole
    report to _fatal_report, and nothing else fails loudly."""
    assert not fixture_report.stats.get("fatal")
    assert fixture_report.diagnostics or fixture_report.clusters == ()
