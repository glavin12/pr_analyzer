# Section 8 — MCP Adapter

> Part of the Phase 1 plan. House style and shared conventions follow
> [`docs/plans/README.md`](README.md) and [`section-7-security.md`](section-7-security.md):
> deterministic only, stdlib-first, tests before implementation, additive contract changes,
> explicit deferrals. This section does **not** modify the parsing engine
> (`src/agentic_pr_analyzer/parsing/`) — it wraps the frozen `parse_log`/`to_json`/
> `FailureReport`/`ParseLimits`/`SCHEMA_VERSION` surface that Sections 1–7 already shipped
> (`CLAUDE.md` §2) behind an MCP tool, for a coding agent (Codex, then Claude Code) to call
> instead of `cat`-ing a raw CI log into its own context.

---

## 1. Goal & non-goals

**Goal.** Expose the deterministic log-parsing engine as a tool a coding agent can call over
stdio, using the official `mcp` Python SDK (`pyproject.toml` dependency added this section).
The agent hands the server a path to a saved CI log; the server parses it with the existing
engine and hands back a small, structured, already-triaged summary — cluster ranking,
confidence, exit code, evidence excerpts — instead of the raw log text. The parsing engine is
the moat (`CLAUDE.md` §3); this section is the thinnest possible pipe from that moat to an
agent's tool-call surface.

**Non-goals**, each deliberate rather than deferred:

- **No LLM, diagnosis, or fix generation anywhere in this layer.** The server returns exactly
  what `parse_log` already computed — clusters, confidence buckets, evidence. It reasons about
  nothing. Diagnosis is the calling agent's job (or Slice 4+'s, later, in-process). Adding a
  causal explanation here would duplicate `CLAUDE.md` §6's "no hidden behavior" violation the
  parsing engine itself has avoided across seven sections.
- **No GitHub auth or log-fetch in the server.** `github/client.py` and `github/ingestion.py`
  are not imported anywhere under `mcp/`. The calling agent already has its own way to obtain
  a log — the raw `gh api .../actions/jobs/<id>/logs` blob, a downloaded artifact, a fixture under
  `tests/fixtures/raw_logs/` — and hands the server a **path**, not a URL or a repo slug. This
  keeps the module boundary from `CLAUDE.md` §4 intact: GitHub API access stays a separate,
  untouched module tree.
- **No HTTP daemon.** stdio only, one process per agent session, exactly the transport the
  `mcp` SDK's stdio server implements out of the box. A long-running network listener is a new
  attack surface, a new deployment concern, and nothing on the current roadmap needs it —
  Slice 8+'s web UI (not started) is a different surface with its own security model.
- **No autonomous or webhook triggering.** The server only ever responds to a tool call the
  agent explicitly makes. It does not poll GitHub, does not watch a repo, does not run on a
  schedule. That class of behavior is explicitly Slice 5+ orchestration-loop territory
  (`CLAUDE.md` §8), not this adapter.

---

## 2. What this section builds on

No new parsing logic. The adapter is a pure consumer of the frozen public surface
(`parsing/__init__.py`):

