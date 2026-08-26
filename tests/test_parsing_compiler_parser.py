from pathlib import Path

from agentic_pr_analyzer.parsing import confidence
from agentic_pr_analyzer.parsing.limits import ParseLimits
from agentic_pr_analyzer.parsing.model import DiagnosticType, Severity
from agentic_pr_analyzer.parsing.normalizer import normalize
from agentic_pr_analyzer.parsing.parsers.compiler_parser import CompilerParser
from agentic_pr_analyzer.parsing.providers.github_actions import GitHubActionsProvider
from agentic_pr_analyzer.parsing.segmentation import build_sections

TSC_FIXTURE = Path("tests/fixtures/raw_logs/SYNTHETIC/tsc-sample/sample.log")
ESLINT_FIXTURE = Path("tests/fixtures/raw_logs/SYNTHETIC/eslint-sample/sample.log")


def _load(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _diagnostics_for(content: str):
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    lines, sections = build_sections(lines)
    return CompilerParser().parse(lines, sections)


def _detect_for(content: str) -> bool:
    lines, _ = normalize(content, GitHubActionsProvider(), ParseLimits())
    lines, sections = build_sections(lines)
    return CompilerParser().detect(lines, sections)


def test_is_not_fallback():
    assert CompilerParser().is_fallback is False


def test_detect_true_for_tsc_fixture():
    assert _detect_for(_load(TSC_FIXTURE)) is True


def test_detect_true_for_eslint_fixture():
    assert _detect_for(_load(ESLINT_FIXTURE)) is True


def test_detect_false_on_unrelated_content():
    assert _detect_for("2026-01-01T00:00:00.0000000Z just a normal build log\n") is False


def test_tsc_fixture_extracts_two_compiler_errors():
    diags = _diagnostics_for(_load(TSC_FIXTURE))
    compiler_errors = [d for d in diags if d.type == DiagnosticType.COMPILER_ERROR]
    assert len(compiler_errors) == 2

    first, second = compiler_errors
    assert first.tool == "tsc"
    assert first.parser == "compiler"
    assert first.severity == Severity.ERROR
    assert first.file == "src/auth.ts"
    assert first.line == 42
    assert first.column == 7
    assert first.metadata["code"] == "TS2339"
    assert first.message == "Property 'x' does not exist on type 'User'."
    assert first.confidence == confidence.EXACT_TOOL_FORMAT

    assert second.file == "src/utils.ts"
    assert second.line == 10
    assert second.column == 3
    assert second.metadata["code"] == "TS2345"


def test_tsc_non_pretty_shape_is_also_parsed():
    content = (
        "2026-01-01T00:00:00.0000000Z src/auth.ts(42,7): error TS2339: "
        "Property 'x' does not exist.\n"
    )
    diags = _diagnostics_for(content)
    compiler_errors = [d for d in diags if d.type == DiagnosticType.COMPILER_ERROR]
    assert len(compiler_errors) == 1
    d = compiler_errors[0]
    assert d.file == "src/auth.ts"
    assert d.line == 42
    assert d.column == 7
    assert d.metadata["code"] == "TS2339"
    assert d.message == "Property 'x' does not exist."


def test_tsc_process_failure_co_emitted():
    diags = _diagnostics_for(_load(TSC_FIXTURE))
    process_failures = [d for d in diags if d.type == DiagnosticType.PROCESS_FAILURE]
    assert len(process_failures) == 1
    assert process_failures[0].exit_code == 2
    assert process_failures[0].parser == "compiler"


def test_eslint_fixture_extracts_lint_errors():
    diags = _diagnostics_for(_load(ESLINT_FIXTURE))
    lint_errors = [d for d in diags if d.type == DiagnosticType.LINT_ERROR]
    assert len(lint_errors) == 3

    first, second, third = lint_errors
    assert first.tool == "eslint"
    assert first.parser == "compiler"
    assert first.file == "src/index.js"
    assert first.line == 12
    assert first.column == 9
    assert first.severity == Severity.ERROR
    assert first.message == "'x' is assigned a value but never used"
    assert first.metadata["rule"] == "no-unused-vars"
    assert first.confidence == confidence.EXACT_TOOL_FORMAT

    assert second.file == "src/index.js"
    assert second.line == 18
    assert second.severity == Severity.WARNING
    assert second.metadata["rule"] == "consistent-return"

    assert third.file == "src/utils.js"
    assert third.line == 5
    assert third.column == 3
    assert third.metadata["rule"] == "no-console"


def test_eslint_process_failure_co_emitted():
    diags = _diagnostics_for(_load(ESLINT_FIXTURE))
    process_failures = [d for d in diags if d.type == DiagnosticType.PROCESS_FAILURE]
    assert len(process_failures) == 1
    assert process_failures[0].exit_code == 1
    assert process_failures[0].parser == "compiler"
