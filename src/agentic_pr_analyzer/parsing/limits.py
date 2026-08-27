from dataclasses import dataclass


@dataclass(frozen=True)
class ParseLimits:
    """Bounds threaded through every pipeline stage. Exceeding a bound

    truncates and records it in stats/truncated -- it never raises. Generous
    defaults: real CI logs stay well under these in practice.

    max_total_chars is applied *before* the line split, so peak memory is
    bounded by min(len(content), max_total_chars) rather than by the input.
    max_total_lines alone cannot bound a log of few, enormous lines -- a
    500 MB single-line log never reaches 200,000 lines, so the split would
    otherwise fall through to the full input. Default 100 MB is a safety
    valve, not a shaping limit: a measured 100 MB log parses in a few
    seconds (see docs/perf-baselines.md); real job logs are <10 MB.
    """

    max_total_chars: int = 100_000_000
    max_total_lines: int = 200_000
    max_line_length: int = 20_000
    max_diagnostics: int = 500
    # Caps Diagnostic.evidence length, keeping the FIRST N entries (Section 5
    # ranks on min(evidence) and derives section_id from evidence[0]). The
    # full extent stays in Diagnostic.source_range, so capping samples
    # evidence, never loses it. Wired in by pipeline.py's evidence cap
    # (Section 6, D4) -- not this file's call sites.
    max_context_lines: int = 50
