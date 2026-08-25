from pathlib import Path

from agentic_pr_analyzer.parsing import confidence
from agentic_pr_analyzer.parsing.limits import ParseLimits
from agentic_pr_analyzer.parsing.model import DiagnosticType
from agentic_pr_analyzer.parsing.normalizer import normalize
from agentic_pr_analyzer.parsing.parsers.js_test_parser import JsTestParser
from agentic_pr_analyzer.parsing.providers.github_actions import GitHubActionsProvider
from agentic_pr_analyzer.parsing.segmentation import build_sections

JEST_FIXTURE = Path("tests/fixtures/raw_logs/SYNTHETIC/jest-sample/sample.log")
VITEST_FIXTURE = Path("tests/fixtures/raw_logs/SYNTHETIC/vitest-sample/sample.log")


def _load(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _diagnostics_for(content: str):
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    lines, sections = build_sections(lines)
    return JsTestParser().parse(lines, sections)


def _detect_for(content: str) -> bool:
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    lines, sections = build_sections(lines)
    return JsTestParser().detect(lines, sections)


def test_is_not_fallback():
    assert JsTestParser().is_fallback is False


def test_detect_true_for_jest_fixture():
    assert _detect_for(_load(JEST_FIXTURE)) is True


def test_detect_true_for_vitest_fixture():
    assert _detect_for(_load(VITEST_FIXTURE)) is True


def test_detect_false_on_unrelated_content():
    assert _detect_for("2026-01-01T00:00:00.0000000Z just a normal build log\n") is False


def test_jest_failure_extracts_test_failure_diagnostic():
    diags = _diagnostics_for(_load(JEST_FIXTURE))
    test_failures = [d for d in diags if d.type == DiagnosticType.TEST_FAILURE]
    assert len(test_failures) == 1

    d = test_failures[0]
    assert d.tool == "jest"
    assert d.parser == "js_test"
    assert d.test_id == "src/sum.test.js::sum › adds negative numbers"
    assert d.file == "src/sum.test.js"
    assert d.line == 7
    assert d.column == 25
    assert d.confidence == confidence.EXACT_TOOL_FORMAT
    assert d.metadata["tool"] == "jest"
    assert d.metadata["expected"] == "-3"
    assert d.metadata["actual"] == "3"
    assert d.message == "expect(received).toBe(expected) // Object.is equality"
    assert d.stack_trace is not None
    assert d.stack_trace.frames[0].function == "Object.<anonymous>"
    assert d.stack_trace.frames[0].in_project is True


def test_vitest_failure_extracts_test_failure_diagnostic():
    diags = _diagnostics_for(_load(VITEST_FIXTURE))
    test_failures = [d for d in diags if d.type == DiagnosticType.TEST_FAILURE]
    assert len(test_failures) == 1

    d = test_failures[0]
    assert d.tool == "vitest"
    assert d.test_id == "src/sum.test.ts::sum › adds negative numbers"
    assert d.file == "src/sum.test.ts"
    assert d.line == 7
    assert d.column == 25
    assert d.metadata["tool"] == "vitest"
    assert d.metadata["expected"] == "-3"
    assert d.metadata["actual"] == "3"
    assert d.message == "AssertionError: expected 3 to be -3 // Object.is equality"


def test_process_failure_co_emitted_for_jest():
    diags = _diagnostics_for(_load(JEST_FIXTURE))
    process_failures = [d for d in diags if d.type == DiagnosticType.PROCESS_FAILURE]
    assert len(process_failures) == 1
    assert process_failures[0].exit_code == 1
    assert process_failures[0].parser == "js_test"


def test_process_failure_co_emitted_for_vitest():
    diags = _diagnostics_for(_load(VITEST_FIXTURE))
    process_failures = [d for d in diags if d.type == DiagnosticType.PROCESS_FAILURE]
    assert len(process_failures) == 1
    assert process_failures[0].exit_code == 1
