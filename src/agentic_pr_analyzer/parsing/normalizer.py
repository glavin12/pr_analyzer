"""Raw log text -> list[LogLine].

`content.splitlines()` (NOT `content.split("\\r\\n")`): splitlines is what
gives the verified 2323-line / ESC@226 / FAILURES@2276 anchor-fixture
numbers; a naive CRLF split under-counts and breaks every downstream line
reference.
"""

import re

from .limits import ParseLimits
from .model import LogLine
from .providers.base import LogProvider
from .sanitize import mask

_BOM = "﻿"
# Real ESC control byte only (U+001B) -- literal "\x1b[" appearing as text
# inside e.g. pytest parametrize IDs is untouched.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?=]*[A-Za-z]")


def normalize(
    content: str, provider: LogProvider, limits: ParseLimits
) -> tuple[list[LogLine], dict]:
    if content.startswith(_BOM):
        content = content[1:]

    raw_lines = content.splitlines()
    truncated_lines = len(raw_lines) > limits.max_total_lines
    if truncated_lines:
        raw_lines = raw_lines[: limits.max_total_lines]

    lines: list[LogLine] = []
    lines_over_limit = 0
    for lineno, raw in enumerate(raw_lines, start=1):
        if len(raw) > limits.max_line_length:
            raw = raw[: limits.max_line_length]
            lines_over_limit += 1

        timestamp, payload = provider.split_line(raw)
        marker, marker_body = provider.marker_of(payload)
        text = mask(_ANSI_RE.sub("", payload))
        raw_text = mask(payload)

        lines.append(
            LogLine(
                raw_lineno=lineno,
                raw_text=raw_text,
                text=text,
                timestamp=timestamp,
                marker=marker,
                marker_body=marker_body,
                section_id=None,
            )
        )

    stats = {"truncated_lines": truncated_lines, "lines_over_limit": lines_over_limit}
    return lines, stats
