"""Deterministic stack-trace parsing shared across ecosystems (Section 3).

Pure functions, `list[LogLine] -> StackTrace | None`. Python and JS only
this increment (brief SS7 decision); each other ecosystem is a new
`parse_<lang>_stack` function added later, not a refactor of these.
Never raises: a slice with no recognizable frames/exception degrades to
`None` rather than a guess.
"""

import re

from .model import LogLine, StackFrame, StackTrace

# Path segments that mark a frame as dependency/tooling code rather than
# project code -- checked as whole path segments so e.g. a project directory
# literally named "distiller" doesn't false-match "dist".
_NOT_IN_PROJECT = {"site-packages", ".tox", ".venv", "node_modules", "dist"}

# `--tb=short`: "<file>:<line>: in <func>". Non-greedy file group so a
# Windows drive-letter colon in the path doesn't get mistaken for the
# line-number separator (a drive letter is never followed by digits).
_PY_SHORT_FRAME_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+): in (?P<func>.+)$")
# Default long traceback: 'File "<file>", line <N>, in <func>'.
_PY_LONG_FRAME_RE = re.compile(r'^File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>.+)$')
# pytest's trailing crash-location line: "<file>:<line>: <ExceptionType>".
# Printed when there's no separate call frame to show (e.g. a bare `assert`
# directly in the test body) -- the only file/line evidence in that case, so
# it's treated as a frame with no function. Flush-left and never "E "-prefixed
# distinguishes it from the exception line and from indented source context.
_PY_LOCATION_RE = re.compile(r"^(?!E\s)(?P<file>\S.*?):(?P<line>\d+): (?P<rest>.+)$")
# pytest's exception line: "E<spaces><rest>". The indent width itself isn't
# a reliable header/continuation signal -- it varies with pytest's assertion
# rewriting (3 spaces for a plain "Type: msg" line, 7+ for a rewritten
# "assert ..." line) -- so header-vs-continuation is tracked positionally
# instead: the first "E "-line after a non-"E" line starts a new run and is
# the header; every immediately-following "E "-line is a continuation.
_PY_EXC_LINE_RE = re.compile(r"^E(?:\s+)(?P<rest>\S.*)$")
_PY_EXC_TYPE_MSG_RE = re.compile(r"^(?P<type>[A-Za-z_][\w.]*): (?P<msg>.*)$")

# jest: "at fn (file:line:col)" / "at file:line:col". vitest's short frame
# form uses "❯" instead of "at" -- same shape otherwise, so one regex
# covers both rather than a per-tool duplicate.
_JS_FRAME_RE = re.compile(
    r"^\s*(?:at|❯)\s+(?:(?P<func>[^()]+?)\s+\()?"
    r"(?P<file>[^()\s][^()]*?):(?P<line>\d+):(?P<col>\d+)\)?\s*$"
)


def _in_project(file_path: str) -> bool:
    parts = re.split(r"[\\/]+", file_path)
    return not any(part in _NOT_IN_PROJECT for part in parts)


def parse_python_traceback(lines: list[LogLine]) -> StackTrace | None:
    frames: list[StackFrame] = []
    exception_type: str | None = None
    message: str | None = None
    in_exception_run = False

    for line in lines:
        text = line.text
        match = (
            _PY_SHORT_FRAME_RE.match(text)
            or _PY_LONG_FRAME_RE.match(text)
            or _PY_LOCATION_RE.match(text)
        )
        if match:
            in_exception_run = False
            file_path = match.group("file")
            func = match.groupdict().get("func")
            frames.append(
                StackFrame(
                    file_path=file_path,
                    line_number=int(match.group("line")),
                    column=None,
                    function=func.strip() if func else None,
                    raw_lineno=line.raw_lineno,
                    in_project=_in_project(file_path),
                    raw_text=line.raw_text,
                )
            )
            continue

        exc_match = _PY_EXC_LINE_RE.match(text)
        if exc_match:
            if not in_exception_run:
                # Chained exceptions ("During handling of...") start a new
                # run; keep overwriting so the *final* E-line wins, per the
                # engine's rule that only the last raised exception is
                # reported. A continuation line within the same run (e.g.
                # "E     Expected: ...") is not a new exception -- ignored.
                rest = exc_match.group("rest")
                type_msg = _PY_EXC_TYPE_MSG_RE.match(rest)
                if type_msg:
                    exception_type = type_msg.group("type")
                    message = type_msg.group("msg")
                else:
                    exception_type = None
                    message = rest
            in_exception_run = True
            continue

        in_exception_run = False

    if not frames and exception_type is None and message is None:
        return None
    return StackTrace(exception_type=exception_type, message=message, frames=tuple(frames))


def parse_js_stack(lines: list[LogLine]) -> StackTrace | None:
    frames: list[StackFrame] = []
    for line in lines:
        match = _JS_FRAME_RE.match(line.text)
        if not match:
            continue
        file_path = match.group("file")
        frames.append(
            StackFrame(
                file_path=file_path,
                line_number=int(match.group("line")),
                column=int(match.group("col")),
                function=match.group("func"),
                raw_lineno=line.raw_lineno,
                in_project=_in_project(file_path),
                raw_text=line.raw_text,
            )
        )

    if not frames:
        return None
    return StackTrace(exception_type=None, message=None, frames=tuple(frames))


def primary_frame(trace: StackTrace | None) -> StackFrame | None:
    """The frame a diagnostic's file/line/column get pinned to: the last

    in-project frame (closest to the actual assertion/call site -- pytest's
    chained-exception blocks and jest/vitest's dependency frames both put
    project code last), falling back to the trace's last frame when nothing
    in it is in-project.
    """
    if trace is None or not trace.frames:
        return None
    return next((f for f in reversed(trace.frames) if f.in_project), trace.frames[-1])
