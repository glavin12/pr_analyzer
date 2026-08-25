from dataclasses import dataclass


@dataclass(frozen=True)
class ParseLimits:
    """Bounds threaded through every pipeline stage. Exceeding a bound

    truncates and records it in stats/truncated -- it never raises. Generous
    defaults: real CI logs stay well under these in practice.
    """

    max_total_lines: int = 200_000
    max_line_length: int = 20_000
    max_diagnostics: int = 500
    max_context_lines: int = 50
