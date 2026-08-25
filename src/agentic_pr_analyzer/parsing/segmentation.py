"""LogLine list -> (LogLine list with section_id filled, LogSection tree).

`##[group]`/`##[endgroup]` push/pop a stack; unbalanced markers auto-close
at EOF (last line's raw_lineno). `[command]` echo lines are tagged via
their own `marker` field but don't open a section -- they're ordinary
content of whatever group they appear inside.

LogLine is frozen, so section assignment produces a new list
(`dataclasses.replace`) rather than mutating in place.
"""

import dataclasses

from .model import LogLine, LogSection, WorkflowMarker


def build_sections(lines: list[LogLine]) -> tuple[tuple[LogLine, ...], tuple[LogSection, ...]]:
    built: dict[int, dict] = {}
    open_stack: list[int] = []
    next_id = 0
    new_lines: list[LogLine] = []

    for line in lines:
        if line.marker == WorkflowMarker.GROUP:
            parent_id = open_stack[-1] if open_stack else None
            sec_id = next_id
            next_id += 1
            built[sec_id] = {
                "id": sec_id,
                "title": line.marker_body,
                "kind": "group",
                "start_lineno": line.raw_lineno,
                "end_lineno": line.raw_lineno,
                "parent_id": parent_id,
            }
            open_stack.append(sec_id)
            for open_id in open_stack:
                built[open_id]["end_lineno"] = line.raw_lineno
            new_lines.append(dataclasses.replace(line, section_id=sec_id))
            continue

        if line.marker == WorkflowMarker.ENDGROUP and open_stack:
            for open_id in open_stack:
                built[open_id]["end_lineno"] = line.raw_lineno
            sec_id = open_stack.pop()
            new_lines.append(dataclasses.replace(line, section_id=sec_id))
            continue

        if open_stack:
            for open_id in open_stack:
                built[open_id]["end_lineno"] = line.raw_lineno
            new_lines.append(dataclasses.replace(line, section_id=open_stack[-1]))
        else:
            new_lines.append(line)

    sections = tuple(LogSection(**built[i]) for i in sorted(built))
    return tuple(new_lines), sections