| Symbol | Source | Used for |
|---|---|---|
| `parse_log(content, source, limits) -> FailureReport` | `parsing/pipeline.py:129` | the one call that does all the work |
| `FailureReport`, `FailureCluster`, `Diagnostic`, `DiagnosticType`, `DiagnosticRole` | `parsing/model.py` | the shape being adapted |
| `SCHEMA_VERSION` (currently `"1.3"`) | `parsing/model.py:30` | reported alongside, never confused with the adapter's own version (§4.3) |
| `ParseLimits` | `parsing/limits.py` | used with its defaults — the adapter does not tune limits per call in v1 |
| `confidence.py`'s four constants (`0.9`/`0.85`/`0.6`/`0.4`) | `parsing/confidence.py` | the float `Diagnostic.confidence` always holds exactly one of these; bucketing thresholds in §9 are chosen to split cleanly between them |
| `clustering.build_clusters` → `report.clusters` / `report.primary_cluster` | `parsing/clustering.py:294` | already ranked by `_cluster_rank_key`; `clusters[0] is primary_cluster`. The adapter **reuses this order verbatim** — re-ranking here would be a second, undocumented ranking policy living outside the one file that currently owns ranking |
| `normalizer.normalize(content, provider, limits) -> (list[LogLine], stats)` | `parsing/normalizer.py:72` | re-run once per report to resolve `Diagnostic.evidence` (line numbers) into masked excerpt text — `Diagnostic` itself only carries line numbers, never text (`model.py:176`) |
| `providers.detect_provider(sample)` | `parsing/providers/__init__.py:9` | needed to call `normalize` the same way `pipeline.py:182` does |
| `sanitize.mask` | `parsing/sanitize.py` | the defense-in-depth pass over the serialized response, §6 |

Everything the adapter needs is already exported or reachable via `agentic_pr_analyzer.parsing`
and its submodules. Nothing here required a change to those files.

---

## 3. Raw-log context isolation (hard requirement)

**The raw log body must never enter the calling agent's context.** This is the reason this
section exists rather than the agent just running `cat job.log`. A real CI log is 2,000–20,000
lines (`CLAUDE.md` §7); pasting one into an agent's context wastes the agent's budget on
92% signal-free scrollback and defeats the entire point of having a deterministic parser
upstream of it.

Three mechanics enforce this in v1, all at the **convention** level (agent cooperation, not
sandboxing):

1. **Path-only tool schema.** `analyze_ci_log`'s only parameter is `path: str`. There is no
   `content: str` / `log_text: str` field anywhere in any tool's input schema. An agent
   physically cannot paste a log into the request even if it wanted to — the schema doesn't
   accept it, and the server ignores unknown fields it doesn't validate rather than silently
   accepting an inline body.
2. **The server reads the file entirely server-side.** `open(path).read()` happens inside
   `server.py`, after `paths.resolve_allowed` (§6), and the raw string never crosses back out
   of the process — not in a log message, not in an error payload (a read/decode failure
   returns the exception type and the resolved path, mirroring the `cli.py:91` LEAK-2 pattern
   from `section-7-security.md`, never `str(exc)` verbatim if the exception could embed file
   content).
3. **The response is a tiny tiered summary only** (§4) — bucketed confidence, cluster counts,
   short bounded excerpts, never the full `LogLine` stream, never `to_json(report)` verbatim.
   `to_json` output for a real fixture is tens of KB; the tiered response targets low
   hundreds of bytes per cluster.

Two additional conventions close the gap between "the tool doesn't hand back the log" and "the
agent doesn't go get it another way":

- **Fetch always redirects, never inlines.** Every follow-up tool (`get_cluster_detail`,
  `get_full_report`, §5) takes a `reportId` + selector and returns more *structure*, never the
  underlying raw text. There is no tool anywhere in this surface whose contract is "return
  the log". `get_full_report` returns the full `FailureReport` shape (still all bucketed/
  structured fields) — not `content`.
- **An explicit instruction in the tool description.** `analyze_ci_log`'s MCP tool description
  (the string the agent sees when deciding how to call it) states plainly that the log file
  should not be opened or read directly by the agent — that this tool is the intended way to
  inspect it, and that the file may be arbitrarily large. This is a norm, not an enforcement
  mechanism: nothing stops an agent with a generic file-read tool from opening the path anyway.
  It is the same class of guarantee as a docstring — cheap, real, and worth having, but not a
  boundary.

**What v1 explicitly does not attempt** — named here so it reads as a decision, not an
oversight: an **enforced**-level guarantee would replace the real filesystem path in every
tool response with an opaque server-owned handle (e.g. `reportId` *is* the only handle the
agent ever sees, and the server refuses to accept a raw path from a second, generic
file-reading tool because it never handed one out). That closes the gap the convention-level
approach leaves open. It is real work — it changes what `analyze_ci_log`'s first call looks
like (the agent would need to hand the server a path once, up front, out of band from its own
context, e.g. via an environment variable or a resource root) — and is **parked, not built**,
in §10.

