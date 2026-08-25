"""Section 2: the pytest tool-specific parser.

Detects a pytest run from its banner/summary lines, extracts one
TEST_FAILURE diagnostic per failed/errored test from the `= FAILURES =`
and/or `= ERRORS =` blocks (stack trace via `stacktrace.parse_python_traceback`),
and co-emits the shared PROCESS_FAILURE (see `process_failure.py` docstring
for why: the registry only falls back to GenericParser when no specialized
parser fired, so the exit-code diagnostic would otherwise vanish once this
parser fires).

`= ERRORS =` (collection errors, fixture setup/teardown errors) is a
separate section from `= FAILURES =`, discovered by actually running pytest
against deliberately-broken files locally rather than guessed -- its
separator lines read "ERROR at setup of <test>" / "ERROR at teardown of
<test>" / "ERROR collecting test session" rather than a bare test name, and
its short-summary line is sometimes just "ERROR <test_id>" with no
" - <msg>" suffix at all (unlike FAILURES entries, which always have one).
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
# The " - <msg>" suffix is present for FAILURES entries but not always for
# ERRORS entries (a bare collection error has neither a test_id nor a msg
# here at all: just "ERROR ").
_SHORT_SUMMARY_LINE_RE = re.compile(r"^(?:FAILED|ERROR) (?P<test_id>\S+)(?: - (?P<msg>.*))?$")
_BANNER_LINE_RE = re.compile(r"^=+\s*(?P<body>.+?)\s*=+$")
_BLOCK_START_RE = re.compile(r"^=+\s*(?:FAILURES|ERRORS)\s*=+$")
_COUNT_PAIR_RE = re.compile(r"(\d+)\s+([A-Za-z]+)")
_DURATION_RE = re.compile(r"in ([\d.]+)s")
_EXPECTED_RE = re.compile(r"^E\s+Expected(?: \w+)?: (?P<val>.*)$")
_ACTUAL_RE = re.compile(r"^E\s+Actual(?: \w+)?: (?P<val>.*)$")
# ERRORS-block separators wrap the test name rather than being it, e.g.
# "ERROR at setup of test_foo" / "ERROR at teardown of test_foo" /
# "ERROR collecting test session" -- strip the wrapper to recover the bare
# name for progress/short-summary correlation.
_ERROR_PREFIX_RE = re.compile(r"^ERROR (?:at (?:setup|teardown) of |collecting )(?P<rest>.*)$")

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
        blocks, short_summary_start = self._find_blocks(lines)

        short_summary: dict[str, str | None] = {}
        run_summary: dict = {}
        if short_summary_start is not None:
            tail = lines[short_summary_start:]
            short_summary = self._collect_short_summary(tail)
            run_summary = self._parse_run_summary(tail)

        diagnostics: list[Diagnostic] = []
        for start, end in blocks:
            for name, chunk in self._split_failure_chunks(lines[start:end]):
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

    def _find_blocks(self, lines: list[LogLine]) -> tuple[list[tuple[int, int]], int | None]:
        """Scans for `= FAILURES =` / `= ERRORS =` blocks -- either, both, in

        either order, or neither -- plus the `short test summary info`
        offset. Any `=...=`-shaped banner line closes whatever block is
        currently open; a FAILURES/ERRORS banner also opens the next one.
        """
        blocks: list[tuple[int, int]] = []
        short_summary_start = None
        block_start: int | None = None
        for i, line in enumerate(lines):
            text = line.text
            is_banner = _BANNER_LINE_RE.match(text) is not None
            if is_banner and block_start is not None:
                blocks.append((block_start, i))
                block_start = None
            if _BLOCK_START_RE.match(text):
                block_start = i + 1
            elif is_banner and short_summary_start is None and _SHORT_SUMMARY_MARKER in text:
                short_summary_start = i + 1
        if block_start is not None:
            blocks.append((block_start, len(lines)))
        return blocks, short_summary_start

    def _collect_short_summary(self, tail: list[LogLine]) -> dict[str, str | None]:
        summary: dict[str, str | None] = {}
        for line in tail:
            match = _SHORT_SUMMARY_LINE_RE.match(line.text)
            if match:
                summary[match.group("test_id")] = match.group("msg")
        return summary

    def _bare_name(self, separator_name: str) -> str:
        match = _ERROR_PREFIX_RE.match(separator_name)
        return match.group("rest") if match else separator_name

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
        short_summary: dict[str, str | None],
        run_summary: dict,
    ) -> Diagnostic:
        bare = self._bare_name(name)
        # A per-test id ends with "::<bare>"; a bare-file collection error
        # ("ERROR collecting tests/test_foo.py") has no "::" at all -- the
        # whole id equals the file path itself, so try both.
        matched_id = next(
            (tid for tid in short_summary if tid == bare or tid.endswith("::" + bare)), None
        )
        if matched_id is None:
            matched_id = next(
                (tid for tid in progress if tid == bare or tid.endswith("::" + bare)), None
            )
        test_id = matched_id or name

        # An ERRORS-block separator ("ERROR at setup of ...", "ERROR
        # collecting ...") is definitionally an error, not a failure, even
        # when there's no progress-line outcome to correlate against (e.g. a
        # collection error, which has no test node at all).
        default_outcome = TestOutcome.ERROR if name.startswith("ERROR") else TestOutcome.FAILED
        outcome, progress_line = progress.get(test_id, (default_outcome, None))

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
