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

BARE_ASSERT_LOG = (
    "============================= test session starts =============================\n"
    "collected 12 items\n"
    "\n"
    "tests/test_parser.py ....F....\n"
    "\n"
    "================================== FAILURES ===================================\n"
    "________________________ test_parse_invalid_timestamp ___________________________\n"
    "\n"
    "    def test_parse_invalid_timestamp():\n"
    "        result = parse_log(\"2025-13-45 ERROR Something went wrong\")\n"
    ">       assert result.timestamp is not None\n"
    "E       AssertionError: assert None is not None\n"
    "\n"
    "tests/test_parser.py:42: AssertionError\n"
    "=========================== short test summary info ===========================\n"
    "FAILED tests/test_parser.py::test_parse_invalid_timestamp - AssertionError\n"
    "========================= 1 failed, 11 passed ================================\n"
)

# Real pytest output (v9.1.1), captured by actually running `pytest` against
# a deliberately-broken test file locally (see the PR discussion / session
# notes for the two source files: a bad top-level import, and a fixture that
# raises on setup). Absolute machine-specific paths sanitized to generic
# placeholders; everything else -- banner shape, "ERROR collecting <file>"
# separator, the stdlib frame, the short-summary line with no " - <msg>"
# suffix -- is exactly what pytest printed, not guessed.
COLLECTION_ERROR_LOG = (
    "============================= test session starts =============================\n"
    "platform win32 -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0 -- /repo/.venv/Scripts/python.exe\n"
    "cachedir: .pytest_cache\n"
    "rootdir: /repo\n"
    "configfile: pyproject.toml\n"
    "collecting ... collected 0 items / 1 error\n"
    "\n"
    "=================================== ERRORS ====================================\n"
    "_____________ ERROR collecting tests/probe/test_broken_import.py ______________\n"
    "ImportError while importing test module '/repo/tests/probe/test_broken_import.py'.\n"
    "Hint: make sure your test modules/packages have valid Python names.\n"
    "Traceback:\n"
    "C:\\Python311\\Lib\\importlib\\__init__.py:126: in import_module\n"
    "    return _bootstrap._gcd_import(name[level:], package, level)\n"
    "           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
    "tests\\probe\\test_broken_import.py:1: in <module>\n"
    "    import nonexistent_module_xyz\n"
    "E   ModuleNotFoundError: No module named 'nonexistent_module_xyz'\n"
    "=========================== short test summary info ===========================\n"
    "ERROR tests/probe/test_broken_import.py\n"
    "!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
    "============================== 1 error in 0.20s ===============================\n"
)

# Real pytest output for a fixture that raises during setup -- note the
# short-summary line here *does* have a " - <msg>" suffix (truncated), unlike
# the collection-error capture above, confirming that suffix's presence is
# not reliable and must stay optional in the regex.
SETUP_ERROR_LOG = (
    "============================= test session starts =============================\n"
    "platform win32 -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0 -- /repo/.venv/Scripts/python.exe\n"
    "cachedir: .pytest_cache\n"
    "rootdir: /repo\n"
    "configfile: pyproject.toml\n"
    "collecting ... collected 1 item\n"
    "\n"
    "tests/probe/test_broken_fixture.py::test_uses_broken_fixture ERROR       [100%]\n"
    "\n"
    "=================================== ERRORS ====================================\n"
    "_________________ ERROR at setup of test_uses_broken_fixture __________________\n"
    "tests\\probe\\test_broken_fixture.py:5: in broken_fixture\n"
    "    raise RuntimeError(\"fixture setup failed: could not connect to test database\")\n"
    "E   RuntimeError: fixture setup failed: could not connect to test database\n"
    "=========================== short test summary info ===========================\n"
    "ERROR tests/probe/test_broken_fixture.py::test_uses_broken_fixture - RuntimeE...\n"
    "============================== 1 error in 0.13s ===============================\n"
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


def test_bare_assert_with_no_call_frame_still_locates_file_and_line():
    # Regression: a plain `assert` directly in the test body prints no
    # "in <func>" frame at all, just the trailing crash-location line and
    # an "E"-line indented further than 3 spaces -- both previously fell
    # through stacktrace.py's frame/exception detection.
    diags = _diagnostics_for(BARE_ASSERT_LOG)
    d = next(d for d in diags if d.type == DiagnosticType.TEST_FAILURE)
    assert d.file == "tests/test_parser.py"
    assert d.line == 42
    assert d.stack_trace is not None
    assert d.stack_trace.message == "assert None is not None"


def test_collection_error_is_captured_from_errors_block():
    diags = _diagnostics_for(COLLECTION_ERROR_LOG)
    test_failures = [d for d in diags if d.type == DiagnosticType.TEST_FAILURE]
    assert len(test_failures) == 1

    d = test_failures[0]
    assert d.test_id == "tests/probe/test_broken_import.py"
    assert d.file == "tests\\probe\\test_broken_import.py"
    assert d.line == 1
    assert d.message == "No module named 'nonexistent_module_xyz'"
    assert d.metadata["outcome"] == "error"
    assert d.stack_trace.exception_type == "ModuleNotFoundError"


def test_setup_error_is_captured_from_errors_block():
    diags = _diagnostics_for(SETUP_ERROR_LOG)
    test_failures = [d for d in diags if d.type == DiagnosticType.TEST_FAILURE]
    assert len(test_failures) == 1

    d = test_failures[0]
    assert d.test_id == "tests/probe/test_broken_fixture.py::test_uses_broken_fixture"
    assert d.file == "tests\\probe\\test_broken_fixture.py"
    assert d.line == 5
    assert d.metadata["outcome"] == "error"
    assert d.stack_trace.exception_type == "RuntimeError"


def test_malformed_failures_block_degrades_to_partial_diagnostic_without_raising():
    diags = _diagnostics_for(MALFORMED_LOG)
    test_failures = [d for d in diags if d.type == DiagnosticType.TEST_FAILURE]
    assert len(test_failures) == 1
    d = test_failures[0]
    assert d.test_id == "test_mystery"
    assert d.file is None
    assert d.stack_trace is None
