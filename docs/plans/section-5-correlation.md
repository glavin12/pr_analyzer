# Section 5 — Correlation, Deduplication & Failure Clustering

> Part of the Slice 2 engine plan. Shared constraints and cross-section
> reconciliations: [`README.md`](README.md). Sequenced **after** the Step 0 hotfix.

---

## 1. What exists today & what Section 5 replaces

### Code being replaced

| Location | What it does today | Fate |
|---|---|---|
| `parsing/pipeline.py:150-173` — `_build_clusters` | One `FailureCluster` per `Diagnostic`, `related=()` always, `section_id` from `evidence[0]` | **Deleted.** Replaced by `clustering.build_clusters(diagnostics, lines)` |
| `parsing/pipeline.py:176-181` — `_pick_primary` | `max(confidence)` over diagnostics with `file is not None`, else over all | **Deleted.** Replaced by the cluster-ordering ladder (§2.6) |
| `parsing/pipeline.py:84` | `clusters, primary_cluster = _build_clusters(...)` | Rewritten to call `build_clusters` |
| `parsing/pipeline.py:89-98` — `stats` | 8 keys | **Additive:** `+ clusters_built`, `+ diagnostics_deduplicated` (name per [C2](README.md#c2--stats-key-naming-collision)) |
| `parsing/model.py:24` | `SCHEMA_VERSION = "1.1"` | → `"1.2"` (§3) |
| `parsing/model.py:138-143` — `FailureCluster` | 4 fields | **Additive:** `+ related_roles`, `+ key` |
| `tests/test_parsing_pipeline.py:4-11` — `test_parse_log_builds_trivial_one_cluster_per_diagnostic` | Asserts `len(clusters) == len(diagnostics)` | **Deleted by name.** This test *is* the placeholder contract; its own name says "trivial". Replaced, not weakened for convenience. |

### Currently correct — must be preserved bit-for-bit

- **`report.exit_code` semantics** (`pipeline.py:85`): `next(d.exit_code for d in diagnostics if d.exit_code is not None)`. Reads `diagnostics`, not clusters. **Do not touch this line.** Section 5 never rewrites `report.diagnostics`, so the semantics survive by construction.
- **`report.diagnostics` is the non-lossy evidence record** — every diagnostic every parser emitted, in emission order. Section 5 does **not** delete from it. Dedup is expressed as cluster membership + role, never by dropping evidence.
- **The pallets/click primary-cluster expectation** (`tests/test_parsing_golden_snapshot.py:40-61`): `primary_cluster.primary` is the `TEST_FAILURE` for `tests/test_types.py::test_file_surrogates[type1]`, `file == "tests\\test_types.py"`, `line == 288`; one `PROCESS_FAILURE` with `evidence == (2307,)` and `exit_code == 1`; `report.exit_code == 1`; `raw_line_count == 2323`.
- **`section_id` derivation** — section of the primary's first evidence line. Keep the existing three lines verbatim (moved into `clustering.py`), so the golden fixture's `"section_id": null` stays `null`.
- **`parse_log` total-function guard** (`pipeline.py:30-33`) and `_fatal_report` (`pipeline.py:36-69`). New `FailureCluster` fields get defaults so `_fatal_report`'s construction at `pipeline.py:54` compiles unchanged.
- **`primary_frame` "last in-project frame"** (`stacktrace.py:134-144`) — clustering consumes frames, never re-picks them.

### Two verified facts that drive the design

**(a) A duplicate `PROCESS_FAILURE` already ships today.** `_run_registry`
(`pipeline.py:125-136`) runs *every* non-fallback parser that detects and concatenates.
All four parsers call `find_process_failure` (`pytest_parser.py:106`,
`js_test_parser.py:76`, `compiler_parser.py:63`, `generic_parser.py:31`) — verified by
grep. A log that trips two parsers emits the identical exit-code diagnostic twice,
differing **only in the `parser` field**. This is the concrete reason `parser` is excluded
from the dedup key.

**(b) `section_id` is `null` for every diagnostic in every committed fixture.** GitHub
Actions closes the `##[group]Run <cmd>` block *before* the step's output is printed — the
group wraps the command echo and env only. **Therefore no correlation rule may key on
`section_id`.** The field is kept on the cluster as evidence-when-present; the
"section/step nesting" signal exists in the model but is empirically inert for this
provider, and a rule built on it would never fire. See §11 for the correct implementation
when a rule needs it.

---

## 2. Design

Three stages, one pass, one module. Input: `diagnostics: tuple[Diagnostic, ...]`
(emission order) + `lines`. Output: `(clusters, primary_cluster)`.

```
diagnostics ──▶ [A] key each ──▶ [B] collapse exact duplicates ──▶ [C] correlate representatives
                                                                          │
             primary_cluster ◀── [E] order clusters ◀── [D] rank members ◀┘
```

### 2.1 Message normalization (key construction only)

`normalize_message(msg: str | None) -> str`. Applied **only** to build keys.
`Diagnostic.message` is never rewritten.

| # | Rule | Transform | Justification |
|---|---|---|---|
| 1 | null | `msg or ""` | `message` is Optional |
| 2 | separators | `\` → `/` | the click fixture renders the same file as `tests\test_types.py` (traceback) and `tests/test_types.py` (test id) |
| 3 | timestamps | `\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z\|[+-]\d{2}:?\d{2})?` → `<TS>` | a retried step reprints the same error with a new clock |
| 4 | temp paths | `(?i)(?:[a-z]:)?/(?:[^\s'"]*/)?(?:tmp\|temp)/[^\s'"]*` → `<TMP>` | pytest `--basetemp` renders per-run dirs. **Runs before rule 5** (temp dirs contain hex) |
| 5 | hex / ids | `(?i)\b(?:0x[0-9a-f]{4,}\|[0-9a-f]{12,})\b` → `<HEX>` | object addresses, shas, uuid chunks |
| 6 | durations | `(?i)\b\d+(?:\.\d+)?\s?(?:ms\|s\|sec\|secs\|seconds\|m\|min)\b` → `<DUR>` | `in 6.41s`, `(3 ms)` |
| 7 | pids/ports | `(?i)\b(?:pid\|port)[= ]\d+` → `pid=<ID>` | tox prints `pid=3332` |
| 8 | whitespace | `\s+` → `" "`, then `.strip()` | render noise |
| 9 | case | `.casefold()` | last; verified safe on the anchor fixture |

**Deliberately NOT stripped** — these are decisions an engineer must not "improve"
without a fixture proving the need:

- **Bare integers (`\b\d+\b` → `<N>`) — rejected.** It would collide `expected 3 to be -3`
  with `expected 4 to be -4`, and every numeric-bearing lint message. Rules 3–7 already
  remove all per-run variance observed in the committed corpus.
  `ponytail:` upgrade path — add as rule 8b only when a fixture shows two occurrences of
  one failure that rules 3–7 fail to collapse.
- **Quotes and punctuation — kept.** `'x' is assigned a value but never used` loses its
  subject without them.
- **Absolute path prefixes — not stripped.** One log = one job = one checkout root, so
  paths are already consistent within a report.
- **Truncated messages — not un-truncated.** A truncated message is genuinely weaker
  evidence; a truncated/full pair correlates via `test_id` (rule C1) and lands in the same
  cluster as `SECONDARY`.

### 2.2 Path normalization

`norm_path(p: str | None) -> str`: `""` if None → `p.replace("\\", "/")` → collapse
`//+` → strip a leading `./` → `.casefold()`.

Casefolding is technically wrong on a case-sensitive filesystem, but this is a *keying*
function only and the anchor fixture is a Windows job. The cost of a false collapse (two
files differing only in case in one repo) is far below the cost of never collapsing
Windows path variants.

### 2.3 The dedup key

```python
def dedup_key(d: Diagnostic) -> str:
    return "|".join((
        d.type.value,
        d.severity.value,
        d.tool or "",
        str(d.metadata.get("code") or d.metadata.get("rule") or ""),
        norm_path(d.file),
        "" if d.line is None else str(d.line),
        "" if d.column is None else str(d.column),
        d.test_id or "",
        normalize_message(d.message),
    ))
```

**Excluded, each with its reason:**

| Excluded | Why |
|---|---|
| `parser` | **The point.** Two parsers co-emitting one `PROCESS_FAILURE` (verified, §1a) must collide. |
| `evidence`, `source_range` | Exactly what differs between two occurrences of one failure. |
| `confidence` | A constant from `confidence.py`, not evidence. |
| stack signature | **Deliberate.** `type + tool + code + file + line + test_id + message` already separates every real case. Adding a stack signature can only make the key *more* selective — i.e. cause real duplicates to *fail* to collapse (one occurrence truncated, one not). Frames are still used, as a frame set, by rule C3. |
| `metadata` beyond `code`/`rule` | `run_summary` is per-run; `expected`/`actual` are derivable from the message; `outcome` never varies within a `test_id`. |

### 2.4 The four dedup categories → exactly what happens to each

| Category | Deterministic detection | Outcome |
|---|---|---|
| **Exact duplicate** | `dedup_key(a) == dedup_key(b)` | The **first-emitted** occurrence is the group representative and enters correlation. Every later occurrence attaches to the representative's cluster as `related` with role `DUPLICATE`. **Collapses** (cluster count does not grow). Nothing is deleted from `report.diagnostics`. Counted in `stats["diagnostics_deduplicated"]`. |
| **Near duplicate** | Same location (`type + tool + code + norm_path(file) + line + test_id`) but a different `normalize_message` | **Not a separate mechanism.** Rule C2a already puts them in one cluster; the loser gets role `SECONDARY`. **Kept, with a link.** |
| **Summary of existing** | Rule S1 | **Kept, with a link**: attached as `related` with role `SUMMARY`; can never be a cluster primary. |
| **Related but independent** | No correlation rule fires | **Separate cluster. No link field.** Their only real relation is "same job", already expressed by being in the same `FailureReport` and by cluster ordering. A `related_cluster_ids` field would have no consumer — skipped on purpose. |

### 2.5 Correlation rules

Applied **rule-major**: for each representative `d` in emission order, try S1 → C1 → C2a
→ C2b → C3 → C4 in order; within a rule, scan existing clusters in creation order and
take the first match. First match anywhere wins. If nothing matches, `d` opens a new
cluster with attach-relation `member`.

**Global guard G1** (checked before C1–C3 can attach anything): two diagnostics with
**different non-None `test_id`s never share a cluster.** The test is the unit of failure;
two failing tests in one file, or two tests sharing an in-project helper frame, must stay
separate.

| # | Rule | Condition | Attach relation |
|---|---|---|---|
| **S1** | marker echo | `d.type is not PROCESS_FAILURE`; **every** line in `d.evidence` carries `marker in (WorkflowMarker.ERROR, WorkflowMarker.WARNING)`; and there exists a clustered `t` with equal `normalize_message`, `d.line == t.line`, `d.column == t.column`, where **not** all of `t`'s evidence lines are marker lines | `summary` |
| **C1** | same test | `d.test_id is not None` and `== cluster.primary.test_id` | `member` |
| **C2a** | same file+line | G1 holds; `norm_path(d.file) != ""` and equals the cluster primary's; `d.line is not None and == cluster.primary.line` | `member` |
| **C2b** | same file, same tool, file-level tools only | G1 holds; `norm_path(d.file)` equal; `d.tool is not None and == cluster.primary.tool`; **and `d.test_id is None and cluster.primary.test_id is None`** | `member` |
| **C3** | stack-frame overlap | G1 holds; both have a `stack_trace`; `frame_set(d) & frame_set(cluster.primary)` non-empty, where `frame_set(d) = {(norm_path(f.file_path), f.line_number) for f in d.stack_trace.frames if f.in_project}` | `member` |
| **C4** | known chain — runner exit | `d.type is PROCESS_FAILURE`. Deferred to pass 3 below. | `consequence` |
| — | else | — | new cluster, `member` |

**C4 in detail** (the `assertion → test failure → runner exit code → step failed → job
failed` chain). Because `find_process_failure` returns only `matches[-1]`
(`process_failure.py:29`), a log has **at most one distinct** `PROCESS_FAILURE` after
dedup. So:

1. **Pass 1** — cluster every non-`PROCESS_FAILURE` representative with S1/C1/C2a/C2b/C3.
2. **Pass 2** — rank pass-1 clusters (§2.6) and take the winner.
3. **Pass 3** — attach every `PROCESS_FAILURE` representative to that winning cluster with
   relation `consequence`. **If pass 1 produced no clusters**, each `PROCESS_FAILURE`
   becomes its own cluster with relation `member` — this is the *job-level* cluster: a red
   job whose tool output the engine could not parse. "Job-level" is a description of that
   condition, not a role or a type; no new enum value for it.

*Rejected alternative:* attach the `PROCESS_FAILURE` to the nearest preceding cluster by
line number. On the eslint fixture that picks `src/utils.js` over `src/index.js` for no
defensible reason. "The job exited N because of the primary failure" is never arbitrary.

**Why there is no section-nesting rule:** see §1b — `section_id` is `null` for every
diagnostic in every committed fixture.

### 2.6 The two ranking ladders

Both are sort keys; **lower tuple wins**; the final element makes each a total order, so
two engineers get byte-identical output.

**Member ranking — which member of a cluster is its `primary`.** Only members with
attach-relation `member` are eligible. `duplicate` / `summary` / `consequence` can never
be primary. The winner becomes role `PRIMARY`; remaining `member`s become `SECONDARY`.

| Rank | Discriminator | Key |
|---|---|---|
| 1 | severity | `{ERROR: 0, WARNING: 1, INFO: 2}[d.severity]` |
| 2 | diagnostic type | `_TYPE_RANK[d.type]` |
| 3 | located | `0 if d.file is not None else 1` — preserves `pipeline.py:179` |
| 4 | confidence | `-d.confidence` |
| 5 | earliest evidence | `min(d.evidence) if d.evidence else 0` |
| 6 | emission index | index in `report.diagnostics` — **total order** |

**Cluster ranking — `clusters` order, and `report.primary_cluster = clusters[0]`.**
Applied to pass-1 clusters only.

| Rank | Discriminator | Key |
|---|---|---|
| 1 | has an ERROR member | `0 if any(m.severity is Severity.ERROR for m in members) else 1` |
| 2 | classification type | `_TYPE_RANK[cluster.classification]` |
| 3 | located primary | `0 if cluster.primary.file is not None else 1` |
| 4 | primary confidence | `-cluster.primary.confidence` |
| 5 | corroboration | `-len(members)` |
| 6 | earliest evidence | `min(min(m.evidence) for m in members if m.evidence)`, else `0` |
| 7 | creation index | **total order** |

```python
_TYPE_RANK = {
    DiagnosticType.TEST_FAILURE:     0,   # names a concrete broken behaviour
    DiagnosticType.EXCEPTION:        1,
    DiagnosticType.COMPILER_ERROR:   2,   # names a concrete broken symbol
    DiagnosticType.LINT_ERROR:       3,
    DiagnosticType.DEPENDENCY_ERROR: 4,
    DiagnosticType.UNKNOWN:          5,
    DiagnosticType.PROCESS_FAILURE:  6,   # names nothing
}
```

**Why type rank sits above line order (rank 2 vs rank 6), with its counter-argument.** A
cross-step cascade (build fails, then tests fail) would be ranked by earliest-failure
under the opposite ordering. Rejected because GitHub Actions **halts the job at the first
failing step** unless `continue-on-error` is set, so multi-failing-step logs are the
exception; and hoisting line order above type rank would make a lint error outrank a test
failure in the very common `lint step → test step` layout.
`ponytail:` this is the one heuristic in Section 5 with a real judgement call. Upgrade
path: if Slice 6's labeled corpus shows earliest-failure beats type rank, swap
discriminators 2 and 6 — one table, one line.

`_TYPE_RANK[DEPENDENCY_ERROR] = 4` is provisional: no parser emits it yet. When a
dependency parser lands, "install failed ⇒ nothing downstream is meaningful" probably
wants rank 0. Revisit then, not now.

### 2.7 Resulting shape

- `related` is ordered by **emission order**, `related_roles` index-aligned. One less rule
  to specify and test than a role-sorted order; `related_roles` makes any consumer filter
  trivial.
- `classification = primary.type` (unchanged meaning).
- `section_id` = section of `min(primary.evidence)` — the existing three lines from
  `pipeline.py:154-158`, moved verbatim.
- `key = dedup_key(primary)` — a stable cluster identity across runs, and it makes the
  golden snapshot a readable spec of the normalization rules.

**Complexity:** single pass, rule-major, first-match-wins, no fixpoint iteration, no
union-find. Worst case O(rules × n²) with n bounded by `ParseLimits.max_diagnostics = 500`.
`ponytail:` O(n²) scan with a ceiling of 500 diagnostics; index clusters by
`test_id`/`(file, tool)` in dicts if Section 6's perf work shows it matters.

---

## 3. Contract changes

**`SCHEMA_VERSION`: `"1.1"` → `"1.2"`** (see [C1](README.md#c1--schema_version-chain)).
The JSON gains a new enum vocabulary (`DiagnosticRole`) and two new `FailureCluster`
keys. Every existing field keeps its name, type and meaning, so a 1.1 consumer that
ignores unknown keys still works — additive, but a version bump is how this repo signals
*any* output-shape change (precedent: Section 3's 1.0 → 1.1 for the additive
`TestOutcome`). Both new fields carry defaults, so the only construction site outside
`clustering.py` — `pipeline.py:54` — compiles unchanged.

`FailureReport` is **not** changed. New observability goes into `stats` (a plain `dict`,
no schema surface).

`parsing/model.py`, verbatim:

```python
class DiagnosticRole(Enum):
    """The role a diagnostic plays *inside its cluster* (Section 5).

    Not a property of the diagnostic itself -- a parser never sets this; it
    is assigned by `clustering.build_clusters` from the correlation rule
    that attached the diagnostic. "Job-level" is not a role: it describes a
    cluster whose only member is a PROCESS_FAILURE because nothing else in
    the log was parseable.
    """

    PRIMARY = "primary"
    SECONDARY = "secondary"
    CONSEQUENCE = "consequence"
    SUMMARY = "summary"
    DUPLICATE = "duplicate"
```

```python
@dataclass(frozen=True)
class FailureCluster:
    primary: Diagnostic
    related: tuple[Diagnostic, ...]
    section_id: int | None
    classification: DiagnosticType
    # Section 5, additive. Index-aligned with `related`: related_roles[i] is
    # the role of related[i]. `primary` is implicitly DiagnosticRole.PRIMARY
    # and is not listed here. Invariant: len(related_roles) == len(related).
    related_roles: tuple[DiagnosticRole, ...] = ()
    # Section 5, additive. `clustering.dedup_key(primary)` -- the normalized
    # identity these diagnostics were grouped under. Stable across runs, so
    # the golden snapshot doubles as a readable spec of the dedup rules.
    key: str | None = None
```

Also: `SCHEMA_VERSION = "1.2"`; export `DiagnosticRole` from `parsing/__init__.py`.

No change to `Diagnostic`, `DiagnosticType`, `Severity`, `TestOutcome`, `SourceRange`,
`StackFrame`, `StackTrace`, `LogLine`, `LogSection`, `LogSource`, `FailureReport`,
`ParseLimits`, `to_dict`, `to_json`, or any parser.

---

## 4. Files & modules

### New (one source file)

**`src/agentic_pr_analyzer/parsing/clustering.py`** — the whole stage:

```python
normalize_message(msg: str | None) -> str
norm_path(p: str | None) -> str
dedup_key(d: Diagnostic) -> str
build_clusters(diagnostics, lines) -> tuple[tuple[FailureCluster, ...], FailureCluster | None]
```

**Why one file and not two.** `dedup.py` + `clustering.py` was considered. `dedup_key` and
`normalize_message` have exactly **one** consumer — the clusterer — and share its single
concern: *deciding what counts as the same failure*. The repo splits modules when they
have separate consumers (`stacktrace.py`, `process_failure.py` are shared by parsers) or
genuinely different inputs/outputs (`normalizer.py` → `segmentation.py`). Neither applies.
They are public (no leading underscore) so tests exercise them directly without reaching
into privates. **Split trigger, stated up front:** if Slice 3's diff correlator wants
`normalize_message`, extract it to `dedup.py` then.

**Why the boundary is honest anyway.** `pipeline.py` currently owns clustering logic
inline (`:150-181`). After this change `pipeline.py` orchestrates stages and owns none of
them — the same shape as `normalize` / `build_sections` / `PARSER_REGISTRY`.

### Changed

| File | Change |
|---|---|
| `parsing/model.py` | `SCHEMA_VERSION` → `"1.2"`; `+ DiagnosticRole`; `FailureCluster` `+ related_roles`, `+ key` |
| `parsing/pipeline.py` | delete `:150-181`; import `build_clusters`; rewrite `:84`; `+ 2` stats keys. **Line 85 (`exit_code`) untouched.** |
| `parsing/__init__.py` | export `DiagnosticRole` |
| `tests/test_parsing_pipeline.py` | see §6 |
| `tests/test_parsing_golden_snapshot.py` | see §6/§7 |
| `tests/fixtures/parsed/pallets/click/32472305359_96741461054.json` | regenerated — §7 |
| `tests/fixtures/raw_logs/SYNTHETIC/README.md` | document the 4 new fixtures + evidence level |
| `CLAUDE.md` §2 / §4 / §8 | tick Section 5; add `clustering.py` to the tree; remove "cross-diagnostic clustering/deduplication" from deferrals |
| `docs/project-brief.md` | Slice 2 item 5 |

### Not changed

`parsers/*` (all four), `stacktrace.py`, `process_failure.py`, `segmentation.py`,
`normalizer.py`, `confidence.py`, `limits.py`, `sanitize.py`, `providers/*`, `cli.py`.

---

## 5. Fixtures

### Existing coverage (no new files needed)

| Fixture | Type | What Section 5 exercises |
|---|---|---|
| `raw_logs/pallets/click/32472305359_96741461054.log` | **REAL** | The anchor. Intra-step chain `assertion → test failure → runner exit code`: 2 diagnostics → **1 cluster** with one `CONSEQUENCE`. The common case, and it is real. |
| `SYNTHETIC/eslint-sample/sample.log` | synthetic | C2b file-level grouping + mixed ERROR/WARNING: 4 diagnostics → **2 clusters**; `src/index.js` cluster has a `SECONDARY` warning. Exercises cluster-rank discriminator 5. |
| `SYNTHETIC/tsc-sample/sample.log` | synthetic | **Anti-over-merge:** two errors in two files stay **2 clusters** (C2b requires same file). |
| `SYNTHETIC/jest-sample/`, `vitest-sample/` | synthetic | Single test failure + `CONSEQUENCE`: 2 diagnostics → **1 cluster**, twice, across two tools. |

On all five, the *primary diagnostic identity is unchanged* by Section 5 — only cluster
count and roles change. That is acceptance criterion AC-2.

### New fixtures

All four are **`SYNTHETIC/`** with a README entry. The only real capture in hand is
`pallets/click`, a single-test-failure Python job; each shape below requires a red public
run that is not in hand — same precedent as Sections 3 and 4 — and each carries a
"replace with a real capture the first time one is seen" note.

| Fixture | Shape | Why synthetic | Evidence strength |
|---|---|---|---|
| `SYNTHETIC/multi-test-sample/sample.log` | pytest, **3 failing tests across 2 files**, `= FAILURES =` block, `short test summary info`, `##[error]...exit code 1` | the real capture has exactly one failing test | **Strong** — a straight extension of the verified anchor's pytest output |
| `SYNTHETIC/duplicate-sections-sample/sample.log` | a tsc section **and** a pytest section in one log, so both parsers fire → **two byte-identical `PROCESS_FAILURE`s differing only in `parser`** | a polyglot red job was not captured — **but the duplication is a verified property of `_run_registry` today**, not an invention (§1a) | **Strong** — the bug it pins was reproduced against real code |
| `SYNTHETIC/cascade-sample/sample.log` | two failing steps: `##[group]Run npm run build` + 2 tsc errors, then `##[group]Run npm test` + 1 jest failure, then one `##[error]...exit code 1` | a two-failing-step job requires `continue-on-error`; GHA otherwise halts at the first failure | Medium — step shape matches real GHA; the layout is uncommon |
| `SYNTHETIC/summary-echo-sample/sample.log` | a tsc error printed by the tool, then echoed by a problem matcher as `##[error]src/auth.ts(42,7): error TS2339: ...` | no committed capture has a problem-matcher annotation | **Weakest in the set — say so in the README.** The exact rendering of problem-matcher output in a raw job log is unconfirmed. **First to replace with a real capture.** |

**Parser bug found while designing `summary-echo-sample`, filed separately:** the current
`CompilerParser` matches the annotation line and produces a second `COMPILER_ERROR` with
`file == "##[error]src/auth.ts"` — `_TSC_NONPRETTY_RE`'s unanchored `\S+` swallows the
marker prefix. Rule S1 is deliberately robust to it (it matches on normalized message +
line + column, never `file`). **Fixing the regex is a one-line parser change, out of
Section 5's scope** — file it separately so the dedup layer never becomes where parser
bugs get papered over. *Rejected alternative:* stripping a leading `##[<marker>]` inside
`norm_path` — that hides a parser bug in the normalizer, where it would rot.

---

## 6. Tests (written first)

Two new files, ~30 named cases, plus surgical edits to two existing files.

### New — `tests/test_parsing_clustering.py`

*Message normalization*
- `test_normalize_message_none_is_empty_string`
- `test_normalize_message_collapses_windows_and_posix_separators`
- `test_normalize_message_masks_timestamp` / `_duration` / `_hex` / `_tmp_path` / `_pid` — two messages differing only in that token normalize equal
- `test_normalize_message_keeps_bare_integers` — `"expected 3"` and `"expected 4"` normalize **different**. *Pins the deliberate non-rule.*
- `test_normalize_message_never_raises_on_lone_surrogate`

*Path normalization*
- `test_norm_path_is_separator_and_case_insensitive`
- `test_norm_path_none_is_empty_string`

*Dedup key*
- `test_dedup_key_ignores_parser_field` — **the rule that fixes §1a**
- `test_dedup_key_ignores_evidence_and_source_range`
- `test_dedup_key_ignores_confidence`
- `test_dedup_key_separates_different_severity`
- `test_dedup_key_separates_different_error_code` — `TS2339` vs `TS2345`
- `test_dedup_key_excludes_stack_signature` — identical fields, different `stack_trace` → **same** key. *Pins the deliberate exclusion.*

*Ranking ladders*
- `test_cluster_rank_error_beats_warning`
- `test_cluster_rank_test_failure_beats_compiler_error`
- `test_cluster_rank_located_beats_unlocated`
- `test_cluster_rank_more_members_wins_on_tie`
- `test_cluster_rank_is_stable_for_indistinguishable_clusters`
- `test_member_rank_error_beats_warning_in_same_cluster`
- `test_consequence_and_summary_and_duplicate_are_never_primary`

*Guards*
- `test_different_test_ids_never_merge` — G1, driven through C2b and C3

*Invariants over the whole corpus (parametrized over all 9 fixture logs)*
- `test_invariant_every_diagnostic_appears_exactly_once_across_clusters`
- `test_invariant_related_roles_length_matches_related_length`
- `test_invariant_primary_cluster_is_clusters_zero_when_clusters_nonempty`
- `test_invariant_no_cluster_primary_has_a_non_primary_role`
- `test_invariant_clustering_is_deterministic` — `to_json(parse_log(c))` twice, byte-identical
- `test_invariant_no_fixture_produces_a_fatal_report` — `"fatal" not in report.stats`. *Catches a clustering exception being swallowed by `parse_log`'s guard and silently degrading the report.*

### New — `tests/test_parsing_correlation_fixtures.py`

- `test_click_anchor_collapses_to_one_cluster_with_a_consequence` — REAL fixture: `len(clusters) == 1`, `related_roles == (DiagnosticRole.CONSEQUENCE,)`
- `test_eslint_fixture_groups_by_file_and_tool` — 4 → 2 clusters
- `test_eslint_fixture_warning_is_secondary_not_primary`
- `test_tsc_fixture_two_files_stay_two_clusters` — anti-over-merge
- `test_jest_fixture_process_failure_is_a_consequence`
- `test_vitest_fixture_process_failure_is_a_consequence`
- `test_multi_test_fixture_one_cluster_per_failing_test` — 3 clusters
- `test_multi_test_fixture_same_file_different_tests_do_not_merge` — G1 end-to-end
- `test_duplicate_sections_fixture_collapses_cross_parser_process_failure` — `stats["diagnostics_deduplicated"] == 1`; exactly one `CONSEQUENCE` + one `DUPLICATE`
- `test_duplicate_sections_fixture_keeps_both_diagnostics_in_report` — non-lossy
- `test_duplicate_sections_fixture_exit_code_still_one`
- `test_cascade_fixture_produces_one_cluster_per_failing_unit` — 3 clusters
- `test_cascade_fixture_process_failure_attaches_only_to_the_primary_cluster`
- `test_summary_echo_fixture_marks_marker_sourced_diagnostic_as_summary`
- `test_summary_echo_fixture_primary_is_the_unpolluted_tool_diagnostic` — `primary.file == "src/auth.ts"`

### Changed — `tests/test_parsing_pipeline.py`

- **Delete** `test_parse_log_builds_trivial_one_cluster_per_diagnostic` — it asserts the placeholder contract Section 5 is chartered to remove.
- **Add** `test_parse_log_collapses_process_failure_into_the_primary_cluster`
- **Add** `test_parse_log_bare_process_failure_is_its_own_job_level_cluster`
- **Add** `test_parse_log_every_diagnostic_belongs_to_exactly_one_cluster`
- **Keep verbatim**: `test_parse_log_primary_cluster_prefers_located_diagnostic_over_bare_process_exit`, `test_parse_log_with_no_diagnostics_has_no_primary_cluster`, `test_parse_log_exit_code_bundled_from_process_failure_diagnostic`
- **Edit one line**: `schema_version == "1.2"`

### Changed — `tests/test_parsing_golden_snapshot.py`

`test_key_facts_about_the_real_failure` — **strengthened, not weakened**. Every existing
assertion stays byte-identical; only `schema_version` flips. Add:

```python
assert len(report.clusters) == 1
assert report.primary_cluster.related_roles == (DiagnosticRole.CONSEQUENCE,)
assert report.primary_cluster.related[0].exit_code == 1
assert "fatal" not in report.stats
```

`test_golden_snapshot_matches_committed_json` and
`test_json_output_is_pure_ascii_and_valid` — **code unchanged**; the committed JSON is
regenerated (§7).

### Unchanged — `tests/test_parsing_fuzz_security.py`

`parse_log`'s top-level guard already covers clustering. The gap the guard *creates* is
closed by the `"fatal" not in report.stats` invariant above.

---

## 7. Golden-snapshot impact

`tests/fixtures/parsed/pallets/click/32472305359_96741461054.json` **will change** —
exactly and only:

| Field | Before | After |
|---|---|---|
| `schema_version` | `"1.1"` | `"1.2"` |
| `sections` | 18 entries | **identical** |
| `diagnostics` | 2 entries | **byte-identical** |
| `clusters` | 2 entries, each `related: []`, `section_id: null` | **1 entry**: `primary` = the `test_failure` (verbatim), `related` = `[<the process_failure, verbatim>]`, `section_id: null`, `classification: "test_failure"`, **new** `related_roles: ["consequence"]`, **new** `key: "<dedup_key(primary)>"` |
| `primary_cluster` | the `test_failure` cluster | same cluster, now carrying `related` / `related_roles` / `key` |
| `exit_code` | `1` | **`1`** |
| `raw_line_count` | `2323` | **`2323`** |
| `truncated` | `false` | **`false`** |
| `stats` | 8 keys | **+ `"clusters_built": 1`, + `"diagnostics_deduplicated": 0`** |

**How the regeneration is justified.** This is an intentional output-format change —
Section 5's entire deliverable is that `clusters` stops mirroring `diagnostics` 1:1. The
discipline that keeps it from papering over a regression:

1. `test_key_facts_about_the_real_failure` is hand-written, not generated, and is
   strengthened *before* implementation. It fails against current code
   (`len(clusters) == 1`).
2. Regeneration happens **only after** that hand-written test passes (step 8.11). If it
   fails, the implementation is wrong — `CLAUDE.md` §7's default assumption.
3. Regeneration uses the one-liner already in the docstring at
   `tests/test_parsing_golden_snapshot.py:5-14`, **unchanged**.
4. The commit body states the delta table above verbatim.

**Reviewer's one-line check:** `git diff` on the JSON must touch **only**
`schema_version`, the `clusters` array, `primary_cluster`, and two `stats` keys. Any
change inside a `diagnostics` entry is a regression, not a reformat.

---

## 8. Implementation steps (ordered TDD checklist)

1. **Write `tests/test_parsing_clustering.py`** — every unit case in §6. Import from a
   module that does not exist yet. Run: all fail on `ImportError`. This is the spec.
2. **Write the four SYNTHETIC fixture logs** + README entries (shape, why synthetic,
   evidence strength, replace-with-real note). `summary-echo-sample` flagged weakest.
3. **Write `tests/test_parsing_correlation_fixtures.py`** — all 15 cases. Run: fail.
4. **Edit `tests/test_parsing_pipeline.py`** — delete the trivial test, add three, flip
   `schema_version`. Run: fail.
5. **Strengthen the golden fact test** with the four new assertions + `schema_version`.
   Run: fail. **Do not regenerate the JSON yet.**
6. **`model.py`** — add `DiagnosticRole`, the two `FailureCluster` fields (with defaults),
   bump `SCHEMA_VERSION`. Export from `parsing/__init__.py`.
7. **`clustering.py` — smallest implementation, in this order** (run the unit file after
   each): (a) `normalize_message` + `norm_path`; (b) `dedup_key`; (c) `_TYPE_RANK` + the
   two rank-key functions; (d) `build_clusters`: exact-duplicate grouping → C1 → C2a →
   C2b → C3 → G1 → pass-2 rank → pass-3 `PROCESS_FAILURE` attach → S1.
8. **`pipeline.py`** — delete `:150-181`, import and call `build_clusters` at `:84`, add
   the two stats keys. **Leave line 85 alone.**
9. **Edge cases** — walk each, add a case where one is missing:
   - empty `diagnostics` → `((), None)`
   - `evidence == ()` (from `_fatal_report`) → `min()` guarded, no `ValueError`
   - `source_range is None`
   - `stack_trace is None` → C3 skipped, `frame_set` empty
   - a stack with **no** in-project frames → C3 cannot fire
   - `file is None` on every diagnostic → discriminator 3 ties, falls through
   - all-`WARNING` report with no `PROCESS_FAILURE`
   - two `PROCESS_FAILURE`s with *different* exit codes → both attach as `CONSEQUENCE`, `report.exit_code` still takes the first
   - a lone surrogate through `normalize_message` → `dedup_key` → `to_json`
10. **Refactor** — one shared rank-key helper if the two ladders visibly duplicate; add
    the two `ponytail:` comments (O(n²) ceiling; type-rank-over-line-order judgement
    call). Do not add indices, an interface, or a config knob.
11. **Regenerate the golden JSON** — **only now**, and only because step 5's test is
    green. Inspect `git diff` against the §7 table row by row.
12. **Full suite green**, then `uv run agentic-pr-analyzer parse <anchor>.log` to confirm
    the CLI path.
13. **Docs in the same commit**: `CLAUDE.md` §2/§4/§8, `docs/project-brief.md`,
    `SYNTHETIC/README.md`.
14. **File the out-of-scope parser bug** separately (`_TSC_NONPRETTY_RE`).

---

## 9. Acceptance criteria

1. `uv run pytest` fully green; no test deleted or weakened except
   `test_parse_log_builds_trivial_one_cluster_per_diagnostic`.
2. **Primary-diagnostic identity unchanged on all five pre-existing fixtures.**
3. `report.exit_code == 1` on the click fixture; `pipeline.py:85` unmodified in the diff.
4. `len(report.diagnostics)` unchanged on all five — clustering is non-lossy.
5. **Every diagnostic appears exactly once across `report.clusters`**, on all nine.
6. `len(cluster.related_roles) == len(cluster.related)` for every cluster on all nine.
7. click: `len(report.clusters) == 1`, `related_roles == (CONSEQUENCE,)`.
8. eslint 4→2, tsc 3→2, jest/vitest 2→1.
9. `duplicate-sections-sample`: `stats["diagnostics_deduplicated"] == 1`; both
   `PROCESS_FAILURE`s present in `report.diagnostics`; exactly one has role `DUPLICATE`;
   `report.exit_code == 1`.
10. `multi-test-sample`: 3 clusters; no two different non-None `test_id`s share a cluster.
11. `to_json(parse_log(c))` byte-identical across two runs, all nine fixtures.
12. `"fatal" not in report.stats` for all nine.
13. `parse_log` still never raises: the existing fuzz suite passes unchanged.
14. `SCHEMA_VERSION == "1.2"`; `Diagnostic` and every parser file untouched in the diff.
15. Exactly **one** new source file. `git diff --stat src/` shows `clustering.py` (new),
    `model.py`, `pipeline.py`, `__init__.py` — nothing else.
16. The golden JSON diff touches only `schema_version`, `clusters`, `primary_cluster`, and
    two `stats` keys.

---

## 10. Potential regressions & risks

| Risk | Guard |
|---|---|
| **A clustering exception is swallowed by `parse_log`'s guard, silently degrading the report to `_fatal_report`.** The most dangerous failure mode — nothing else would fail loudly. | `test_invariant_no_fixture_produces_a_fatal_report` + `assert "fatal" not in report.stats` in the golden fact test |
| Over-merging: two independent failures collapse, one hidden as `SECONDARY` | G1 tests + the anti-over-merge assertion on the tsc fixture |
| Under-merging: dedup never fires, Section 5 is a no-op with extra fields | `test_dedup_key_ignores_parser_field` + `test_duplicate_sections_fixture_collapses_cross_parser_process_failure` |
| Message normalization becomes too aggressive over time | `test_normalize_message_keeps_bare_integers` and `test_dedup_key_separates_different_error_code` pin the two non-rules most likely to be "improved" |
| Non-deterministic output (set/dict iteration order leaking) | Both ladders end in an index-based total order; `test_invariant_clustering_is_deterministic`; the golden snapshot |
| `report.exit_code` semantics drift | `pipeline.py:85` untouched; two exit-code tests |
| `min(d.evidence)` on `evidence == ()` raises `ValueError` | explicit `if d.evidence else 0` guard; step 9 walk; fuzz suite reaches `_fatal_report` |
| Regenerating the golden JSON masks a real regression | the hand-written fact test must pass **before** regeneration (step 11) |
| The `##[error]`-prefixed-path parser bug gets "fixed" inside `norm_path` and rots | explicitly rejected in §5; S1 matches on message+line+column, never `file`; the parser fix is filed separately |
| `_TYPE_RANK` is the one real judgement call | documented with its counter-argument, marked `ponytail:`, upgrade path is a two-row swap |
| Perf regression on a 200k-line log | O(n²) bounded by `max_diagnostics = 500`, runs once per log; `ponytail:` names the ceiling; Section 6 owns perf |

---

## 11. Explicitly deferred

| Deferred | Owner | Note |
|---|---|---|
| **Step attribution** — the correct implementation of "step nesting" as correlation evidence: the last `LogSection` with a `"Run "`-prefixed title whose `start_lineno < min(evidence)`. Not built because §1b shows no rule needs it today. | Section 7 (observability) or Slice 4 | one helper + one ladder row when a rule needs it |
| Changing `FailureCluster.section_id` from "section of the primary's first evidence line" to "enclosing step group" | later | a **semantic change to an existing field**, not additive — needs its own version bump |
| `related_cluster_ids` / cross-cluster links for "related but independent" | nobody, until a consumer asks | a field with no consumer rots |
| Streaming, bounded memory, indexing clusters by `test_id`/`(file, tool)` | **Section 6** | the `ponytail:` comment names the O(n²)-at-500 ceiling |
| Secret-masking matrix; observability metrics beyond the two stats keys; confidence calibration | **Section 7** | `dedup_key` reuses already-masked text — no new exposure surface |
| `DEPENDENCY_ERROR` rank (provisional 4; probably wants 0) | the section shipping a dependency parser | |
| gcc/clang/rustc/javac parsers, `parse_<lang>_stack` for Go/Java/Rust/C++ | additive parsers, later | Section 5 adds no parser and requires no parser change |
| Correlating failures to **PR diff hunks** | **Slice 3** | Section 5 correlates diagnostics to each other only |
| Any causal claim — Section 5 produces a **failure origin candidate** per cluster and orders clusters. It never says "root cause". | never | `model.py:9-10` |