---

## 4. The tiered response contract

### 4.1 Why tiered

An agent debugging a red CI run needs, in order of decreasing likelihood of being needed at
all: (1) is there a primary failure and what kind, (2) what does it say and where, (3) the
full evidence text for that one cluster, (4) everything else. Handing back all four tiers on
the first call defeats the purpose of parsing in the first place — the agent's context fills
with clusters 2 through N it will usually never look at. `analyze_ci_log` returns tiers 1–2 for
every cluster (cheap, bounded) plus tier 3 for the primary cluster only; tiers 3–4 for anything
else are one more tool call away, never paid for up front.

### 4.2 `reportId` and the process-lifetime cache

`reportId = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]` — content-addressed, so
two calls against the same log file produce the same id without needing the server to track a
session or a request counter. `analyze_ci_log` computes it once and caches
`{reportId: (FailureReport, content, list[LogLine])}` in an in-process `dict` for the lifetime
of the server process. `get_cluster_detail`/`get_full_report` look the id up; a miss (server
restarted, or a `reportId` the agent invented) returns a clear "unknown reportId, call
`analyze_ci_log` again" error rather than a stack trace.

The cached `list[LogLine]` is the output of the **one** `normalizer.normalize` call
`analyze_ci_log` already had to make to resolve the primary cluster's excerpt (§4.3) — caching
it means a follow-up `get_cluster_detail` for a different cluster never re-normalizes. No new
dependency: a plain `dict`, no LRU, no TTL. **Explicit ceiling:** this cache is unbounded and
resets on process restart. Fine for the actual usage shape (one CI log, maybe a handful of
follow-up calls, one short-lived agent session per stdio connection) — not fine as a long-lived
multi-repo cache. `# ponytail: unbounded process-lifetime dict; add an LRU cap if a session
ever analyzes enough distinct logs for this to matter — it hasn't, because nothing yet keeps
one MCP server process alive across unrelated CI runs.`

### 4.3 Target JSON shape

```jsonc
{
  "schemaVersion": "1.0",
  "reportId": "3f9a2c7e1b0d4a8c",
  "status": "parsed",                 // "parsed" | "fatal" (report.stats["fatal"])
  "source": {
    "path": "tests/fixtures/raw_logs/pallets/click/12345_67890.log",
    "bytes": 84213,
    "truncated": false                // report.truncated, verbatim
  },
  "summary": {
    "exitCode": 1,                    // report.exit_code
    "clusterCount": 3,                // len(report.clusters)
    "diagnosticCount": 7,             // len(report.diagnostics)
    "jobsFailed": [],                 // see §9 -- always [] in v1, see note below
    "stepsFailed": []                 // see §9 -- always [] in v1, see note below
  },
  "clusters": [
    {
      "clusterId": "c0",              // index in report.clusters, 0-based
      "kind": "test_failure",         // DiagnosticType.value, verbatim
      "confidence": "high",           // bucketed, §9 row 1
      "occurrences": 2,               // 1 + count(DUPLICATE) in related_roles
      "relatedDiagnosticsCount": 4,   // len(cluster.related)
      "primaryDiagnostic": {
        "message": "AssertionError: assert False",
        "file": "tests/test_types.py",
        "line": 288,
        "testId": "tests/test_types.py::test_file_surrogates[type1]",
        "excerpt": "...bounded, masked, first N lines of evidence..."
      }
    }
    // clusters[1], clusters[2] included the same way -- every cluster's
    // primaryDiagnostic ships in tier 1/2; only ONE extra tier (the excerpt)
    // is bounded per-cluster, not omitted for non-primary clusters. See the
    // "omitted" field below for what genuinely doesn't ship.
  ],
  "omitted": {
    "diagnosticsNotShown": 3,         // len(diagnostics) - (diagnostics actually surfaced above)
    "reason": "secondary/consequence/duplicate/summary-role diagnostics inside each cluster are not included in this tier -- call get_cluster_detail(reportId, clusterId) for the full member list, or get_full_report(reportId) for everything."
  }
}
```

