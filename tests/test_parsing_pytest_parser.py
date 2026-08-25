from agentic_pr_analyzer.parsing import confidence
from agentic_pr_analyzer.parsing.limits import ParseLimits
from agentic_pr_analyzer.parsing.model import DiagnosticType
from agentic_pr_analyzer.parsing.normalizer import normalize
from agentic_pr_analyzer.parsing.parsers.pytest_parser import PytestParser
from agentic_pr_analyzer.parsing.providers.github_actions import GitHubActionsProvider
from agentic_pr_analyzer.parsing.segmentation import build_sections

_TS = "2026-01-01T00:00:00.0000000Z "

SINGLE_FAILURE_LOG = (
    _TS + "============================= test session starts ==============================\n"
    + _TS + "tests/test_x.py::test_ok PASSED                                          [ 33%]\n"
    + _TS + "tests/test_x.py::test_skip SKIPPED (reason)                              [ 66%]\n"
    + _TS + "tests/test_x.py::test_foo FAILED                                         [100%]\n"
    + _TS + "================================== FAILURES ===================================\n"
    + _TS + "_________________________________ test_foo _________________________________\n"
    + _TS + "tests/test_x.py:10: in test_foo\n"
    + _TS + "    assert expected == actual\n"
    + _TS + "E   AssertionError: assert 1 == 2\n"
    + _TS + "E     Expected: 1\n"
    + _TS + "E     Actual: 2\n"
    + _TS + "=========================== short test summary info ===========================\n"
    + _TS + "FAILED tests/test_x.py::test_foo - AssertionError: assert 1 == 2\n"
    + _TS + "== 1 failed, 1 passed, 1 skipped in 0.12s ===\n"
    + _TS + "##[error]Process completed with exit code 1.\n"
)

MALFORMED_LOG = (
    _TS + "============================= test session starts ==============================\n"
    + _TS + "================================== FAILURES ===================================\n"
    + _TS + "_________________________________ test_mystery _________________________________\n"
    + _TS + "   <garbled output, no recognizable frame or exception line>\n"
    + _TS + "=========================== short test summary info ===========================\n"
    + _TS + "== 1 failed in 0.01s ===\n"
)


def _diagnostics_for(content: str):
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    lines, sections = build_sections(lines)
    return PytestParser().parse(lines, sections)


def _detect_for(content: str) -> bool:
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    lines, sections = build_sections(lines)
    return PytestParser().detect(lines, sections)


def test_detect_true_on_session_starts_banner():
    assert _detect_for(SINGLE_FAILURE_LOG) is True


def test_detect_false_on_unrelated_content():
    assert _detect_for(_TS + "just a normal build log\n") is False


def test_is_not_fallback_and_tool_is_pytest():
    assert PytestParser().is_fallback is False
    assert PytestParser().tool == "pytest"


def test_single_failure_extracts_test_failure_diagnostic():
    diags = _diagnostics_for(SINGLE_FAILURE_LOG)
    test_failures = [d for d in diags if d.type == DiagnosticType.TEST_FAILURE]
    assert len(test_failures) == 1

    d = test_failures[0]
    assert d.test_id == "tests/test_x.py::test_foo"
    assert d.file == "tests/test_x.py"
    assert d.line == 10
    assert d.tool == "pytest"
    assert d.parser == "pytest"
    assert d.confidence == confidence.EXACT_TOOL_FORMAT
    assert d.metadata["outcome"] == "failed"
    assert d.metadata["expected"] == "1"
    assert d.metadata["actual"] == "2"
    assert d.stack_trace is not None
    assert d.stack_trace.exception_type == "AssertionError"
    assert d.stack_trace.frames[0].function == "test_foo"


def test_skipped_and_passed_tests_are_not_emitted_as_diagnostics():
    diags = _diagnostics_for(SINGLE_FAILURE_LOG)
    test_ids = {d.test_id for d in diags if d.type == DiagnosticType.TEST_FAILURE}
    assert test_ids == {"tests/test_x.py::test_foo"}


def test_process_failure_is_co_emitted_alongside_test_failure():
    diags = _diagnostics_for(SINGLE_FAILURE_LOG)
    process_failures = [d for d in diags if d.type == DiagnosticType.PROCESS_FAILURE]
    assert len(process_failures) == 1
    assert process_failures[0].exit_code == 1
    assert process_failures[0].parser == "pytest"


def test_run_summary_metadata_is_parsed():
    diags = _diagnostics_for(SINGLE_FAILURE_LOG)
    d = next(d for d in diags if d.type == DiagnosticType.TEST_FAILURE)
    assert d.metadata["run_summary"]["failed"] == 1
    assert d.metadata["run_summary"]["passed"] == 1
    assert d.metadata["run_summary"]["skipped"] == 1
    assert d.metadata["run_summary"]["duration_seconds"] == 0.12


def test_malformed_failures_block_degrades_to_partial_diagnostic_without_raising():
    diags = _diagnostics_for(MALFORMED_LOG)
    test_failures = [d for d in diags if d.type == DiagnosticType.TEST_FAILURE]
    assert len(test_failures) == 1
    d = test_failures[0]
    assert d.test_id == "test_mystery"
    assert d.file is None
    assert d.stack_trace is None
