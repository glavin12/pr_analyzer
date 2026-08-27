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
from .sanitize import mask_counted

_BOM = "﻿"
# Real ESC control byte only (U+001B) -- literal "\x1b[" appearing as text
# inside e.g. pytest parametrize IDs is untouched.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?=]*[A-Za-z]")

# The exact 9-boundary set str.splitlines() uses (see this module's
# docstring). \r\n MUST be listed first in the alternation so CRLF matches as
# ONE break, not two separate \r and \n matches.
_LINE_BREAK_RE = re.compile("\r\n|[\n\r\v\f\x1c\x1d\x1e\x85  ]")


def _bounded_splitlines(content: str, cap: int) -> tuple[list[str], bool]:
    """str.splitlines(), but stops after `cap` lines without materializing the rest.

    Peak memory for a huge input is proportional to len(content), not to
    max_total_lines, unless the split itself is bounded before it happens --
    content.splitlines() always builds the full line list first. This finds
    only the cap-th line-break's end offset (finditer is lazy, so scanning
    stops there) and slices the prefix, then defers to the real splitlines()
    for the boundary semantics.

    The regex above must stay byte-identical to splitlines()'s boundary set,
    forever -- every line number, evidence tuple, SourceRange,
    StackFrame.raw_lineno and LogSection bound rests on it. Guarded by
    test_bounded_splitlines_equals_splitlines_over_random_break_soup, which
    compares against the REAL str.splitlines() rather than a hardcoded
    expectation, so a future CPython change fails the test, not the product.

    ponytail: content[:end] slices a prefix copy (~cap*avg_line_len extra
    bytes) before re-splitting it; build lines directly from the finditer
    spans instead if that copy ever shows up in a profile.
    """
    end = None
    for i, m in enumerate(_LINE_BREAK_RE.finditer(content), 1):
        if i == cap:
            end = m.end()
            break
    if end is None:
        return content.splitlines(), False
    return content[:end].splitlines(), True


def _merge_max(dst: dict[str, int], src: dict[str, int]) -> None:
    """Merge per-class mask counts for ONE line as a per-class MAX, not a sum.

    `text`, `raw_text` and `marker_body` all cover the same spans of the same
    line -- marker_body is a substring of the payload, and raw_text differs
    from text only by ANSI escapes -- so a secret masked once would otherwise
    be counted two or three times. Summing across *lines* is correct and
    happens at the call site; summing across a line's fields is not.
    """
    for cls, n in src.items():
        if n > dst.get(cls, 0):
            dst[cls] = n


def normalize(
    content: str, provider: LogProvider, limits: ParseLimits
) -> tuple[list[LogLine], dict]:
    if content.startswith(_BOM):
        content = content[1:]

    # max_total_chars only matters for a log with too FEW line breaks to ever
    # hit max_total_lines on its own (a 500 MB single-line log never reaches
    # the line cap, so _bounded_splitlines would fall through to
    # content.splitlines() on the full input). Unconditionally slicing
    # content[:max_total_chars] whenever the input is merely longer than
    # that -- the seemingly obvious way to write this -- was measured to
    # make peak memory WORSE on ordinary large logs: the caller's own
    # reference to the original `content` stays alive for the rest of the
    # pipeline (nothing here can free it), so a second full-size slice sits
    # alongside it. For a 100 MB GitHub-Actions-shaped log, the 200,000-line
    # cap is already reached by ~24 MB in, so that slice is 100 MB of pure
    # waste. This bounded probe (finditer with pos/endpos scans in place, no
    # copy) checks cheaply whether the line cap would bind first, and only
    # pays for the char-level copy when it genuinely wouldn't.
    truncated_chars = False
    if len(content) > limits.max_total_chars:
        breaks_seen = 0
        for _ in _LINE_BREAK_RE.finditer(content, 0, limits.max_total_chars):
            breaks_seen += 1
            if breaks_seen >= limits.max_total_lines:
                break
        if breaks_seen < limits.max_total_lines:
            truncated_chars = True
            content = content[: limits.max_total_chars]

    raw_lines, truncated_lines = _bounded_splitlines(content, limits.max_total_lines)

    lines: list[LogLine] = []
    lines_over_limit = 0
    secrets_masked: dict[str, int] = {}
    for lineno, raw in enumerate(raw_lines, start=1):
        if len(raw) > limits.max_line_length:
            raw = raw[: limits.max_line_length]
            lines_over_limit += 1

        timestamp, payload = provider.split_line(raw)

        # ANSI is stripped BEFORE masking, and marker extraction runs on the
        # stripped form. An escape sequence ends in a word character ("m" in
        # "\x1b[0m"), which suppresses the \b that every token rule anchors
        # on -- so "##[group]Run x \x1b[0mghp_AAAA..." hid a real credential
        # from the masker while the same token on a plain line was caught.
        # marker_body is where that mattered: it flows into LogSection.title
        # (segmentation.py) and Diagnostic.message (process_failure.py) and
        # from there into to_json.
        has_ansi = "\x1b" in payload
        stripped = _ANSI_RE.sub("", payload) if has_ansi else payload

        # Still extracted before masking, not derived from the masked text: a
        # mask token could otherwise alter the "##[group]" prefix shape the
        # marker regex anchors on.
        marker, marker_body = provider.marker_of(stripped)

        line_counts: dict[str, int] = {}
        if marker_body is not None:
            marker_body, body_counts = mask_counted(marker_body)
            _merge_max(line_counts, body_counts)

        text, counts = mask_counted(stripped)
        _merge_max(line_counts, counts)
        if has_ansi:
            raw_text, raw_counts = mask_counted(payload)
            _merge_max(line_counts, raw_counts)
        else:
            # No ESC byte -- true for 99.9%+ of real lines -- so the stripped
            # form IS the payload and one mask() call serves both fields.
            raw_text = text

        for cls, n in line_counts.items():
            secrets_masked[cls] = secrets_masked.get(cls, 0) + n

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

    stats = {
        "truncated_lines": truncated_lines,
        "lines_over_limit": lines_over_limit,
        "truncated_chars": truncated_chars,
        "secrets_masked": secrets_masked,
    }
    return lines, stats