`schemaVersion` here (`"1.0"`) is the **adapter's own version**, tracking this JSON shape. It
is a distinct number from the parser's `SCHEMA_VERSION` (`"1.3"`, `parsing/model.py:30`), which
tracks `FailureReport`'s internal shape. The two change independently and must never be
conflated: a consumer bumping expectations for one gets no signal about the other. The parser's
`schema_version` still travels through, unchanged, inside anything `get_full_report` returns.

**`jobsFailed`/`stepsFailed` note**, resolving an apparent tension with §9's mapping table:
§9 documents the general mapping ("from `LogSource` if present, else `[]`"), but §9 also
documents that v1 always calls `parse_log(content, source=None)` — it never loads a `.json`
sidecar into a `LogSource`, on either the fixture-path or the `gh`-fetched-path input. So in
v1's actual behavior, `report.source` is always `None` and both fields are always `[]`. The
shape stays in the contract (so a client doesn't need a breaking change later) but carries no
data yet. `stepsFailed` has a second, independent reason to stay empty even if `LogSource`
were wired in: `LogSource` (`model.py:198`) has no per-step field at all — nothing in this
codebase fetches GitHub's per-step status — so populating it would require inventing a data
source that doesn't exist. Both are explicit v1 limitations, not a leftover TODO.

---

## 5. Tool surface

| Tool | v1 / follow-up | Input | Output | Notes |
|---|---|---|---|---|
| `analyze_ci_log` | **v1** | `{"path": str}` | the §4.3 shape | The only entry point. Computes `reportId`, populates the cache (§4.2), returns tiers 1–2 for every cluster plus the bounded excerpt for the primary cluster only |
| `get_cluster_detail` | follow-up | `{"reportId": str, "clusterId": str}` | one cluster's full member list — every `related` diagnostic with its role, plus that cluster's bounded excerpt | Cache lookup only; no re-parse, no re-read of the file |
| `get_full_report` | follow-up | `{"reportId": str}` | every cluster at `get_cluster_detail` depth, i.e. the closest thing to `to_json(report)` this surface offers — still bucketed/structured, still no raw log text | The "I give up triaging, show me everything" escape hatch. Still bounded per-diagnostic by the same excerpt cap as everything else — it is not `to_json(report)` verbatim |

Deterministic cluster ranking is entirely the parser's (`clustering.py:150`,
`_cluster_rank_key`); none of these three tools re-ranks, re-scores, or re-orders anything.
`clusterId` is assigned purely from `report.clusters`' existing index, so `c0` is always
`primary_cluster` when one exists.

v1 ships only `analyze_ci_log`. `get_cluster_detail`/`get_full_report` are designed here
(shape decided, cache already built to support them) but are a fast-follow, not required for
the first usable tool — an agent working from `analyze_ci_log`'s output alone, for the common
case of a single dominant cluster, already has what it needs.

---

## 6. Security boundary

Three independent controls, none of which is "trust the agent":

1. **Path allow-listing — `paths.resolve_allowed(path: str) -> Path`.** Resolves the input
   against `Path.resolve()`, then checks it falls under a small allow-list of roots (the
   project's `tests/fixtures/raw_logs/` tree, plus a configurable working-directory scope for
   a freshly `gh`-fetched log). Rejects: absolute paths outside the allow-list, `..` traversal
   that escapes it, and anything not ending in a log-shaped extension the server is willing to
   read. This is the one thing standing between "an agent asks the server to parse a CI log"
   and "an agent asks the server to read `~/.ssh/id_rsa` or `.env`" — a real risk once a tool
   accepts an arbitrary path string from an LLM-driven caller. Reuses no new dependency:
   `pathlib.Path.resolve()` + a prefix check, stdlib only.
