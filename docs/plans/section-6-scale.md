# Section 6 — Scale & Robustness

> Part of the Slice 2 engine plan. Shared constraints and cross-section
> reconciliations: [`README.md`](README.md). Sequenced **after** Section 5.
>
> **Removed from this section by reconciliation:** the O(n²) segmentation fix and the
> decode-symmetry fix moved into the Step 0 hotfix; the ANSI/mask fast path moved to
> Section 7 ([C5](README.md#c5--the-ansimask-fast-path-is-one-change-claimed-twice)); the
> `_build_clusters` index-math micro-optimization is declined
> ([C4](README.md#c4--_build_clusters-ownership--micro-optimization-declined)).

---

## 1. Current-state audit

### 1.1 Where full scans happen

Each is one complete traversal of the `list[LogLine]`:

| # | Stage | Citation | Notes |
|---|---|---|---|
| 0 | `detect_provider(content[:4000])` | `pipeline.py:73` | bounded to 4000 chars / 50 lines. Not a full scan. |
| 1 | `normalize` | `normalizer.py:35-55` | ~10 regex applications per line |
| 2 | `build_sections` | `segmentation.py:23-54` | **fixed in the Step 0 hotfix** |
| 3 | `PytestParser.detect` | `pytest_parser.py:76-86` | early-exits on first hit; full scan when negative |
| 4-7 | `PytestParser.parse` | `pytest_parser.py:89, 90, 96, 97` | 2 full + 2 tail passes |
| 8 | `find_process_failure` | `process_failure.py:20` | once per parser that fires |
| 9-11 | `JsTestParser.detect` | `js_test_parser.py:41, 43, 45` | **three separate `any(...)` scans**, each a full pass on a negative |
| 12 | `CompilerParser.detect` | `compiler_parser.py:50-58` | full pass on a negative |
| 13 | `_build_clusters` | `pipeline.py:151` | builds an n-entry dict keyed by `raw_lineno` |

**Constant factor: ~10 full passes typical, ~15 worst case.** Reported stage split on a
10.5 MB / 88,274-line input:

```
normalize        0.639 s   (77% of runtime)
build_sections   0.023 s
pytest.parse     0.105 s
js_test.detect   0.009 s
compiler.detect  0.024 s
lines_by_no      0.004 s
```

`normalize` dominates. The 10 detect/parse passes together are ~20% — **do not
micro-optimize them**; that is where this section would be over-built.

> **Re-measure before trusting.** These figures and every number in §1.3/§1.4 come from
> the design pass and were **not** independently re-verified. Step 2 of §8 re-measures
> them before they are written to `docs/perf-baselines.md`. The two numbers that *were*
> independently confirmed are the segmentation timings (now fixed in Step 0) and the dead
> `max_context_lines` config.

**Current big-O after the Step 0 hotfix:** `O(n·k)` with `k` ≈ 10–15 ⇒ **O(n)**. This
section's job is bounding memory, not lowering `k`.

### 1.2 What peak memory is proportional to

`normalizer.py:28-31` splits the **entire** string before applying `max_total_lines`:

```python
raw_lines = content.splitlines()          # full input materialized
truncated_lines = len(raw_lines) > limits.max_total_lines
if truncated_lines:
    raw_lines = raw_lines[: limits.max_total_lines]   # ...then most of it is thrown away
```

Reported on a 105.2 MB input **under default limits** (which cap output at 200,000 lines):

```
parse_log(105 MB, default limits): 2.46 s, traced peak 359 MB  (excludes the input str itself)
  of which: content.splitlines() alone -> 0.64 s, +147 MB transient
```

**`max_total_lines` does not bound memory today.** Peak is proportional to
`len(content)`, not to the limit.

Note `text is raw_text` is already true for 2322/2323 anchor lines — `re.sub` returns the
identical object on no-match — so the two string fields already share storage. There is no
memory win available there; only a speed win.

Two further unbounded quantities:

- **`evidence` tuples.** `pytest_parser.py:235` does
  `set(range(chunk[0].raw_lineno, chunk[-1].raw_lineno + 1))`. A synthetic 150,000-line
  `FAILURES` block produced `len(evidence) == 150_002` and a **7.2 MB JSON report** from a
  ~6 MB log. `max_diagnostics` bounds the *count* of diagnostics, nothing bounds their
  *size*.
- **`max_context_lines`** (`limits.py:15`) is declared and **read by zero call sites**
  (independently confirmed). It is dead config — and it is precisely the bound the
  evidence tuples are missing.

### 1.3 How truncation behaves today

Head-only, at `normalizer.py:29-31`. `report.truncated` (`pipeline.py:87`) ORs three
conditions. Reported degradation on the anchor:

```
cap=2290 (mid-FAILURES-block): diags=1 exit=None trunc=True file=tests\test_types.py
cap=2300                     : diags=1 exit=None trunc=True
cap=2310                     : diags=2 exit=1    trunc=True
cap=2323 (whole log)         : diags=2 exit=1    trunc=False
```

Good news: **mid-traceback truncation already degrades gracefully.** `_find_blocks` closes
the open block at EOF (`pytest_parser.py:140-141`), `parse_python_traceback` returns
whatever frames exist, and `message` falls back from `short_summary` to `stack.message`
(`pytest_parser.py:222-224`). Section 6 must **test and document** this, not build it.

Bad news: **head truncation loses `exit_code`.** CI's highest-value artifacts
(`##[error]...exit code N`, pytest's `short test summary info`, jest's `Tests:` footer)
are all at the *tail*, which head truncation is guaranteed to drop.

### 1.4 Line-boundary semantics — the fact that decides the streaming question

`splitlines()` splits on **9** boundaries (`\r\n`, `\n`, `\r`, `\v`, `\f`, `\x1c`, `\x1d`,
`\x1e`, `\x85`, ` `, ` `). File/stream iteration splits on `\n` only:

```python
s = 'a\x0bb\x0cc\x1cd\x85e f\ng'
s.splitlines()        -> 7 lines
list(io.StringIO(s))  -> 2 lines
bytes(range(256)).decode('latin-1'): splitlines -> 9 lines, but only 1 '\n'
```

The anchor fixture happens to be clean (2323 `splitlines` == 2323 `\n`), but **any
streaming path fed by line iteration produces different line numbers than `parse_log` on
binary or exotic input.** That is a determinism break, and it directly contradicts the
load-bearing decision documented at `normalizer.py:1-7`.

### 1.5 Factual correction to a model docstring

`model.py:5-8` states the anchor fixture "contains a lone Unicode surrogate (`\udcff`)".
It does not — line 2283 contains the six ASCII characters `\udcff`, Python's *repr* of the
surrogate inside a `FileNotFoundError` message. The `ensure_ascii=True` decision
(`model.py:210`) is still correct and still needed (`test_parsing_fuzz_security.py:57`
exercises a genuine surrogate), but the docstring cites evidence that isn't there. Fix the
comment.

---

## 2. Design decisions

### D1 — Streaming: **no.** Bounded in-memory, with a named upgrade path.

Do not add `parse_log_stream` / chunked state machines. Keep the whole-log-in-memory
pipeline and make its bounds real.

**Alternatives considered:** (a) true streaming — incremental normalize → segment → parse
over a line iterator; (b) chunked windows with diagnostic merging; (c) an additive
`parse_log_lines(Iterable[str])` seam now.

**Why bounded in-memory:** the parsers make (a) and (b) impossible without the core
rewrite the architecture forbids (`CLAUDE.md` §4). Read from the code:

| Parser | Access pattern | Streamable? |
|---|---|---|
| `PytestParser` | `lines[short_summary_start:]` (`:95`), `lines[start:end]` (`:101`) — arbitrary back-slicing; `_find_blocks` needs the *whole* list before any chunk is emitted | **No** |
| `JsTestParser` | index-based `while` with `_collect_block` lookahead (`:59-74`, `:88-98`); `_detect_tool` at `:53` needs a full prior pass | **No** |
| `CompilerParser` | forward-only, but `_extract_eslint` carries `current_file` state and runs a second independent pass | Partially |
| `GenericParser` | forward-only | Yes |
| `find_process_failure` | full scan, keeps `matches[-1]` (`process_failure.py:30`) — needs EOF | **No** |
| `_build_clusters` | random access by `raw_lineno` (`pipeline.py:151`) | **No** |

Two of four parsers plus the shared exit-code extractor and cluster builder all require the
complete list. Streaming would rewrite every parser plus the registry contract, to solve a
problem that does not exist: the practical ceiling for a GitHub Actions *job* log is
single-digit MB (the anchor is 0.28 MB), and a 105 MB log already parses in ~2.5 s under
default limits. Additionally §1.4: any stream fed by line iteration cannot reproduce
`splitlines()` boundaries, so a streaming path would be **non-deterministic relative to
`parse_log`** on exactly the degenerate inputs it was supposed to handle.

**Why not (c):** nothing would call it. `load_raw_log` buffers (`models.py:81`),
`GitHubClient.get_job_log` buffers (`client.py:103`). An entry point with zero callers is
speculative API surface.

**Named upgrade path** (write it down, don't build it): the first time a caller genuinely
streams — i.e. `GitHubClient` switches to `resp.iter_lines()` — add

```python
def parse_log_lines(lines: Iterable[str], source=None, limits=ParseLimits()) -> FailureReport:
    """Additive seam for callers that already own the line split.
    Byte-identical to parse_log only if the caller reproduces str.splitlines()
    boundaries (see normalizer._LINE_BREAK_RE); otherwise line numbers differ.
    """
```

as a ~10-line wrapper around a refactored `_normalize_lines(raw_lines, provider, limits)`.
`parse_log` keeps working unchanged because it becomes
`parse_log_lines(_bounded_splitlines(content, limits), ...)`.

**Cost:** memory stays proportional to `min(len(content), max_total_chars)` rather than
O(1). Accepted, and now *actually* bounded (D2).

### D2 — Bound the split before it happens (`_bounded_splitlines`)

Cap by characters first, then split lazily to `max_total_lines` using a regex that
reproduces `splitlines()`'s exact boundary set.

```python
_LINE_BREAK_RE = re.compile("\r\n|[\n\r\v\f\x1c\x1d\x1e\x85  ]")

def _bounded_splitlines(content: str, cap: int) -> tuple[list[str], bool]:
    """str.splitlines(), but stops after `cap` lines without materializing the rest.

    The regex must stay byte-identical to splitlines()'s boundary set: `\r\n`
    is listed first so CRLF is one break, not two. ponytail: slices a prefix
    copy (~cap*avg_len extra bytes); build lines from finditer spans directly
    if that ever shows up in a profile.
    """
    end = None
    for i, m in enumerate(_LINE_BREAK_RE.finditer(content), 1):
        if i == cap:
            end = m.end()
            break
    if end is None:
        return content.splitlines(), False
    return content[:end].splitlines(), True
```

**Alternatives rejected:** `content.split("\n")` — breaks the documented
`normalizer.py:1-7` invariant and the 2323-line anchor number; `io.StringIO` iteration —
wrong boundaries (§1.4); leave as-is — §1.2.

Reported as byte-identical to `splitlines()[:cap]` over 100,000 randomized degenerate
cases (0 mismatches), plus the real anchor at cap 3 and cap 200,000. `finditer` is lazy,
so scan cost is O(prefix), not O(input). Reported on 105 MB:

```
content.splitlines()          0.64 s, +147 MB transient, 882,740 lines
_bounded_splitlines(200_000)  0.48 s,  +81 MB transient, 200,000 lines
```

**Cost:** ~15 lines and a second definition of the boundary set that must not drift from
CPython's. Guarded by the differential property test T-P1, which compares against the real
`str.splitlines()` so a CPython change fails the test rather than the product.

### D3 — `LogLine` gets `slots=True`

`@dataclass(frozen=True, slots=True)` on `LogLine` only (`model.py:72`).

`frozen=True, slots=True` is legal on Python ≥3.11 and `dataclasses.replace` still works
(`segmentation.py:39,46,52` depends on it). `dataclasses.fields` — used by
`model._serialize` — works with slots. No `__dict__`, `asdict`, or `weakref` usage against
`LogLine` anywhere in `src/` or `tests/`; the only construction site is `normalizer.py:46`.

Reported at the 200,000-line ceiling: **27.2 MB → 19.2 MB** (−29% of LogLine object
overhead, −8 MB of a 73 MB total).

**Alternatives rejected:** slots on every frozen dataclass — `Diagnostic`/`StackFrame`/
`LogSection` are bounded by `max_diagnostics`/traceback size; no measurable win, more blast
radius. `NamedTuple` — would change `dataclasses.replace` and `_serialize` behaviour.

**Cost:** one word. `LogLine` never appears in the serialized `FailureReport`, so zero
schema impact.

### D4 — Wire `max_context_lines` into an evidence cap, centrally

Cap `Diagnostic.evidence` to `limits.max_context_lines` entries **in `pipeline.py`**, not
in each parser.

**Why central:** three parsers build evidence today and future ecosystem parsers will too;
a cap in each is three places to forget in parser #5. The registry return at
`pipeline.py:77` is the single choke point all diagnostics route through. Same rule as
`process_failure.py`'s extraction — fix it once where everything passes.

**Why capping is safe:** `source_range` already carries the authoritative full span (2
ints), so nothing is lost — evidence is a *sample*, the range is the *extent*. The default
`max_context_lines=50` is a no-op on the anchor (evidence lengths are 25 and 1), so the
golden snapshot's diagnostics are unchanged by this alone.

> **[C3](README.md#c3--the-evidence-cap-must-be-head-first): the cap keeps the FIRST N
> entries.** Section 5's ranking uses `min(d.evidence)` and derives `section_id` from
> `evidence[0]`. A tail-biased or sampled cap would silently change clustering. Pinned by
> `test_evidence_cap_preserves_first_entry`.

**Alternatives rejected:** head+tail sampling of evidence — YAGNI, and `source_range`
already gives the extent. Deleting `max_context_lines` — the 150,002-entry / 7.2 MB
measurement is a real hole, and the field exists precisely for this.

**Cost:** ~6 lines in `pipeline.py` plus a `stats["evidence_truncated"]` flag.

### D5 — Truncation policy: head-only, plus a bounded tail rescue for the exit code

Keep head truncation as the line-materialization policy. When line truncation occurs *and*
no `PROCESS_FAILURE` was found in the head, normalize the last 64,000 characters of the
original `content` and run the existing `find_process_failure` over it. Merge the single
recovered diagnostic with `evidence=()`, `source_range=None`,
`metadata["from_truncated_tail"] = True`.

**Alternatives rejected:**

- *Head-only, unchanged* — measurably loses `exit_code` (§1.3).
- *Full head+tail with a gap sentinel line* — **every parser assumes list adjacency implies
  log adjacency.** `pytest._split_failure_chunks` (`:177-189`) would fold a head FAILURES
  block straight into the tail's summary, and `evidence_lines = set(range(first, last+1))`
  (`:235`) would then enumerate the entire omitted gap as evidence for lines that were
  never parsed. That is silent corruption across four parsers, to fix a case no fixture
  exercises.
- *Per-section truncation* — requires segmentation before truncation, inverting the
  pipeline order; and CI's most-truncated section is usually the one containing the
  failure.

**Why the rescue is safe:** it touches **zero parsers**, reuses the existing shared
extractor, and preserves line-number fidelity exactly — because it makes *no line claims at
all*. A mid-line slice at `content[-64_000:]` cannot forge a `##[error]` marker (the marker
regex is anchored at payload start, `github_actions.py:37`), so the worst case is that it
finds nothing.

**Cost:** ~12 lines in `pipeline.py`, one extra bounded `normalize` call on truncated logs
only. The rescued diagnostic deliberately has no `source_range`, which a naive consumer
must tolerate — already possible (`_fatal_report` at `pipeline.py:45` emits
`source_range=None`).

**Upgrade path (named, not built):** if a future ecosystem's *primary* evidence proves to
live only in the tail, promote to full head+tail with a gap sentinel — and re-verify all
parsers' adjacency assumptions first.

### D6 — `SourceRange` byte offsets: **declined. Close the deferral.**

Do not add byte offsets. Replace the deferral comment at `model.py:95-96` with a decision
comment. Line numbers are the traceability strategy.

**Accuracy.** There are **three** distinct coordinate systems, and byte offsets exist only
in the first:

1. **Raw file bytes** — what `save_raw_log` preserves (`models.py:66`, `newline=""`).
2. **Payload chars** — after `split_line` strips the timestamp prefix
   (`normalizer.py:40`); a different origin per line.
3. **Masked text chars** — after `mask()` rewrites secrets to `«REDACTED:...»`
   (`normalizer.py:42-43`), which *changes length*.

Every diagnostic's `message`, `file` and `evidence` derive from (3). A byte offset is only
meaningful against (1). Storing offsets means storing a coordinate no other field agrees
with — an invitation to a subtly-wrong "jump to byte 41,203" in a future UI.

**Feasibility.** `splitlines()` **discards which boundary was used**, and the 9 boundaries
encode to 1–3 UTF-8 bytes each. Byte offsets are therefore *not derivable* from the current
split output. Computing them requires a boundary-preserving split — abandoning
`splitlines()`, the explicitly documented decision the anchor fixture's verified
2323/ESC@226/FAILURES@2276 numbers rest on. Section 6 would be risking the project's only
real anchor to add a redundant coordinate.

**Memory.** 2 extra ints on every `SourceRange`, on every `Diagnostic`, on every report —
paid on 100% of reports for a capability used by 0% of them.

**Debugging usefulness.** The only operation byte offsets enable that line numbers don't is
O(1) `file.seek()`. Every current and planned consumer — golden snapshots, `evidence`
tuples, `StackFrame.raw_lineno`, `LogSection` bounds, the Slice 3 diff correlator (which
correlates against line-based diff hunks) — is already line-keyed and already holds the
content in memory, where `lines[n-1]` is O(1) anyway.

**Named upgrade path:** if a future surface genuinely needs seek-into-a-100 MB-log-without-
loading-it, the answer is a **separate on-demand side table** —
`def line_byte_offsets(content: str) -> tuple[int, ...]`, computed once, O(n) ints total —
not 2 ints duplicated into every `SourceRange`. Write that in the comment.

### D7 — Add `max_total_chars`

New `ParseLimits` field, applied before the line split.

**Why it's not YAGNI:** `max_total_lines` cannot bound a log of *few, enormous* lines — a
500 MB single-line log never reaches 200,000 lines, so `_bounded_splitlines` falls through
to `content.splitlines()` on the full input. A runaway `set -x` loop producing a
half-gigabyte log is an ordinary CI occurrence, and today it OOMs before any limit applies.
Together:

```
peak ≈ O(min(len(content), max_total_chars, prefix_through(max_total_lines)))
```

**Default `100_000_000`:** a 105 MB input reportedly parses in ~2.5 s under default limits,
so a 100 MB ceiling is a genuine safety valve that never shapes normal output (the anchor
is 0.28 MB; real job logs are single-digit MB).

### D8 — What NOT to optimize

Explicitly declined, with the trigger for revisiting:

- **`JsTestParser.detect`'s three scans** (`js_test_parser.py:41,43,45`) — 0.009 s of a
  0.82 s parse (1.1%). Revisit only if a profile shows registry `detect` >20% of runtime.
- **Merging `CompilerParser`'s two extractors** (`compiler_parser.py:61`) — 0.024 s /
  2.9%. Merging couples tsc and eslint state machines for no measurable gain.
- **Regex hardening / ReDoS.** All 28 compiled patterns in `parsing/` were fuzzed against
  400 adversarial strings each. Worst case: `_BANNER_LINE_RE` at 1.72 ms on a 20,000-char
  line — linear-ish, no catastrophic backtracking. `max_line_length=20_000` plus
  `max_total_chars` bound the total. Record the fuzz as a test (T-P4) so a future regex
  can't regress this silently.
- **`_build_clusters`' n-entry dict** — 0.004 s. **Declined outright** per
  [C4](README.md#c4--_build_clusters-ownership--micro-optimization-declined): Section 5
  has already moved this code to `clustering.py`.

---

## 3. Contract changes

All additive. **`SCHEMA_VERSION` stays at Section 5's `"1.2"`** — no bump.

**Justification:** the version tracks the *shape* of the serialized model — dataclass
fields and enum members. Section 6 adds **no field and no enum member** to any serialized
type. New keys land inside `stats` and `metadata`, both already declared free-form `dict`
(`model.py:134`, `:190`). This is exactly the precedent Section 4 set and recorded in
`CLAUDE.md` §2. Bumping now would make the version mean two different things.

### `limits.py` — new field

```python
@dataclass(frozen=True)
class ParseLimits:
    """Bounds threaded through every pipeline stage. Exceeding a bound

    truncates and records it in stats/truncated -- it never raises. Generous
    defaults: real CI logs stay well under these in practice.

    max_total_chars is applied *before* the line split, so peak memory is
    bounded by min(len(content), max_total_chars) rather than by the input.
    max_total_lines alone cannot bound a log of few, enormous lines.
    Default 100 MB is a safety valve, not a shaping limit: a measured 105 MB
    log parses in ~2.5s (see docs/perf-baselines.md); real job logs are <10 MB.
    """

    max_total_chars: int = 100_000_000
    max_total_lines: int = 200_000
    max_line_length: int = 20_000
    max_diagnostics: int = 500
    # Caps Diagnostic.evidence length, keeping the FIRST N entries (Section 5
    # ranks on min(evidence) and derives section_id from evidence[0]). The full
    # extent stays in Diagnostic.source_range, so capping samples evidence,
    # never loses it.
    max_context_lines: int = 50
```

All four existing `ParseLimits(...)` call sites use keywords, so field order is free;
`max_total_chars` goes first for readability. Append it last instead if a reviewer prefers
zero positional risk — either is additive.

### `model.py`

```python
# `slots=True`: LogLine is the only type materialized per log line, so it is
# the only one where the per-instance __dict__ is worth removing. Measured
# 27.2 MB -> 19.2 MB at the 200k-line ceiling. Compatible with frozen=True
# and with dataclasses.replace (segmentation.py relies on both); LogLine is
# never serialized into FailureReport, so this has no schema impact.
@dataclass(frozen=True, slots=True)
class LogLine:
    ...
```

```python
@dataclass(frozen=True)
class SourceRange:
    # 1-based inclusive raw line numbers. Byte offsets were deferred to
    # Section 6 and are DECLINED there, deliberately:
    #
    #  * splitlines() discards which of its 9 boundaries was used, and they
    #    encode to 1-3 UTF-8 bytes each, so byte offsets are not derivable
    #    from the current split -- computing them means abandoning
    #    splitlines(), the decision the anchor fixture's line numbers rest on
    #    (see normalizer.py's module docstring).
    #  * There are three coordinate systems here: raw file bytes, timestamp-
    #    stripped payload chars, and masked text chars (mask() changes
    #    length). Every other field in this model lives in the third. A byte
    #    offset would be the only field in the first.
    #  * Upgrade path if a surface ever needs seek-without-load: a separate
    #    on-demand line_byte_offsets(content) side table, not 2 ints on every
    #    SourceRange.
    start: int
    end: int
```

Also fix the surrogate claim in `model.py`'s module docstring (§1.5).

### New `stats` keys

`truncated_chars` (bool), `evidence_truncated` (bool), `tail_rescued` (bool).

---

## 4. Files & modules

**No new source modules.** Section 6 is edits to the existing seams — that is the design,
not a shortcut. A `streaming.py` or `bounds.py` would be an abstraction with one
implementation.

| File | Change | Why this boundary |
|---|---|---|
| `parsing/normalizer.py` | + `_LINE_BREAK_RE`, `_bounded_splitlines`; char cap; extract `_normalize_lines(raw_lines, ...)` | line materialization already lives here and its docstring already owns the `splitlines` decision |
| `parsing/limits.py` | + `max_total_chars`, document `max_context_lines` | the declared home of every bound |
| `parsing/pipeline.py` | + `_cap_evidence`, + `_rescue_tail_exit_code`, 3 stats keys | the choke point all diagnostics route through — one cap, every parser, including future ones |
| `parsing/model.py` | `slots=True` on `LogLine`; `SourceRange` decision comment; surrogate-claim fix | model-level facts belong with the model |
| `docs/perf-baselines.md` | **new** — recorded baselines | numbers must live in a diffable file, not in a test assertion |
| `tests/test_parsing_scale.py` | **new** — unit + fixture + property, all fast/offline | runs by default |
| `tests/test_parsing_perf.py` | **new** — `@pytest.mark.perf`, excluded by default | isolated so a slow file can never leak into `uv run pytest` |
| `pyproject.toml` | + `perf` marker; `addopts = "-m 'not integration and not perf'"` | existing convention (§7), extended by one term |
| `CLAUDE.md` §2 / §8 | tick Section 6 with the streaming-declined note; remove "streaming/perf" from deferrals | standing rule — same commit |
| `docs/project-brief.md` | record the streaming-declined and byte-offsets-declined decisions | §6 rule: "don't silently change the plan" |

---

## 5. Fixtures

**Zero new committed fixtures. Zero large files in git.**

| Input | Where it comes from | Rationale |
|---|---|---|
| Truncated mid-traceback | **reuse the real anchor** with `ParseLimits(max_total_lines=2290)` | lands mid-`FAILURES` block. A committed truncated copy would be a second thing to keep in sync |
| Truncated past the summary | anchor at caps 2200 / 2300 / 2310 | same |
| Empty / whitespace-only / newlines-only | inline string literals | already the pattern in `test_parsing_fuzz_security.py:63` |
| Enormous single line | `"x" * 5_000_000` inline | already at `test_parsing_fuzz_security.py:69` |
| Binary bytes | `bytes(range(256)).decode("latin-1")` inline | already at `:51`. Yields **9** `splitlines` lines from **1** newline — assert that explicitly |
| Lone surrogate | `"bad \udcff line"` inline | already at `:57` |
| Exotic line breaks (`\v \f \x1c \x85  `) | inline | new — the boundary set separating `splitlines` from stream iteration (§1.4) |
| Giant `FAILURES` block (150k lines) | generated from a template | the D4 evidence-cap guard |
| **10 / 50 / 100 MB logs** | **generated at test time** from the committed 0.28 MB anchor plus `random.Random(1337)` | **never committed.** Multi-MB fixtures would balloon clone size and add nothing the generator doesn't. Seeded, so a baseline is reproducible |

```python
def _synth_log(target_bytes: int, seed: int = 1337) -> str:
    """Deterministic ~target_bytes GitHub-Actions-shaped log, generated not committed.

    Seeded so a recorded baseline is reproducible. Built from the real anchor
    so line shapes (timestamps, ##[group], pytest blocks) are realistic rather
    than a uniform synthetic that would flatter the regex costs.
    """
    rng = random.Random(seed)
    template = ANCHOR_PATH.read_bytes().decode("utf-8", errors="replace").splitlines()
    out, size = [], 0
    while size < target_bytes:
        line = rng.choice(template)
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)
```

---

## 6. Tests (written first)

```toml
markers = [
  "integration: hits the real GitHub API over the network; excluded by default.",
  "perf: generates multi-MB logs and measures time/memory; slow, excluded by default.",
]
addopts = "-m 'not integration and not perf'"
```

### `tests/test_parsing_scale.py` (fast, offline, runs by default)

**Bounded split (D2)**
- `test_bounded_splitlines_matches_splitlines_when_under_cap`
- `test_bounded_splitlines_stops_at_cap_without_materializing_rest`
- `test_bounded_splitlines_treats_crlf_as_one_break` — `"a\r\nb"` → `["a","b"]`
- `test_bounded_splitlines_honours_exotic_breaks`
- `test_max_total_chars_truncates_and_flags`
- `test_max_total_chars_default_does_not_truncate_anchor`

**Evidence cap (D4)**
- `test_evidence_capped_to_max_context_lines`
- `test_evidence_cap_preserves_first_entry` — **[C3](README.md#c3--the-evidence-cap-must-be-head-first) guard**: the cap keeps the head, so `min(evidence)` and `evidence[0]` are unchanged
- `test_source_range_survives_evidence_cap` — the extent is not lost when the sample is capped
- `test_default_evidence_cap_is_noop_on_anchor` — evidence lengths still `(25, 1)`

**Tail rescue (D5)**
- `test_head_truncation_still_recovers_exit_code_from_tail` — anchor at 2290 → `exit_code == 1`, `stats["tail_rescued"] is True`
- `test_tail_rescued_diagnostic_makes_no_line_claims` — `evidence == ()`, `source_range is None`, `metadata["from_truncated_tail"] is True`. **Guards line-number fidelity.**
- `test_no_tail_rescue_when_not_truncated`
- `test_no_duplicate_process_failure_when_head_already_has_one`

**Degradation ladder (§1.3)**
- `test_truncated_mid_traceback_still_locates_the_file`
- `test_truncated_mid_traceback_degrades_message_not_crashes` — documents the accuracy loss rather than pretending it doesn't happen
- `test_binary_bytes_produce_splitlines_boundaries_not_newline_boundaries` — `report.raw_line_count == 9`. Locks §1.4 so a future streaming attempt fails loudly
- `test_parse_log_total_on_exotic_line_breaks`, `..._on_whitespace_only`, `..._on_newlines_only`

**Golden / determinism**
- `T-G1 test_bounded_split_path_matches_reference_splitlines_on_anchor`
- `T-G2 test_golden_snapshot_diagnostics_subtree_unchanged_by_section6` — `json.loads(to_json(report))["diagnostics"]` equals the committed value, likewise `sections`, `clusters`, `exit_code`, `raw_line_count`. Only `stats` may differ. **This is what stops regeneration from hiding a regression.**
- existing `test_golden_snapshot_matches_committed_json` continues to assert full equality against the regenerated file

**Property-based (stdlib `random`, fixed seed — no hypothesis)**

> **Why not hypothesis.** `CLAUDE.md` §6: prefer the stdlib, keep the dependency surface
> small. The properties here are **differential** (new impl ≡ old impl) over a **tiny
> alphabet** — 15 characters for the splitter. Seeded random over that space is not a
> weaker approximation of hypothesis; it is near-exhaustive (0 mismatches in 100,000
> cases, under a second). Hypothesis's real value is shrinking a failure to a minimal
> example — worth a dependency when properties are complex or stateful. These are neither.
> **Revisit** if a later section needs stateful or multi-argument properties; record that
> trigger in the test docstring so the decision is re-openable rather than folklore.

- `T-P1 test_bounded_splitlines_equals_splitlines_over_random_break_soup` — `random.Random(20260827)`, 20,000 strings of length 0–25 over `['a','b',' ','\n','\r','\r\n','\v','\f','\x1c','\x1d','\x1e','\x85',' ',' ','']`, each at caps `(1, 2, 3, 5, 100)`. Asserts equality with `s.splitlines()[:cap]`. **100,000 assertions, ~0.6 s.**
- `T-P2 test_parse_log_is_total_over_random_byte_soup` — 2,000 strings of random codepoints including surrogates, control chars, BOMs. Asserts `parse_log(s)` returns and `json.loads(to_json(...))` succeeds.
- `T-P3 test_no_parsing_regex_is_superlinear` — every `re.Pattern` reachable from `parsing.*` module globals (28 today, auto-discovered so new parsers are covered for free), matched against seeded adversarial strings at 2k / 8k / 20k; asserts each `.match()` + `.search()` pair completes in **< 50 ms** (measured worst today 1.72 ms — 29× headroom, so it catches catastrophic backtracking without being CI-flaky).

### `tests/test_parsing_perf.py` (`@pytest.mark.perf`, excluded by default)

**Time and memory are measured in separate runs** — `tracemalloc` inflates the same 10 MB
parse from 0.82 s to 2.13 s (2.6×). Measuring both at once produces a meaningless number;
this caveat must be in the test docstring.

- `test_perf_10mb_completes_within_generous_ceiling` — `min` of 3 runs < **30 s** (baseline ~0.82 s; a 1000×-regression tripwire, not a benchmark)
- `test_perf_50mb_completes_within_generous_ceiling` — < **60 s**
- `test_perf_100mb_completes_within_generous_ceiling` — < **120 s**
- `test_perf_scales_sublinearly_in_time` — `t(50MB) / t(10MB) < 8`. **Machine-independent** — a ratio, not a wall clock. The assertion that actually catches an O(n²) reintroduction
- `test_perf_peak_memory_bounded_by_limits_not_input` — `tracemalloc` peak for 100 MB vs 50 MB under identical default limits; asserts `peak(100MB) < 1.6 * peak(50MB)`. Today this **fails**; after D2+D7 it passes. **This is the D2 acceptance test**
- `test_perf_report_size_bounded_by_max_context_lines` — 150,000-line FAILURES block → `len(to_json(report)) < 2_000_000` (today 7,169,584)

---

## 7. Performance baselines

### Procedure (documented at the top of `test_parsing_perf.py`)

```bash
uv run pytest -m perf -s -q
```

1. Generate input via `_synth_log(target_bytes, seed=1337)` — deterministic, never committed.
2. **Time**: 1 warm-up run (imports, regex compilation, page-in), then `min()` of 3
   `time.perf_counter()` runs. `min`, not mean — least polluted by scheduler noise.
   `tracemalloc` **off**.
3. **Memory**: a separate run with `tracemalloc.start()` before the input string is
   allocated (so the input is counted, not silently excluded), recording
   `get_traced_memory()[1]`.
4. Print a machine-readable line: `PERF <case> <input_mb> <seconds> <peak_mb>`.

### What gets recorded — `docs/perf-baselines.md`

A committed table, per row: case, input size, lines parsed, seconds, tracemalloc peak MB,
plus a header block with **Python version, OS, CPU, date, and commit SHA**. Regenerated by
re-running the command and pasting; never auto-written by a test.

**Numbers are recorded, not asserted.** Assertions use generous absolute ceilings (30–40×
headroom) and machine-independent **ratios**. A tight wall-clock assertion would make CI
flaky on a different machine, and the first response to a flaky perf test is always to
delete it.

### Reported "before" figures — re-measure in step 2 before committing

| Case | Input | Lines parsed | Time | tracemalloc peak |
|---|---|---|---|---|
| anchor (`pallets/click`) | 0.28 MB | 2,323 | 21.4 ms | — |
| synthetic 10 MB | 10.5 MB | 88,274 | 0.82 s | ~68 MB |
| synthetic 50 MB, default limits | 52.6 MB | 200,000 (truncated) | 1.89 s | — |
| synthetic 50 MB, limits lifted | 52.6 MB | 441,370 | 4.12 s | — |
| synthetic 100 MB, default limits | 105.2 MB | 200,000 (truncated) | 2.46 s | **359 MB** (excl. input) |
| 150k-line FAILURES block | ~6 MB | 150,003 | 0.87 s | — (7.2 MB JSON out) |

> Also record an "after" column, and see
> [C6](README.md#c6--section-7-invalidates-section-6s-perf-baselines): **Section 7 must
> re-record this table**, because it takes masking from 3 rules to 13 and `normalize` is
> ~77% of runtime.

---

## 8. Implementation steps (ordered TDD checklist)

Each step is red → green → next. Do not batch.

1. **Scaffold.** Add the `perf` marker + `addopts` to `pyproject.toml`. Create
   `tests/test_parsing_scale.py` and `tests/test_parsing_perf.py` with `_synth_log` and all
   tests from §6 — all failing or erroring. Confirm `uv run pytest` picks up only the fast
   ones and `uv run pytest -m perf` only the slow ones.
2. **Record the "before" baseline.** Run `uv run pytest -m perf -s` against current code
   and write `docs/perf-baselines.md`. **This must happen before any fix**, or there is
   nothing to regress against. This is also where §1's reported figures get independently
   confirmed or corrected.
3. **D2 + D7 — bounded split and `max_total_chars`.** Add `_LINE_BREAK_RE`,
   `_bounded_splitlines`, the char cap, `stats["truncated_chars"]`. Green: all
   bounded-split unit tests, T-P1, T-G1, `test_max_total_chars_*`,
   `test_perf_peak_memory_bounded_by_limits_not_input`. **First `stats` change** →
   regenerate the golden snapshot here, and verify T-G2 plus
   `test_key_facts_about_the_real_failure` both pass.
4. **D3 — `LogLine` slots.** One word. Green: everything still green. Isolated with no
   other change, so a break is unambiguously attributable.
5. **D4 — evidence cap.** Add `_cap_evidence` in `pipeline.py` and
   `stats["evidence_truncated"]`. Green: evidence-cap tests including
   `test_evidence_cap_preserves_first_entry`, and
   `test_perf_report_size_bounded_by_max_context_lines`. Golden regenerated; T-G2 must
   still hold — the anchor's evidence is 25 and 1, both under 50.
6. **D5 — tail rescue.** Add `_rescue_tail_exit_code` and `stats["tail_rescued"]`. Green:
   all four tail-rescue tests plus the two mid-traceback degradation tests. Golden
   regenerated; `stats["tail_rescued"] is False` on the untruncated anchor.
7. **D6 — close the byte-offset deferral.** Comment-only change to `model.py`, plus the
   surrogate-claim correction (§1.5). No test.
8. **Re-record the baseline.** Re-run `-m perf`, add the "after" column.
9. **Docs.** `CLAUDE.md` §2 → Section 6 `[x]` with the streaming-declined note; §8 remove
   "streaming/perf". `docs/project-brief.md` gains the D1 and D6 decisions. **Same commit
   as the code.**

---

## 9. Acceptance criteria

1. `uv run pytest` passes, is fully offline, and takes **no longer than today** (perf tests
   excluded by the marker).
2. `uv run pytest -m perf` passes and prints `PERF` lines for all six cases.
3. `tracemalloc` peak for `parse_log(100MB, default limits)` is **< 1.6×** the peak for
   `parse_log(50MB, default limits)`. (Today: scales with input.)
4. `parse_log(anchor, ParseLimits(max_total_lines=2290)).exit_code == 1` with
   `truncated is True`. (Today: `None`.)
5. `len(to_json(parse_log(<150k-line FAILURES block>))) < 2_000_000`. (Today: 7,169,584.)
6. The evidence cap keeps the **first** N entries — `min(evidence)` and `evidence[0]`
   unchanged for every anchor diagnostic.
7. `test_key_facts_about_the_real_failure` passes **unmodified** — 2323 lines,
   `exit_code == 1`, `evidence == (2307,)`,
   `test_id == "tests/test_types.py::test_file_surrogates[type1]"`,
   `file == "tests\\test_types.py"`, `line == 288`, primary cluster identity.
8. The regenerated golden snapshot differs from the committed one **only** inside `stats` —
   verified by T-G2, and reviewable as a `git diff` where every changed line is under the
   `"stats"` key.
9. `SCHEMA_VERSION == "1.2"` (unchanged from Section 5) and `model.py` gains no dataclass
   field or enum member.
10. `parse_log` still never raises across T-P2's 2,000 seeded byte-soup inputs plus every
    existing fuzz case.
11. `git ls-files` shows **no new file over 1 MB**; `tests/fixtures/` gains **zero** files.
12. `pyproject.toml` `dependencies` and `dependency-groups.dev` are **unchanged**.
13. `docs/perf-baselines.md` exists with before/after columns and a machine/date/SHA header.

---

## 10. Potential regressions & risks

| Risk | Why it's real | Guard |
|---|---|---|
| **Line-number fidelity breaks** (the big one). `_bounded_splitlines` must reproduce `splitlines()`'s 9-boundary set exactly, forever. A future CPython adding a boundary would silently diverge. | The anchor's 2323/ESC@226/FAILURES@2276 numbers and every `evidence`, `SourceRange`, `StackFrame.raw_lineno` and `LogSection` bound rest on this. | T-P1 (100k differential cases vs the real `splitlines`), T-G1 (anchor equality), `test_key_facts_about_the_real_failure`. All three compare against `str.splitlines()` itself, so a CPython change fails the test rather than the product. |
| **The tail-rescued diagnostic has no `source_range`.** A consumer assuming it is non-None breaks. | new shape in the wild | already possible — `_fatal_report` (`pipeline.py:45`) emits `source_range=None`. `test_tail_rescued_diagnostic_makes_no_line_claims` pins it; `metadata["from_truncated_tail"]` makes it self-describing |
| **Evidence cap silently drops information Section 5 wanted.** | Section 5 iterates `evidence` in its ranking ladders | head-first cap ([C3](README.md#c3--the-evidence-cap-must-be-head-first)) + `test_evidence_cap_preserves_first_entry`; `source_range` retains the full extent; `stats["evidence_truncated"]` flags it; `max_context_lines` is caller-adjustable. **Standing rule: correlate on `source_range`, sample from `evidence`.** |
| **Golden regeneration masks a real regression.** The section regenerates three times (steps 3, 5, 6). | the exact failure mode `CLAUDE.md` §7 warns about | T-G2 asserts the `diagnostics` / `sections` / `clusters` / `exit_code` / `raw_line_count` subtrees are unchanged; only `stats` may move |
| **`slots=True` breaks something the grep missed.** | grep is not a type checker | step 4 is isolated with no other change; the full suite is the guard |
| **`max_total_chars` truncates a legitimate large log**, losing the failure. | 100 MB is generous, not infinite | `report.truncated is True` + `stats["truncated_chars"]` + the tail rescue still recovers the exit code; and it is a `ParseLimits` field a caller can raise |
| **Perf tests flake on a slower machine or in CI.** | the classic reason perf suites get deleted | excluded by the `perf` marker; absolute ceilings carry 30–40× headroom; the assertion that matters (`test_perf_scales_sublinearly_in_time`) is a **ratio** |
| **Perf tests generate 100 MB and OOM a small runner.** | 100 MB str + ~360 MB peak | `perf` excluded by default and documented as needing ~1 GB free; 10 MB and 50 MB carry most of the signal |
| **Section 7 invalidates the recorded baselines.** | 3 → 13 masking rules against a stage that is 77% of runtime | [C6](README.md#c6--section-7-invalidates-section-6s-perf-baselines): Section 7 re-records the table as its final step |

---

## 11. Explicitly deferred

Documented decisions, not oversights. Add to `CLAUDE.md` §8.

- **True streaming / chunked parsing — declined, not deferred (D1).** Impossible without
  rewriting `PytestParser`, `JsTestParser`, `find_process_failure` and the cluster builder,
  which the architecture forbids; and a stream-fed line split cannot reproduce
  `splitlines()` boundaries, so it would be non-deterministic against `parse_log`. Reopen
  only if a real GitHub Actions job log exceeds `max_total_chars` in practice.
- **`parse_log_lines` — deferred until a caller exists (D1).** Signature and ~10-line
  implementation written down in D1. Trigger: `GitHubClient.get_job_log` switching to
  `resp.iter_lines()`.
- **`SourceRange` byte offsets — declined, deferral closed (D6).** Upgrade path is an
  on-demand `line_byte_offsets(content)` side table.
- **Full head+tail truncation with a gap sentinel (D5).** Only the exit-code rescue ships.
  Trigger: an ecosystem whose *primary* evidence lives only in the tail. Requires
  re-verifying adjacency assumptions in all parsers first.
- **Merging redundant registry `detect` scans (D8).** 1–3% of runtime. Trigger: a profile
  showing registry `detect` >20%.
- **Regex hardening / lowering `max_line_length`.** 28 patterns fuzzed; worst case 1.72 ms
  at 20,000 chars. T-P3 guards against regression.
- **Hypothesis as a dev dependency.** Declined against `CLAUDE.md` §6; seeded stdlib
  `random` gives equivalent confidence for these differential properties. Trigger: a
  section needing stateful or multi-argument properties.
- **Secret-masking matrix, observability metrics, structured logging — Section 7.** The new
  `stats` keys here are counters for the report, not a metrics pipeline.
- **gcc/clang/rustc/javac parsers, Go/Java/Rust stack traces** — still additive parsers,
  unchanged by this section. The central evidence cap (D4) means they get bounded evidence
  for free.
