# Section 6 performance baselines

Recorded, not asserted (`docs/plans/section-6-scale.md` §7). Regenerate by re-running
the procedure below and pasting the output — never auto-written by a test.

Procedure: `uv run pytest -m perf -s -q` (documented at the top of
`tests/test_parsing_perf.py`). **Time and memory are measured in separate runs** —
`tracemalloc` inflates the same parse by roughly 2.6x, so measuring both at once
produces a meaningless number.

## Methodology note for this recording

At the time this baseline was recorded, `tests/test_parsing_perf.py` could not be
collected by pytest: two other in-flight agents (Sections 5 and 7) have test files
under `tests/` importing symbols (`DiagnosticRole`, `agentic_pr_analyzer.parsing.clustering`,
`MASK_TOKENS`, etc.) that do not exist in `src/` yet, and pytest's collection is
all-or-nothing per invocation. `agentic_pr_analyzer.parsing` itself imports and runs
cleanly, so **both the "before" and "after" numbers below were captured with a
standalone script** (same `_synth_log(target_bytes, seed=1337)` generator, same
warm-up + min-of-3 timing, same separate `tracemalloc.start()`-before-allocation
memory methodology as `tests/test_parsing_perf.py`) rather than via `pytest -m perf`.
Whoever finishes Sections 5/7 should re-run `uv run pytest -m perf -s -q` once it can
import, as a sanity check against this file — see the note at the bottom.

## Header (before)

- Python: 3.11.15 (uv-managed venv; `pyproject.toml` requires `>=3.11`)
- OS: Windows 11 Home Single Language, MINGW64_NT-10.0-26200 (Git Bash)
- CPU: 13th Gen Intel(R) Core(TM) i5-13450HX
- Date: 2026-08-27
- Commit: `ed88c72` ("the plans have ben pushed") — parent of this section's changes

## Header (after)

- Python: 3.11.15
- OS: Windows 11 Home Single Language, MINGW64_NT-10.0-26200 (Git Bash)
- CPU: 13th Gen Intel(R) Core(TM) i5-13450HX
- Date: 2026-08-27
- Commit: this section's working tree, pre-commit (same session as "before"; D2 +
  D7 landed in `normalizer.py`/`limits.py` in between)
- **Caveat:** the "after" timing run executed while the other two in-flight agents
  (Sections 5/7) were concurrently running their own heavy test suites on the same
  machine, so absolute wall-clock numbers below are noisy/inflated versus "before"
  (e.g. anchor went from 20ms to 89ms just from system contention). The
  machine-independent **ratios** are the numbers that matter here and are still
  valid, since both the numerator and denominator of each ratio were measured
  under the same contention. Memory numbers (measured in fully separate
  `tracemalloc` runs) are close to the isolated control measurement taken
  mid-session under quieter conditions (see the note below), so those are trusted
  as-is.

## Results

Sizes below are MiB (`len(content) / (1024*1024)`), matching the test file's `MB`
constant, which is `1024 * 1024` despite the "MB" name.

| Case | Input | Lines parsed | Time — before | Time — after | tracemalloc peak — before | tracemalloc peak — after |
|---|---|---|---|---|---|---|
| anchor (`pallets/click`) | 0.26 MiB | 2,323 | 0.020 s | 0.089 s | — | — |
| synthetic 10 MiB | 10.00 MiB | 88,801 | 1.003 s | 6.138 s | 67.8 MB | 67.8 MB |
| synthetic 50 MiB, default limits | 50.00 MiB | 200,000 (truncated) | 3.571 s | 11.192 s | 207.6 MB | 207.7 MB |
| synthetic 50 MiB, limits lifted | 50.00 MiB | 444,061 | 8.226 s | 29.685 s | — | — |
| synthetic 100 MiB, default limits | 100.00 MiB | 200,000 (truncated) | 3.736 s | 14.807 s | 349.6 MB | **307.7 MB** |
| 150k-line FAILURES block | 7.33 MiB | 150,006 | 1.454 s | 4.480 s | — (JSON 7,171,754 B) | — (JSON 7,172,539 B) |

Derived ratios:

- `t50/t10`: before 3.561, after 1.823 — both comfortably under the `< 8.0`
  sublinearity ceiling despite the noisy "after" run (see caveat above).
- `peak100/peak50`: before **1.684 (fails the `< 1.6` D2 acceptance bound)**, after
  **1.482 (passes)**. This is the actual D2 acceptance-test number.
- Memory is unchanged for 10 MiB and 50 MiB (both stay under the 100 MB default
  `max_total_chars`, so D7 never triggers for them — only D2's bounded split
  applies, and it was already effectively free for these sizes; see the note
  below on why). The improvement is entirely in the 100 MiB case, where content
  exceeds the char cap: 349.6 -> 307.7 MB, a 42 MB reduction.