2. **Masking is already structurally complete at the normalizer choke point** (Section 7,
   `section-7-security.md` §3.2) — every `LogLine.raw_text`/`text`/`marker_body` is masked
   before `LogLine` construction, so every string the adapter reads out of `report.diagnostics`
   or out of the cached `list[LogLine]` is already clean. The adapter does not need to, and
   does not, re-implement masking.
3. **One additional `sanitize.mask` pass over the fully serialized JSON response, as defense
   in depth.** Section 7 (`section-7-security.md` §3.2) explicitly rejected a second
   output-boundary masking pass *for the parser itself*, reasoning that no diagnostic-text
   source exists outside `LogLine`-derived data, so a second pass only duplicates work. That
   reasoning holds for `FailureReport`/`to_json`. It does not fully cover this adapter, because
   this adapter's output boundary is a different, higher-stakes one: the direct, verbatim
   channel into a third-party LLM's context (`section-7-security.md` §2, threat (c) — "fed
   verbatim into a third-party LLM API as context" — except here the "third-party LLM" is the
   calling coding agent itself, arriving earlier and more directly than Slice 4 ever
   anticipated). One `mask(json.dumps(response))` call before writing to stdout is cheap
   insurance against a field this adapter itself introduces (e.g. a path string, a future
   free-text field) that never passed through `normalizer.py`. It is not a substitute for the
   real choke point — it is a second, independent net under it, scoped to exactly the boundary
   that changed with this section (log content reaching an LLM's context directly, not via a
   committed fixture or a future Slice 4 call).
4. **Zero network egress from the server.** `mcp/` imports only from `agentic_pr_analyzer.parsing`
   (and stdlib / the `mcp` SDK). It never imports `agentic_pr_analyzer.github`. This is
   mechanically checkable (a test can assert no `github` import appears in `mcp/*.py`'s
   `ast`-parsed import list) and is listed as a planned test in §8 rather than built now.

---

## 7. Module layout

```
src/agentic_pr_analyzer/mcp/
├── __init__.py    # empty (or a docstring only) -- no package-level re-exports needed;
│                    nothing outside this tree imports from mcp/ today
├── adapter.py      # pure: FailureReport (+ content, + list[LogLine]) -> the §4.3 dict shape.
│                     No I/O, no `mcp` SDK import, no stdio. Independently unit-testable
│                     against a FailureReport built in-memory, same style as
│                     tests/test_parsing_pipeline.py already does
├── paths.py        # resolve_allowed(path: str) -> Path, plus its PathNotAllowedError.
│                     No `mcp` SDK import either -- pure filesystem-boundary logic
└── server.py        # the ONLY file in this tree that imports the `mcp` SDK. Wires
                        stdio transport -> tool schemas -> paths.resolve_allowed ->
                        file read -> parsing.parse_log -> adapter.* -> sanitize.mask ->
                        response. Exposes main(), the pyproject.toml script entry point
                        (`agentic-pr-analyzer-mcp = "agentic_pr_analyzer.mcp.server:main"`)
```

Same separation-of-concerns reasoning as the rest of the codebase (`CLAUDE.md` §4): protocol
code (`server.py`) is isolated from pure transformation code (`adapter.py`) the same way
`cli.py` is kept separate from `parsing/pipeline.py` today. `adapter.py` and `paths.py` being
importable and testable without an `mcp` SDK dependency in the test process is the concrete
payoff — `tests/test_mcp_adapter.py` and `tests/test_mcp_paths.py` (§8) never need to spin up
a stdio server or speak the MCP protocol at all.

Four files, zero touched outside this new tree (this section's `pyproject.toml` change is the
only edit outside `mcp/`).

---

## 8. Fixtures & tests

No new fixture format. `analyze_ci_log`'s natural test input is the same committed fixtures the
parsing engine already validates against — the `pallets/click` anchor
(`tests/fixtures/raw_logs/pallets/click/`) and the `SYNTHETIC/` set (`CLAUDE.md` §4) — so this
section adds zero new fixture files.

