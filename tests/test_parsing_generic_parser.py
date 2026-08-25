from agentic_pr_analyzer.parsing import confidence
from agentic_pr_analyzer.parsing.limits import ParseLimits
from agentic_pr_analyzer.parsing.model import DiagnosticType
from agentic_pr_analyzer.parsing.normalizer import normalize
from agentic_pr_analyzer.parsing.parsers.generic_parser import GenericParser
from agentic_pr_analyzer.parsing.providers.generic import GenericProvider
from agentic_pr_analyzer.parsing.providers.github_actions import GitHubActionsProvider
from agentic_pr_analyzer.parsing.segmentation import build_sections


def _diagnostics_for(content: str, provider=None):
    lines, _ = normalize(content, provider or GenericProvider(), ParseLimits())
    lines, sections = build_sections(lines)
    return GenericParser().parse(lines, sections)


def test_generic_parser_detect_is_always_true_and_is_fallback():
    assert GenericParser().detect([], ()) is True
    assert GenericParser().is_fallback is True


def test_generic_parser_extracts_file_line_error():
    diags = _diagnostics_for("src/main.py:42:7: error: unexpected token\n")
    assert len(diags) == 1
    d = diags[0]
    assert d.file == "src/main.py"
    assert d.line == 42
    assert d.column == 7
    assert d.type == DiagnosticType.UNKNOWN
    assert d.confidence == confidence.GENERIC_FILE_LINE_ERROR
    assert d.parser == "generic"


def test_generic_parser_ignores_plain_text():
    diags = _diagnostics_for("just a normal log line\nanother one\n")
    assert diags == []


def test_generic_parser_extracts_process_failure_from_last_error_exit_code():
    content = (
        "2026-01-01T00:00:00.0000000Z ##[error]Some earlier failure\n"
        "2026-01-01T00:00:01.0000000Z ##[error]Process completed with exit code 1.\n"
    )
    diags = _diagnostics_for(content, provider=GitHubActionsProvider())
    assert len(diags) == 1
    assert diags[0].type == DiagnosticType.PROCESS_FAILURE
    assert diags[0].exit_code == 1
    assert diags[0].confidence == confidence.KNOWN_SUMMARY
    assert diags[0].evidence == (2,)


def test_generic_parser_never_emits_tool_specific_types():
    content = (
        "src/main.py:1:1: error: bad\n"
        "2026-01-01T00:00:00.0000000Z ##[error]Process completed with exit code 1.\n"
    )
    diags = _diagnostics_for(content, provider=GitHubActionsProvider())
    allowed = {DiagnosticType.PROCESS_FAILURE, DiagnosticType.UNKNOWN}
    assert all(d.type in allowed for d in diags)