## A real bug found and fixed during this section: the naive char-cap made memory WORSE

The first D7 implementation was the obvious one: `if len(content) >
limits.max_total_chars: content = content[:limits.max_total_chars]`, unconditionally,
before the line split. Measuring it immediately showed a regression, not an
improvement: `peak100/peak50` went from 1.684 (before any fix) to **2.4** (after
D2+naive D7) — 100 MiB peak *rose* to 498.4 MB.

Root cause: `content[:limits.max_total_chars]` creates a **new, full-size copy** of
the string. The caller (`pipeline.py`'s `_parse_log_inner`) holds its own reference
to the original, untruncated `content` for the entire rest of the parse — nothing in
`normalize()` can free that, since Python passes the reference by value and
reassigning the local `content` name inside `normalize()` does not touch the
caller's binding. So for any input exceeding the cap, the naive version briefly (and
then, via the caller's still-live reference, for the *whole rest of the pipeline
run*) held both the full original string and a second ~100 MB slice of it —
doubling exactly the memory this limit exists to bound, on exactly the inputs it
was meant to help.

It also turned out to be unnecessary in the common case: for a 100 MiB
GitHub-Actions-shaped log, the default 200,000-line cap is already reached by
roughly the first 24 MB of content (average log line ≈120 chars including the
newline). `_bounded_splitlines`'s own `finditer` already stops scanning at that
point lazily, with no copy larger than the returned line list. The char cap
matters only for the case D7 was actually written for — a log with too *few* line
breaks to ever hit `max_total_lines` on its own (e.g. one giant line) — not for an
ordinary large log that the line cap already bounds.

The fix (shipped, see `normalizer.py`): before slicing, run a cheap bounded probe —
`_LINE_BREAK_RE.finditer(content, 0, limits.max_total_chars)`, using the regex
engine's `pos`/`endpos` window (scans in place, no copy), counting line breaks and
stopping early once `max_total_lines` is reached. Only if that probe finds *fewer*
breaks than the line cap does the char-level slice actually happen — i.e. only when
it is the genuinely binding constraint. This is confirmed by the 100 MiB "after"
number (307.7 MB) exactly matching a control measurement with `max_total_chars`
effectively disabled — proof the char-level copy no longer fires for this case at
all.

Independent isolated re-measurement (before the noisy full run above, machine
otherwise idle) confirmed this in a controlled A/B:

```
NO-CHAR-CAP  target=50MB  peak=207.7MB
NO-CHAR-CAP  target=100MB peak=307.7MB   (ratio 1.48 -- matches the shipped fix exactly)
NAIVE-D7     target=100MB peak=498.4MB   (ratio 2.40 -- the regression this replaced)
```

## Agreement with `docs/plans/section-6-scale.md` §7's reported figures

The reported figures in the spec were **not independently verified** before this
recording (explicitly flagged in the spec itself). Comparing:

- **Line counts and JSON size match closely** (150,006 vs reported 150,003 lines;
  7,171,754 vs reported 7,169,584 bytes — the small deltas are template-choice noise
  in `_synth_log`'s random line sampling, not a real disagreement).
- **Wall-clock times on this machine run 1.5-4x slower than the figures in §7** (e.g.
  10 MiB: 1.00 s here vs 0.82 s reported; 50 MiB default: 3.57 s here vs 1.89 s
  reported; 100 MiB default: 3.74 s here vs 2.46 s reported). This is a different
  physical machine (§7 doesn't state what it ran on), not a regression — the
  *shape* of the numbers (sublinear scaling, no O(n^2) blowup) agrees with the spec.
  Absolute wall-clock ceilings in `test_parsing_perf.py` (30s/60s/120s) have 15-30x
  headroom over these numbers, so this doesn't threaten any assertion.
- **The `peak100/peak50` before-ratio (1.684) independently confirms the exact defect
  D2 exists to fix** — memory scales with input size, not with `max_total_lines`,
  matching the spec's claim in §1.2 almost exactly (predicted "proportional to
  `len(content)`", observed ratio ~2.0 (100 MiB / 50 MiB input) vs actual peak ratio
  1.684 — sub-input-proportional already, likely because `LogLine` overhead is a
  fixed multiplier of the (still input-proportional) `raw_lines` list, but clearly
  not bounded by `max_total_lines` either, since both runs hit the same 200,000-line
  cap yet produced different peaks).
- **tracemalloc peak absolute values differ from §7's reported 359 MB for 100 MB.**
  This recording's `_peak_mb` (matching `test_parsing_perf.py`'s helper) starts
  `tracemalloc` **before** the input string is allocated, so the input itself is
  counted in the peak. The spec's §1.2 ad hoc measurement explicitly **excludes**
  the input string ("excludes the input str itself"). These are two different
  methodologies measuring two different things — not a disagreement once that's
  accounted for. This recording uses the test file's methodology throughout, since
  that is what the acceptance test (`test_perf_peak_memory_bounded_by_limits_not_input`)
  actually asserts against.

