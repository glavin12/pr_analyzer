"""Section 6 performance baselines. Behind the `perf` marker, excluded from
the default run.

    uv run pytest -m perf -s -q

Procedure (this docstring is the documented procedure -- keep it in sync):

1. Generate input via `_synth_log(target_bytes, seed=1337)`. Deterministic,
   NEVER committed: a multi-MB fixture would balloon clone size and add
   nothing the generator doesn't.
2. TIME: one warm-up run (imports, regex compilation, page-in), then `min()`
   of 3 `time.perf_counter()` runs. `min`, not mean -- least polluted by
   scheduler noise. `tracemalloc` OFF.
3. MEMORY: a SEPARATE run with `tracemalloc.start()` called before the input
   string is allocated, so the input is counted rather than silently excluded.

**Time and memory must be measured in separate runs.** `tracemalloc` inflates
the same parse by roughly 2.6x; measuring both at once produces a meaningless
number.

Numbers are RECORDED in docs/perf-baselines.md, not asserted. The assertions
here use generous absolute ceilings (30-40x headroom) and machine-independent
ratios. A tight wall-clock assertion makes CI flaky on a different machine, and
the first response to a flaky perf test is always to delete it.

Needs ~1 GB free for the 100 MB case.
"""

import random
import time
import tracemalloc
from pathlib import Path

import pytest

from agentic_pr_analyzer.parsing import ParseLimits, parse_log, to_json

pytestmark = pytest.mark.perf

ANCHOR = Path("tests/fixtures/raw_logs/pallets/click/32472305359_96741461054.log")

MB = 1024 * 1024


def _synth_log(target_bytes: int, seed: int = 1337) -> str:
    """Deterministic ~target_bytes GitHub-Actions-shaped log, generated not committed.

    Seeded so a recorded baseline is reproducible. Built from the real anchor
    so line shapes (timestamps, ##[group], pytest blocks) are realistic rather
    than a uniform synthetic that would flatter the regex costs.
    """
    rng = random.Random(seed)
    template = ANCHOR.read_bytes().decode("utf-8", errors="replace").splitlines()
    out, size = [], 0
    while size < target_bytes:
        line = rng.choice(template)
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _time_parse(content: str, limits: ParseLimits | None = None) -> float:
    limits = limits or ParseLimits()
    parse_log(content, limits=limits)  # warm-up: imports, regex compilation, page-in

    timings = []
    for _ in range(3):
        start = time.perf_counter()
        parse_log(content, limits=limits)
        timings.append(time.perf_counter() - start)
    return min(timings)


def _peak_mb(target_bytes: int) -> float:
    """tracemalloc started BEFORE the input is allocated, so the input string
    counts toward the peak rather than being silently excluded."""
    tracemalloc.start()
    try:
        content = _synth_log(target_bytes)
        parse_log(content)
        return tracemalloc.get_traced_memory()[1] / MB
    finally:
        tracemalloc.stop()


def _report(case: str, input_mb: float, seconds: float, peak_mb: float | None = None):
    print(f"PERF {case} {input_mb:.1f} {seconds:.3f} {peak_mb if peak_mb is not None else '-'}")


def test_perf_anchor_baseline():
    content = ANCHOR.read_bytes().decode("utf-8", errors="replace")
    seconds = _time_parse(content)
    _report("anchor", len(content) / MB, seconds)
    assert seconds < 5.0


def test_perf_10mb_completes_within_generous_ceiling():
    content = _synth_log(10 * MB)
    seconds = _time_parse(content)
    _report("synthetic-10mb", len(content) / MB, seconds)
    assert seconds < 30.0


def test_perf_50mb_completes_within_generous_ceiling():
    content = _synth_log(50 * MB)
    seconds = _time_parse(content)
    _report("synthetic-50mb", len(content) / MB, seconds)
    assert seconds < 60.0


def test_perf_100mb_completes_within_generous_ceiling():
    content = _synth_log(100 * MB)
    seconds = _time_parse(content)
    _report("synthetic-100mb", len(content) / MB, seconds)
    assert seconds < 120.0


def test_perf_scales_sublinearly_in_time():
    """THE assertion that actually catches an O(n^2) reintroduction. A ratio,
    not a wall clock, so it is machine-independent. Both inputs hit the same
    max_total_lines ceiling, so 5x the bytes must cost well under 8x the time."""
    t10 = _time_parse(_synth_log(10 * MB))
    t50 = _time_parse(_synth_log(50 * MB))
    print(f"PERF ratio t50/t10 {t50 / t10:.2f}")
    assert t50 / t10 < 8.0


def test_perf_peak_memory_bounded_by_limits_not_input():
    """THE D2 acceptance test. Before the bounded split, peak scaled with
    len(content) rather than with max_total_lines, so this failed."""
    peak50 = _peak_mb(50 * MB)
    peak100 = _peak_mb(100 * MB)
    print(f"PERF peak50 {peak50:.1f} peak100 {peak100:.1f} ratio {peak100 / peak50:.2f}")
    assert peak100 < 1.6 * peak50


def test_perf_report_size_bounded_by_max_context_lines():
    header = [
        "2026-01-01T00:00:00.0000000Z ============================= test session starts ==============================",
        "2026-01-01T00:00:00.0000000Z ================================== FAILURES ===================================",
        "2026-01-01T00:00:00.0000000Z ________________________________ test_big _____________________________________",
        "2026-01-01T00:00:00.0000000Z tests\\test_big.py:1: in test_big",
    ]
    body = ["2026-01-01T00:00:00.0000000Z     filler line %d" % i for i in range(150_000)]
    tail = [
        "2026-01-01T00:00:00.0000000Z E   AssertionError: boom",
        "2026-01-01T00:00:00.0000000Z ##[error]Process completed with exit code 1.",
    ]
    content = "\n".join(header + body + tail)
    size = len(to_json(parse_log(content)))
    print(f"PERF failures-150k {len(content) / MB:.1f} - json_bytes={size}")
    assert size < 2_000_000
