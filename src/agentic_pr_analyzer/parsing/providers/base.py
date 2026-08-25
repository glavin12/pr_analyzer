from typing import Protocol

from ..model import WorkflowMarker


class LogProvider(Protocol):
    name: str

    def detect(self, sample: str) -> bool: ...

    def split_line(self, raw_line: str) -> tuple[str | None, str]: ...

    def marker_of(self, payload: str) -> tuple[WorkflowMarker | None, str | None]: ...
