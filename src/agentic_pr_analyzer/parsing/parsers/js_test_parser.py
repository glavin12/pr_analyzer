"""Section 3: one parser for both jest and vitest.

They share the `at file:line:col` / `❯ file:line:col` stack format
(`stacktrace.parse_js_stack`) and the `FAIL <file>` / `● <suite> ›
<test>` failure markers closely enough that two near-identical parser
classes would just be duplication. Which tool actually ran is recorded per
diagnostic in `tool`/`metadata["tool"]`, proving `StackTrace`/`TestOutcome`
generalize across ecosystems (Section 3's point), not just across two
copy-pasted parsers.
"""

import re

from .. import confidence, stacktrace
from ..model import (
    Diagnostic,
    DiagnosticType,
    LogLine,
    LogSection,
    Severity,
    SourceRange,
    TestOutcome,
    WorkflowMarker,
)
from ..process_failure import find_process_failure

_FAIL_BANNER_RE = re.compile(r"^(?:PASS|FAIL)\s+(?P<file>\S+)\s*$")
_BULLET_RE = re.compile(r"^\s*●\s+(?P<name>\S.*)$")
_VITEST_RUN_RE = re.compile(r"^\s*RUN\s+v\d")
_FOOTER_RE = re.compile(
    r"^(?:Test Suites|Tests|Snapshots|Time|Ran all|Test Files|Duration|Start at):"
)


class JsTestParser:
    name = "js_test"
    tool = None  # set per-diagnostic ("jest" or "vitest"), not fixed for the class
    is_fallback = False

    def detect(self, lines: list[LogLine], sections: tuple[LogSection, ...]) -> bool:
        if not any(_BULLET_RE.match(line.text) for line in lines):
            return False
        if any(_FAIL_BANNER_RE.match(line.text) or _VITEST_RUN_RE.match(line.text) for line in lines):
            return True
        return any(
            line.marker == WorkflowMarker.COMMAND
            and line.marker_body
            and ("jest" in line.marker_body.lower() or "vitest" in line.marker_body.lower())
            for line in lines
        )

    def parse(self, lines: list[LogLine], sections: tuple[LogSection, ...]) -> list[Diagnostic]:
        tool = self._detect_tool(lines)

        diagnostics: list[Diagnostic] = []
        current_file: str | None = None
        i = 0
        n = len(lines)
        while i < n:
            text = lines[i].text
            fail_match = _FAIL_BANNER_RE.match(text)
            if fail_match:
                current_file = fail_match.group("file")
                i += 1
                continue
            bullet_match = _BULLET_RE.match(text)
            if bullet_match:
                block, next_i = self._collect_block(lines, i)
                diagnostics.append(
                    self._build_diagnostic(bullet_match.group("name"), current_file, block, tool)
                )
                i = next_i
                continue
            i += 1

        process_failure = find_process_failure(lines, self.name)
        if process_failure is not None:
            diagnostics.append(process_failure)

        return diagnostics

    def _detect_tool(self, lines: list[LogLine]) -> str:
        for line in lines:
            if _VITEST_RUN_RE.match(line.text) or "vitest" in line.text.lower():
                return "vitest"
        return "jest"

    def _collect_block(self, lines: list[LogLine], start: int) -> tuple[list[LogLine], int]:
        block = [lines[start]]
        i = start + 1
        n = len(lines)
        while i < n:
            text = lines[i].text
            if _BULLET_RE.match(text) or _FAIL_BANNER_RE.match(text) or _FOOTER_RE.match(text):
                break
            block.append(lines[i])
            i += 1
        return block, i

    def _build_diagnostic(
        self, name: str, current_file: str | None, block: list[LogLine], tool: str
    ) -> Diagnostic:
        stack = stacktrace.parse_js_stack(block)
        located = stacktrace.primary_frame(stack)

        message = None
        expected = actual = None
        for line in block[1:]:
            text = line.text.strip()
            if not text:
                continue
            if text.startswith("Expected:"):
                expected = text[len("Expected:"):].strip()
                continue
            if text.startswith("Received:"):
                actual = text[len("Received:"):].strip()
                continue
            if message is None:
                message = text

        file_path = (located.file_path if located else None) or current_file
        test_id = f"{current_file}::{name}" if current_file else name

        return Diagnostic(
            type=DiagnosticType.TEST_FAILURE,
            severity=Severity.ERROR,
            tool=tool,
            message=message,
            file=file_path,
            line=located.line_number if located else None,
            column=located.column if located else None,
            source_range=SourceRange(block[0].raw_lineno, block[-1].raw_lineno),
            stack_trace=stack,
            test_id=test_id,
            exit_code=None,
            confidence=confidence.EXACT_TOOL_FORMAT,
            evidence=tuple(line.raw_lineno for line in block),
            metadata={
                "outcome": TestOutcome.FAILED.value,
                "expected": expected,
                "actual": actual,
                "tool": tool,
            },
            parser=self.name,
        )
