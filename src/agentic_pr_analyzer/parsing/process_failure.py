"""Shared `##[error]...exit code N` -> PROCESS_FAILURE extraction.

Extracted from GenericParser (Section 1) so a specialized parser (pytest,
jest/vitest) can co-emit the same diagnostic itself. Without this, the
registry's fallback-suppression contract (GenericParser only contributes
when no specialized parser produced a diagnostic) would silently drop the
process-exit-code evidence the moment a specialized parser fires.
"""

import re

from . import confidence
from .model import Diagnostic, DiagnosticType, LogLine, Severity, SourceRange, WorkflowMarker

_EXIT_CODE_RE = re.compile(r"exit code (\d+)", re.IGNORECASE)


def find_process_failure(lines: list[LogLine], parser_name: str) -> Diagnostic | None:
    matches = []
    for line in lines:
        if line.marker != WorkflowMarker.ERROR:
            continue
        match = _EXIT_CODE_RE.search(line.marker_body or "")
        if match:
            matches.append((line, int(match.group(1))))

    if not matches:
        return None

    line, code = matches[-1]
    return Diagnostic(
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
        parser=parser_name,
    )
