from typing import Protocol

from ..model import Diagnostic, LogLine, LogSection


class Parser(Protocol):
    name: str
    tool: str | None
    # True for the terminal GenericParser: it only contributes diagnostics
    # when no specialized parser produced any (registry contract).
    is_fallback: bool

    def detect(self, lines: list[LogLine], sections: tuple[LogSection, ...]) -> bool: ...

    def parse(
        self, lines: list[LogLine], sections: tuple[LogSection, ...]
    ) -> list[Diagnostic]: ...
