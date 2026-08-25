"""The always-on fallback parser. Never guesses: it only emits a diagnostic

for two unambiguous patterns -- a `##[error]...exit code N` summary, or a
`path:line[:col]: error: ...` line -- and stays silent otherwise. It can
only emit PROCESS_FAILURE or UNKNOWN (never a tool-specific type; those are
Sections 2-4's job once a real parser+fixture exist for them).
"""

import re

from .. import confidence
from ..model import Diagnostic, DiagnosticType, LogLine, LogSection, Severity, SourceRange
from ..process_failure import find_process_failure

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

        process_failure = find_process_failure(lines, self.name)
        if process_failure is not None:
            diagnostics.append(process_failure)

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
