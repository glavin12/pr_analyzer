"""Section 4: one parser for two JS/TS-ecosystem tools -- tsc (a compiler,
COMPILER_ERROR) and eslint (a linter, LINT_ERROR). Same shape as
`JsTestParser`: one class runs two independent extractors and concatenates,
tool recorded per-diagnostic. gcc/clang/rustc/javac are additive parsers
later, not a refactor of this one.
"""

import re

from .. import confidence
from ..model import (
    Diagnostic,
    DiagnosticType,
    LogLine,
    LogSection,
    Severity,
    SourceRange,
    WorkflowMarker,
)
from ..process_failure import find_process_failure

# tsc has two output shapes depending on --pretty:
#   src/auth.ts:42:7 - error TS2339: Property 'x' does not exist.   (pretty)
#   src/auth.ts(42,7): error TS2339: Property 'x' does not exist.   (non-pretty)
_TSC_PRETTY_RE = re.compile(
    r"^(?P<file>\S+):(?P<line>\d+):(?P<col>\d+) - (?P<severity>error|warning) "
    r"(?P<code>TS\d+): (?P<msg>.*)$"
)
_TSC_NONPRETTY_RE = re.compile(
    r"^(?P<file>\S+)\((?P<line>\d+),(?P<col>\d+)\): (?P<severity>error|warning) "
    r"(?P<code>TS\d+): (?P<msg>.*)$"
)
_TSC_DETECT_RE = re.compile(r"error TS\d+")

# eslint "stylish" reporter: a file-path header line, then indented rows,
# terminated by a blank line or the "✖ N problems" summary.
_ESLINT_ROW_RE = re.compile(
    r"^\s+(?P<line>\d+):(?P<col>\d+)\s+(?P<severity>error|warning)\s+"
    r"(?P<msg>.+?)\s{2,}(?P<rule>\S+)$"
)
_ESLINT_SUMMARY_RE = re.compile(r"^✖\s+\d+\s+problems?\b")


class CompilerParser:
    name = "compiler"
    tool = None  # set per-diagnostic ("tsc" or "eslint"), not fixed for the class
    is_fallback = False

    def detect(self, lines: list[LogLine], sections: tuple[LogSection, ...]) -> bool:
        for line in lines:
            text = line.text
            if _TSC_DETECT_RE.search(text) or _ESLINT_SUMMARY_RE.match(text):
                return True
            if line.marker == WorkflowMarker.COMMAND and line.marker_body:
                body = line.marker_body.lower()
                if "tsc" in body or "eslint" in body:
                    return True
        return False

    def parse(self, lines: list[LogLine], sections: tuple[LogSection, ...]) -> list[Diagnostic]:
        diagnostics = self._extract_tsc(lines) + self._extract_eslint(lines)

        process_failure = find_process_failure(lines, self.name)
        if process_failure is not None:
            diagnostics.append(process_failure)

        return diagnostics

    def _extract_tsc(self, lines: list[LogLine]) -> list[Diagnostic]:
        diagnostics = []
        for line in lines:
            match = _TSC_PRETTY_RE.match(line.text) or _TSC_NONPRETTY_RE.match(line.text)
            if not match:
                continue
            diagnostics.append(
                Diagnostic(
                    type=DiagnosticType.COMPILER_ERROR,
                    severity=Severity(match.group("severity")),
                    tool="tsc",
                    message=match.group("msg"),
                    file=match.group("file"),
                    line=int(match.group("line")),
                    column=int(match.group("col")),
                    source_range=SourceRange(line.raw_lineno, line.raw_lineno),
                    stack_trace=None,
                    test_id=None,
                    exit_code=None,
                    confidence=confidence.EXACT_TOOL_FORMAT,
                    evidence=(line.raw_lineno,),
                    metadata={"code": match.group("code")},
                    parser=self.name,
                )
            )
        return diagnostics

    def _extract_eslint(self, lines: list[LogLine]) -> list[Diagnostic]:
        diagnostics = []
        current_file: str | None = None
        for line in lines:
            text = line.text
            if _ESLINT_SUMMARY_RE.match(text):
                continue
            row_match = _ESLINT_ROW_RE.match(text)
            if row_match:
                if current_file is None:
                    continue
                diagnostics.append(
                    Diagnostic(
                        type=DiagnosticType.LINT_ERROR,
                        severity=Severity(row_match.group("severity")),
                        tool="eslint",
                        message=row_match.group("msg").strip(),
                        file=current_file,
                        line=int(row_match.group("line")),
                        column=int(row_match.group("col")),
                        source_range=SourceRange(line.raw_lineno, line.raw_lineno),
                        stack_trace=None,
                        test_id=None,
                        exit_code=None,
                        confidence=confidence.EXACT_TOOL_FORMAT,
                        evidence=(line.raw_lineno,),
                        metadata={"rule": row_match.group("rule")},
                        parser=self.name,
                    )
                )
                continue
            if not text.strip():
                current_file = None
                continue
            if not text[:1].isspace():
                current_file = text.strip()
        return diagnostics
