from ..model import WorkflowMarker


class GenericProvider:
    """No-prefix / no-marker fallback provider. Always detects (terminal)."""

    name = "generic"

    def detect(self, sample: str) -> bool:
        return True

    def split_line(self, raw_line: str) -> tuple[str | None, str]:
        return None, raw_line

    def marker_of(self, payload: str) -> tuple[WorkflowMarker | None, str | None]:
        return None, None
