"""Section 2: the pytest tool-specific parser.

Detects a pytest run from its banner/summary lines, extracts one
TEST_FAILURE diagnostic per failed/errored test from the `= FAILURES =`
block (stack trace via `stacktrace.parse_python_traceback`), and co-emits
the shared PROCESS_FAILURE (see `process_failure.py` docstring for why: the
registry only falls back to GenericParser when no specialized parser fired,
so the exit-code diagnostic would otherwise vanish once this parser fires).
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

_SESSION_START_MARKER = "test session starts"
_FAILURES_MARKER = " FAILURES "
_SHORT_SUMMARY_MARKER = "short test summary info"

_PROGRESS_RE = re.compile(
    r"^(?P<test_id>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b"
    r"(?:\s*\((?P<reason>[^)]*)\))?\s*(?:\[\s*\d+%\])?\s*$"
)
_SEPARATOR_RE = re.compile(r"^_{3,}\s*(?P<name>.+?)\s*_{3,}$")
_SHORT_SUMMARY_LINE_RE = re.compile(r"^(?:FAILED|ERROR) (?P<test_id>\S+) - (?P<msg>.*)$")
_BANNER_LINE_RE = re.compile(r"^=+\s*(?P<body>.+?)\s*=+$")
_COUNT_PAIR_RE = re.compile(r"(\d+)\s+([A-Za-z]+)")
_DURATION_RE = re.compile(r"in ([\d.]+)s")
_EXPECTED_RE = re.compile(r"^E\s+Expected(?: \w+)?: (?P<val>.*)$")
_ACTUAL_RE = re.compile(r"^E\s+Actual(?: \w+)?: (?P<val>.*)$")

_OUTCOME_MAP = {
    "PASSED": TestOutcome.PASSED,
    "FAILED": TestOutcome.FAILED,
    "ERROR": TestOutcome.ERROR,
    "SKIPPED": TestOutcome.SKIPPED,
    "XFAIL": TestOutcome.XFAILED,
    "XPASS": TestOutcome.XPASSED,
}


class PytestParser:
    name = "pytest"
    tool = "pytest"
    is_fallback = False

    def detect(self, lines: list[LogLine], sections: tuple[LogSection, ...]) -> bool:
        for line in lines:
            text = line.text
            if (
                _SESSION_START_MARKER in text
                or _FAILURES_MARKER in text
                or _SHORT_SUMMARY_MARKER in text
            ):
                return True
            if line.marker == WorkflowMarker.COMMAND and line.marker_body and "pytest" in line.marker_body:
                return True
        return False

    def parse(self, lines: list[LogLine], sections: tuple[LogSection, ...]) -> list[Diagnostic]:
        progress = self._collect_progress(lines)
        failures_start, failures_end, short_summary_start = self._find_block_bounds(lines)

        short_summary: dict[str, str] = {}
        run_summary: dict = {}
        if short_summary_start is not None:
            tail = lines[short_summary_start:]
            short_summary = self._collect_short_summary(tail)
            run_summary = self._parse_run_summary(tail)

        diagnostics: list[Diagnostic] = []
        if failures_start is not None:
            for name, chunk in self._split_failure_chunks(lines[failures_start:failures_end]):
                diagnostics.append(
                    self._build_diagnostic(name, chunk, progress, short_summary, run_summary)
                )

        process_failure = find_process_failure(lines, self.name)
        if process_failure is not None:
            diagnostics.append(process_failure)

        return diagnostics

    def _collect_progress(self, lines: list[LogLine]) -> dict[str, tuple[TestOutcome, LogLine]]:
        progress: dict[str, tuple[TestOutcome, LogLine]] = {}
        for line in lines:
            match = _PROGRESS_RE.match(line.text)
            if match:
                progress[match.group("test_id")] = (_OUTCOME_MAP[match.group("outcome")], line)
        return progress

    def _find_block_bounds(
        self, lines: list[LogLine]
    ) -> tuple[int | None, int, int | None]:
        failures_start = None
        failures_end = len(lines)
        short_summary_start = None
        for i, line in enumerate(lines):
            if failures_start is None and _FAILURES_MARKER in line.text:
                failures_start = i + 1
            elif (
                failures_start is not None
                and short_summary_start is None
                and _SHORT_SUMMARY_MARKER in line.text
            ):
                failures_end = i
                short_summary_start = i + 1
        return failures_start, failures_end, short_summary_start

    def _collect_short_summary(self, tail: list[LogLine]) -> dict[str, str]:
        summary = {}
        for line in tail:
            match = _SHORT_SUMMARY_LINE_RE.match(line.text)
            if match:
                summary[match.group("test_id")] = match.group("msg")
        return summary

    def _parse_run_summary(self, tail: list[LogLine]) -> dict:
        for line in tail:
            match = _BANNER_LINE_RE.match(line.text)
            if not match:
                continue
            body = match.group("body")
            counts = {label: int(n) for n, label in _COUNT_PAIR_RE.findall(body)}
            if not counts:
                continue
            duration = _DURATION_RE.search(body)
            if duration:
                counts["duration_seconds"] = float(duration.group(1))
            return counts
        return {}

    def _split_failure_chunks(
        self, lines: list[LogLine]
    ) -> list[tuple[str, list[LogLine]]]:
        chunks: list[tuple[str, list[LogLine]]] = []
        name: str | None = None
        current: list[LogLine] = []
        for line in lines:
            match = _SEPARATOR_RE.match(line.text)
            if match:
                if name is not None:
                    chunks.append((name, current))
                name = match.group("name")
                current = [line]
                continue
            if name is not None:
                current.append(line)
        if name is not None:
            chunks.append((name, current))
        return chunks

    def _build_diagnostic(
        self,
        name: str,
        chunk: list[LogLine],
        progress: dict[str, tuple[TestOutcome, LogLine]],
        short_summary: dict[str, str],
        run_summary: dict,
    ) -> Diagnostic:
        matched_id = next((tid for tid in short_summary if tid.endswith("::" + name)), None)
        if matched_id is None:
            matched_id = next((tid for tid in progress if tid.endswith("::" + name)), None)
        test_id = matched_id or name

        outcome, progress_line = progress.get(test_id, (TestOutcome.FAILED, None))

        stack = stacktrace.parse_python_traceback(chunk)
        located = stacktrace.primary_frame(stack)

        message = short_summary.get(test_id)
        if message is None and stack is not None:
            message = stack.message

        expected = actual = None
        for line in chunk:
            expected_match = _EXPECTED_RE.match(line.text)
            if expected_match:
                expected = expected_match.group("val")
            actual_match = _ACTUAL_RE.match(line.text)
            if actual_match:
                actual = actual_match.group("val")

        evidence_lines = set(range(chunk[0].raw_lineno, chunk[-1].raw_lineno + 1))
        if progress_line is not None:
            evidence_lines.add(progress_line.raw_lineno)

        return Diagnostic(
            type=DiagnosticType.TEST_FAILURE,
            severity=Severity.ERROR,
            tool=self.tool,
            message=message,
            file=located.file_path if located else None,
            line=located.line_number if located else None,
            column=located.column if located else None,
            source_range=SourceRange(chunk[0].raw_lineno, chunk[-1].raw_lineno),
            stack_trace=stack,
            test_id=test_id,
            exit_code=None,
            confidence=confidence.EXACT_TOOL_FORMAT,
            evidence=tuple(sorted(evidence_lines)),
            metadata={
                "outcome": outcome.value,
                "expected": expected,
                "actual": actual,
                "run_summary": run_summary,
                "tb_style": "short",
            },
            parser=self.name,
        )
