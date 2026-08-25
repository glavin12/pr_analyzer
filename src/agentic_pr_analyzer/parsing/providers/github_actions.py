import re

from ..model import WorkflowMarker

_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z) ?(.*)$")
_HASH_MARKER_RE = re.compile(
    r"^##\[(group|endgroup|error|warning|command|section|debug|notice)\](.*)$"
)
_COMMAND_ECHO_RE = re.compile(r"^\[command\](.*)$")

_DETECT_SCAN_LINES = 50


class GitHubActionsProvider:
    """Timestamp-prefixed lines + `##[...]` group/error/warning markers.

    Per-line timestamps are optional: GitHub Actions emits raw multi-line
    step output (e.g. a YAML `with:` block) as bare continuation lines with
    no timestamp prefix at all.
    """

    name = "github_actions"

    def detect(self, sample: str) -> bool:
        for line in sample.splitlines()[:_DETECT_SCAN_LINES]:
            if _TIMESTAMP_RE.match(line):
                return True
        return False

    def split_line(self, raw_line: str) -> tuple[str | None, str]:
        match = _TIMESTAMP_RE.match(raw_line)
        if not match:
            return None, raw_line
        return match.group(1), match.group(2)

    def marker_of(self, payload: str) -> tuple[WorkflowMarker | None, str | None]:
        match = _HASH_MARKER_RE.match(payload)
        if match:
            marker = WorkflowMarker(match.group(1))
            body = match.group(2).strip() or None
            return marker, body
        match = _COMMAND_ECHO_RE.match(payload)
        if match:
            return WorkflowMarker.COMMAND, match.group(1) or None
        return None, None