Tests before implementation, per `CLAUDE.md` §7 and the standing rule repeated in every prior
section doc:

### `tests/test_mcp_adapter.py` — new, no `mcp` SDK import

| Test | Asserts |
|---|---|
| `test_summarize_shape_matches_contract` | run `parse_log` on the anchor fixture, feed the result through `adapter.summarize`; the returned dict has exactly the §4.3 top-level keys, `clusters` is a list of dicts each with exactly the documented keys |
| `test_confidence_bucketing_matches_thresholds` | the four `confidence.py` constants (0.9, 0.85, 0.6, 0.4) bucket to `high, high, medium, low` — pins the `>=0.85`/`>=0.6` boundary exactly |
| `test_cluster_id_is_index_in_ranked_order` | `clusters[0]["clusterId"] == "c0"` and corresponds to `report.primary_cluster` |
| `test_occurrences_counts_duplicate_role_plus_one` | a cluster with 2 `DUPLICATE`-role related diagnostics reports `occurrences == 3` |
| `test_kind_is_the_raw_diagnostic_type_value` | never fabricates a value absent from `DiagnosticType` — e.g. an `AssertionError` test failure reports `kind == "test_failure"`, not an invented `"assertion_failure"` |
| `test_excerpt_is_bounded_and_masked` | excerpt length is capped (both by line count and by char count); a planted secret in the source line is absent from the excerpt (proves it flows through the already-masked `LogLine.text`, not a re-read of raw content) |
| `test_jobs_and_steps_failed_are_always_empty_in_v1` | pins the §4.3 resolution note — calling `summarize` never populates either, because `source=None` always |
| `test_omitted_reflects_non_surfaced_diagnostic_count` | `diagnosticsNotShown` matches `len(diagnostics)` minus what actually appears across all `primaryDiagnostic` entries |
| `test_fatal_report_status_is_fatal` | a `FailureReport` built with `stats["fatal"] = True` (same shape `pipeline._fatal_report` produces) maps to `"status": "fatal"` |
| `test_schema_version_is_the_adapters_own` | `response["schemaVersion"] == "1.0"`, independent of `report.schema_version` (which is asserted separately to still be `"1.3"` on the same call — proves the two never get conflated) |

### `tests/test_mcp_paths.py` — new, no `mcp` SDK import

| Test | Asserts |
|---|---|
| `test_resolve_allowed_accepts_fixture_path` | a path under `tests/fixtures/raw_logs/` resolves cleanly |
| `test_resolve_allowed_rejects_traversal` | `tests/fixtures/raw_logs/../../.env`-shaped input is rejected before any read is attempted |
| `test_resolve_allowed_rejects_absolute_path_outside_allowlist` | e.g. a path under the user's home directory outside any configured root is rejected |
| `test_resolve_allowed_rejects_non_log_extension` | a `.py`/`.ssh`-shaped target is rejected even if it happens to sit under an allowed root |

### `tests/test_mcp_server.py` — new, exercises `server.py`, the one file that imports `mcp`

| Test | Asserts |
|---|---|
| `test_analyze_ci_log_end_to_end_on_anchor_fixture` | full path: tool call → `paths.resolve_allowed` → read → `parse_log` → `adapter.summarize` → mask pass → response; matches the shape from `test_mcp_adapter.py` |
| `test_analyze_ci_log_rejects_disallowed_path` | tool call with a traversal path returns a tool-level error, not a stack trace and not file content |
| `test_get_cluster_detail_after_analyze_returns_full_members` | a two-call sequence sharing `reportId`; second call does not re-read the file (patch `open`/`Path.read_text` to fail after the first call and confirm the second still succeeds from cache) |
| `test_unknown_report_id_returns_a_clear_error` | `get_cluster_detail` / `get_full_report` with a `reportId` never issued by `analyze_ci_log` — no `KeyError` leaks out |
| `test_mcp_module_never_imports_github` | `ast`-parses every `mcp/*.py` file and asserts no import resolves under `agentic_pr_analyzer.github` — the mechanical version of §6's "zero network egress" claim |
| `test_no_raw_log_text_appears_anywhere_in_the_response` | plant a unique sentinel line in a copy of the anchor fixture with count > excerpt cap; call `analyze_ci_log`; assert the sentinel appears at most as many times as the excerpt cap allows, never as the full original line count — the closest thing to a mechanical check of §3's hard requirement |