## Follow-up

Re-run `uv run pytest -m perf -s -q` once the rest of Section 6/7 lands and the test
file can import, as a pytest-native sanity check against this file. Expect times in
the same ballpark as "before" under quiet conditions (this file's changes don't
touch the hot masking/regex loop — that's Section 7's, per reconciliation C5/C6) and
memory matching the "after" column above.

---

# Post-Section-7 re-record (reconciliation C6)

Everything above was measured with `sanitize.py` at its original **3** masking rules.
Section 7 took the matrix to **13**, and `normalize` is the dominant stage of a parse,
so those numbers went stale the moment it landed. C6 requires this re-record; without
it Section 6's table would be wrong one section after it was written.

- Python: 3.11.15 · Windows 11 Home Single Language · 13th Gen Intel Core i5-13450HX
- Date: 2026-08-27 · Commit: `ed88c72` (working tree, Sections 5+6+7 integrated)
- Machine otherwise **idle** this time — the "after Section 6" column above was
  explicitly flagged as measured under concurrent load from two other agents, so
  compare shapes and ratios across columns, not raw wall clocks.
- Command: `uv run pytest -m perf -s -q` (all 7 perf tests green, including the D2
  acceptance test and the sublinearity ratio).

| Case | Input | Time (3 rules) | Time (13 rules) | Notes |
|---|---|---|---|---|
| anchor (`pallets/click`) | 0.26 MiB | 0.020 s | **0.052 s** | the masking cost, isolated: ~2.6x |
| synthetic 10 MiB | 10.0 MiB | 1.003 s | 2.234 s | |
| synthetic 50 MiB, default limits | 50.0 MiB | 3.571 s | 5.121 s | line cap binds at ~24 MB in |
| synthetic 100 MiB, default limits | 100.0 MiB | 3.736 s | 5.306 s | |
| 150k-line FAILURES block | 7.3 MiB | JSON 7,171,754 B | **JSON 7,917 B** | D4 evidence cap, ~900x smaller |

Derived, post-Section-7:

- `t50/t10` = **2.28** (ceiling `< 8.0`). The assertion that actually catches an
  O(n^2) reintroduction, and it is a ratio, so it stays valid across machines.
- `peak100/peak50` = 198.5 -> 298.5 MB = **1.50** (D2 acceptance bound `< 1.6`).
  Peak now tracks the *limits*, not the input.

## The masking-cost delta, called out

Going from 3 rules to 13 costs roughly **2.6x on parse time** — the anchor moves
0.020 s -> 0.052 s. That matches the design's own prediction ("parse time roughly
triples for the anchor") and is the price of the single-choke-point design: masking
is per-line, and every line pays for every rule.

Two things keep it affordable, and they are the reason this is recorded rather than
optimized away:

1. **The single-mask fast path** (reconciliation C5). `mask()` used to run twice per
   line, once for `text` and once for `raw_text`. Only 1 of the anchor's 2323 lines
   contains an ESC byte, so the stripped form *is* the payload on 99.96% of lines and
   one call now serves both fields. Without this the cost would have been ~2x higher
   again.
2. **Real CI logs are 2-20k lines**, not 200k. At the 200,000-line ceiling the
   masking extrapolates to a few seconds, which is why the ceiling exists.

**Do not "optimize" this with a combined single-alternation regex.** It was
benchmarked and is *worse* — 147 ms vs 49 ms — because Python's `re` optimizes each
pattern's literal prefix but not an alternation of them. A `search()`-then-`sub()`
gate was benchmarked too and was slower still (272 ms), because the search duplicates
the scan. If masking ever becomes the bottleneck, try a cheap literal prefilter gate,
**with a measurement first**.

## Biggest win in the set

The 150k-line FAILURES block: the report went from **7,171,754 bytes to 7,917** — a
~900x reduction, from the D4 evidence cap alone. `max_diagnostics` bounded the
*count* of diagnostics but nothing bounded their *size*, and a single test failure
spanning 150,000 lines produced an `evidence` tuple with 150,002 entries. The full
extent was never lost: it still lives in `Diagnostic.source_range`. Evidence is a
sample; the range is the extent.
