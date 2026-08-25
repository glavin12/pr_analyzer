"""The always-on fallback parser. Never guesses: it only emits a diagnostic

for two unambiguous patterns -- a `##[error]...exit code N` summary, or a
`path:line[:col]: error: ...` line -- and stays silent otherwise. It can
only emit PROCESS_FAILURE or UNKNOWN (never a tool-specific type; those are
Sections 2-4's job once a real parser+fixture exist for them).
"""

import re

from .. import confidence
from ..model import Diagnostic, DiagnosticType, LogLine, LogSection, Severity, SourceRange, WorkflowMarker

_EXIT_CODE_RE = re.compile(r"exit code (\d+)", re.IGNORECASE)
_FILE_LINE_ERROR_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?:error|Error|ERROR)\b[:\s]*(?P<msg>.*)$"
)


class GenericParser:
    name = "generic"
    tool = None
    is_fallback = True

    def detect(self, lines: list[LogLine], sections: tuple[LogSection, ...]) -> bool:
        return True

    def parse(self, lines: list[LogLine], sections: tuple[LogSection, ...]) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []

        error_lines = [line for line in lines if line.marker == WorkflowMarker.ERROR]
        exit_code_matches = []
        for line in error_lines:
            match = _EXIT_CODE_RE.search(line.marker_body or "")
            if match:
                exit_code_matches.append((line, int(match.group(1))))

        if exit_code_matches:
            line, code = exit_code_matches[-1]
            diagnostics.append(
                Diagnostic(
                    type=DiagnosticType.PROCESS_FAILURE,
                    severity=Severity.ERROR,
                    tool=None,
                    message=line.marker_body,
                    file=None,
                    line=None,
                    column=None,
                    source_range=SourceRange(line.raw_lineno, line.raw_lineno),
                    stack_trace=None,
                    test_id=None,
                    exit_code=code,
                    confidence=confidence.KNOWN_SUMMARY,
                    evidence=(line.raw_lineno,),
                    metadata={},
                    parser=self.name,
                )
            )

        for line in lines:
            match = _FILE_LINE_ERROR_RE.match(line.text)
            if not match:
                continue
            diagnostics.append(
                Diagnostic(
                    type=DiagnosticType.UNKNOWN,
                    severity=Severity.ERROR,
                    tool=None,
                    message=match.group("msg") or None,
                    file=match.group("file"),
                    line=int(match.group("line")),
                    column=int(match.group("col")) if match.group("col") else None,
                    source_range=SourceRange(line.raw_lineno, line.raw_lineno),
                    stack_trace=None,
                    test_id=None,
                    exit_code=None,
                    confidence=confidence.GENERIC_FILE_LINE_ERROR,
                    evidence=(line.raw_lineno,),
                    metadata={},
                    parser=self.name,
                )
            )

        return diagnostics