`test_mcp_adapter.py` and `test_mcp_paths.py` need no `mcp` SDK import and therefore run even
before the `mcp` dependency is fully wired — the same reasoning that motivated keeping
`adapter.py`/`paths.py` free of the SDK import in the first place (§7).

---

## 9. Reconciliation: adapter shape vs. real `FailureReport`

Produced independently from the tiered-contract design in §4; this is where each field in the
§4.3 shape is pinned to its actual source in the parser's real output, so the mapping is not
re-derived ad hoc inside `adapter.py`.

| Contract field | Real parser output | Adapter mapping |
|---|---|---|
| `confidence: "high"` | `Diagnostic.confidence` float (0.9/0.85/0.6/0.4, parsing/confidence.py) | bucket ≥0.85→high, ≥0.6→medium, else low; raw float kept in drill-down |
| cluster ranking | `report.clusters` already ordered by clustering._cluster_rank_key; clusters[0]==primary_cluster | reuse order verbatim; never re-rank |
| `source:{path,bytes,truncated}` | parser `source` is LogSource git metadata (name clash) | adapter `source` = file provenance; report.truncated→source.truncated; call parse_log(text, source=None) |
| `evidence.excerpt` text | `Diagnostic.evidence` = line numbers only | re-run normalizer.normalize() (masked, deterministic) to resolve numbers→bounded masked excerpt |
| `kind:"assertion_failure"` | DiagnosticType has no such member | kind = DiagnosticType.value; nuance stays in metadata |
| `clusterId/occurrences/relatedDiagnosticsCount` | no ids; roles in related_roles | index in ranked order → cN; occurrences = 1 + count(DUPLICATE); relatedDiagnosticsCount = len(related) |
| `jobsFailed`/`stepsFailed` | LogSource.job_name (CLI path only) | from LogSource if present, else [] (gh-fetched path has no .json sidecar) |

The `source:{...}` row's naming collision is worth restating plainly: `FailureReport.source`
(`model.py:232`) is a `LogSource | None` carrying **GitHub run/job metadata** (owner, repo,
run id, job name, conclusion, sha, `html_url`). The adapter's `source` key in the §4.3 shape
means something completely different — **which local file was read, how big it was, whether
the parser had to truncate it**. Both are legitimately called "source" in their own domain;
the collision is why this row exists at all, and why `adapter.py` must never name a local
variable `source` that aliases both without a comment pointing at this table.

The `evidence.excerpt` row's "re-run" is real, not free: `normalize()` is roughly 77% of
total parse time on the anchor fixture (`section-7-security.md` §6, C6). `analyze_ci_log`
therefore does it exactly once per report and caches the resulting `list[LogLine]` (§4.2),
rather than once per cluster or once per follow-up call.

---

## 10. Out of scope / parking lot

Documented deferrals, matching the project's existing convention (`CLAUDE.md` §8) of writing
down what was deliberately not built rather than letting it silently not exist:

- **Enforced-tier opaque-handle isolation** (§3) — replacing real filesystem paths with
  server-owned handles everywhere, so a generic file-read tool has nothing to act on even if
  the agent ignores the "don't read this file" instruction. Real design work (how does the
  agent hand the server its *first* path without a path field existing at all?) — parked until
  the convention-level guarantee proves insufficient in practice, not built speculatively.
- **A localhost HTTP transport.** stdio is the whole story in v1 (§1). Revisit only if a
  surface that isn't a local coding-agent CLI needs to reach this server — nothing on the
  roadmap does yet.
- **An `analyze_pr(url)` convenience tool** that would fetch and analyze in one call. Explicitly
  rejected by the "no GitHub auth/log-fetch in the server" non-goal (§1) — it would reintroduce
  the `github/` module dependency this section deliberately keeps out, and duplicate what the
  agent's own `gh` invocation already does.
- **CI providers beyond GitHub Actions.** The parsing engine already has a provider seam
  (`GenericProvider` fallback, `CLAUDE.md` §3) for this; the adapter inherits whatever
  `parse_log` supports without change. Adding a new provider is parsing-engine scope, not
  adapter scope.
- **Tier-3/4 automation** — anything where the server acts on the parsed result (opening an
  issue, commenting on a PR, retrying a job). Squarely orchestration-loop territory
  (Slice 5, not started) and explicitly out of scope for a tool-call adapter whose only job is
  answering "what does this log say" when asked.

---

## 11. Implementation steps (ordered)

1. **Land the dependency and script entry** (this section's `pyproject.toml` change) —
   already done; `mcp` resolves via `uv add mcp`.
2. **Write `tests/test_mcp_paths.py` first** (§8, all fail — `paths.py` doesn't exist).
   Implement `paths.resolve_allowed` until green.
3. **Write `tests/test_mcp_adapter.py` first** (§8, all fail). Implement `adapter.summarize`
   (and the excerpt-resolution helper it needs) against the anchor fixture until green. No
   `mcp` SDK import touched yet.
4. **Write `tests/test_mcp_server.py` first** (§8, all fail — `server.py` doesn't exist).
   Implement `server.py`: stdio transport setup, `analyze_ci_log` tool registration wired to
   `paths` + `parsing.parse_log` + `adapter.summarize`, the final `sanitize.mask` pass, the
   process-lifetime cache (§4.2), and `main()`.
5. **Manually smoke-test over real stdio** against the anchor fixture with an actual MCP
   client (or the SDK's own test harness) before calling v1 done — the automated tests exercise
   the tool functions directly; one real end-to-end stdio round-trip catches transport-layer
   issues the unit tests structurally cannot.
6. **Fast-follow: `get_cluster_detail` / `get_full_report`** (§5) — same TDD order, reusing the
   cache §4.2 already built for them.
7. **Update `CLAUDE.md` §2/§3/§4`** to record Section 8 as done, add the `mcp/` module tree to
   the repository layout, and note the new `agentic-pr-analyzer-mcp` entry point — the standing
   rule from `CLAUDE.md` §2 applies here exactly as it did for Sections 1–7.

---

## 12. Acceptance criteria

1. `uv run pytest` green, including the three new `test_mcp_*.py` files; zero existing test
   modified.
2. `analyze_ci_log` on the `pallets/click` anchor fixture returns a response matching the
   §4.3 shape exactly, with `clusters[0]` corresponding to `report.primary_cluster`.
3. No planted secret, and no full raw log line beyond the excerpt cap, appears anywhere in any
   tool's response — mechanically checked by
   `test_no_raw_log_text_appears_anywhere_in_the_response`.
4. `paths.resolve_allowed` rejects every traversal/outside-allowlist/wrong-extension case in
   §8's table; none of them reaches `open()`.
5. `mcp/*.py` contains zero imports resolving under `agentic_pr_analyzer.github` — mechanically
   checked, not just asserted in prose.
6. `adapter.py` and `paths.py` import cleanly and are fully testable in a process that never
   imports the `mcp` SDK.
7. The adapter's `schemaVersion` (`"1.0"`) and the parser's `SCHEMA_VERSION` (`"1.3"`) are
   never conflated in code, tests, or this document — each call site names which one it means.
8. No change to any file under `parsing/`. This section is additive, not a Section 1–7
   modification.
9. `CLAUDE.md` §2 records Section 8 as done, per the standing rule already governing Sections
   1–7.
