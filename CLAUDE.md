# CLAUDE.md

This is the fast-orientation file for this repo — what to read first to know what this project is, what's built, and what rules to follow while working on it. For the full design rationale, roadmap, and reasoning behind every decision below, see [`docs/project-brief.md`](docs/project-brief.md); this file summarizes and links rather than re-explaining it, so a changed detail only ever needs updating in one place.

## 1. Project summary

`agentic_pr_analyzer` is an agentic AI tool that explains *why* a GitHub CI build/PR is red, proposes fixes, and — eventually — predicts merge breakage before it happens, by reading CI logs and PR diffs, reasoning over them, and remembering what it learned per repo. It's built as a public, production-grade portfolio project: discipline and small, verifiable increments matter as much as raw function. Full context: [`docs/project-brief.md`](docs/project-brief.md).

## 2. Current status

**Phase 1, Slice 0 (dev access) + Slice 1 (GitHub CI ingestion): done. Slice 2 (log-parsing engine): ALL 7 sections done.**

- [x] Slice 0 — repo contract: `.env`/`.env.example`, `.gitignore`, config loader, GitHub client, `verify` CLI command working against the live API.
- [x] Slice 1 — GitHub CI ingestion: auto-discovers the latest failed workflow run, fetches failed job logs, saves them as committed fixtures (`tests/fixtures/raw_logs/<owner>/<repo>/`). `ingest` CLI command working against the live API.
- [x] Slice 2 — deterministic log-parsing engine. **Contract change (2026-08-24):** the former compact "Slice 2 parser" is superseded by a full production-grade engine (`src/agentic_pr_analyzer/parsing/`), built section by section — still deterministic, LLM-free, stdlib-only:
  - [x] Section 1 — foundation & engine contracts: canonical model (`model.py`), provider seam (`GitHubActionsProvider` + `GenericProvider`), normalization + segmentation, `ParseLimits`, total-function guarantee (never raises), basic secret masking, parser registry holding only `GenericParser`. `parse` CLI command working against a saved fixture, no token/network needed.
  - [x] Section 2 — Python/pytest parser: `parsers/pytest_parser.py`, validated against the committed `pallets/click` anchor fixture (`tests/test_types.py::test_file_surrogates[type1]`, primary cluster, `report.exit_code == 1` preserved).
  - [x] Section 3 — stack-trace & test-runner abstractions across ecosystems: shared `stacktrace.py` (`parse_python_traceback`/`parse_js_stack`/`primary_frame`) and `model.TestOutcome` taxonomy, proven by a single `JsTestParser` covering both jest and vitest (synthetic fixtures under `tests/fixtures/raw_logs/SYNTHETIC/`, clearly marked — no real red jest/vitest run captured yet; swap in a real `ingest` capture when one is seen). `SCHEMA_VERSION` bumped 1.0 → 1.1 (additive: `TestOutcome`). Exit-code extraction refactored out of `GenericParser` into shared `process_failure.py` so specialized parsers can co-emit the same PROCESS_FAILURE without the registry's fallback-suppression dropping it.
  - [x] Section 4 — compiler & static-analysis diagnostics: `parsers/compiler_parser.py`, one `CompilerParser` covering tsc (`COMPILER_ERROR`, both `--pretty`/non-pretty shapes) and eslint (`LINT_ERROR`, "stylish" reporter), mirroring `JsTestParser`'s "one class, two tools" shape. Error code/rule id go in `metadata["code"]`/`metadata["rule"]` — no schema change, `SCHEMA_VERSION` stays 1.1. Registered in `PARSER_REGISTRY` ahead of `GenericParser`; co-emits `PROCESS_FAILURE` via `find_process_failure`. gcc/clang/rustc/javac remain deferred additive parsers. Synthetic fixtures under `tests/fixtures/raw_logs/SYNTHETIC/{tsc,eslint}-sample/` (no red public tsc/eslint run captured yet).
  - [x] Section 5 — correlation, deduplication & failure clustering: new `parsing/clustering.py` (`normalize_message`, `norm_path`, `dedup_key`, `build_clusters`) replaces `pipeline.py`'s placeholder one-cluster-per-diagnostic stage. Rule ladder S1/C1/C2a/C2b/C3 with guard G1 (two different non-None `test_id`s never share a cluster), plus a pass-3 attach of every `PROCESS_FAILURE` as a `CONSEQUENCE` of the best-ranked cluster. `SCHEMA_VERSION` 1.1 → 1.2 (additive: `DiagnosticRole`, `FailureCluster.related_roles`/`.key`). Clustering is **non-lossy** — dedup is expressed as cluster membership + role, never by dropping from `report.diagnostics`. Four new SYNTHETIC fixtures (multi-test, duplicate-sections, cascade, summary-echo). **No rule keys on `section_id`**: it is `null` for every diagnostic in every committed fixture, so such a rule could never fire.
  - [x] Section 6 — scale & robustness: `_bounded_splitlines` + `_LINE_BREAK_RE` reproduce `str.splitlines()`'s exact 11-boundary set while stopping at `max_total_lines`, so peak memory tracks the limit rather than the input; new `ParseLimits.max_total_chars` (100 MB safety valve); `LogLine` gains `slots=True`; the dead `max_context_lines` is wired into a central head-first evidence cap in `pipeline.py`; head truncation gains a bounded tail rescue for the exit code. `SCHEMA_VERSION` **stays 1.2** — no field or enum member added, new keys land in the already-free-form `stats` (the Section 4 precedent). **Streaming is DECLINED, not deferred** (see §8). Baselines in [`docs/perf-baselines.md`](docs/perf-baselines.md).
  - [x] Section 7 — security & observability hardening: `sanitize.py` goes from 3 rules to a 13-rule ordered matrix with `mask_counted`/`MASK_TOKENS`/`SECRET_CLASSES`; masking stays at the single `normalizer.py` choke point, now with ANSI stripped before masking (an escape ends in a word character, which suppressed the word-boundary assertion every token rule anchors on — a live leak via `marker_body`). `pipeline.py` gains `_base_stats()`: success and fatal paths emit an identical 24-key set, with all non-deterministic readings nested under one reserved `stats["runtime"]` key that the golden snapshot drops by name. `SCHEMA_VERSION` 1.2 → 1.3. **Entropy heuristics are rejected outright** (they would mask git SHAs, UUIDs, wheel hashes and cache keys — precisely the evidence the engine exists to preserve). **Confidence calibration is NOT in this section** — see the re-scoping note below.

> **Re-scoping note (reconciliation C8).** [`docs/project-brief.md`](docs/project-brief.md) lists *confidence calibration* under Section 7. It was **not** built: Section 7 covers security + observability only and leaves `confidence.py` untouched. Calibration needs either its own increment or a documented drop, and it should be decided against Slice 6's labeled corpus rather than guessed now. Flagging a dropped deliverable beats silently shipping seven of eight items.
- [x] MCP adapter (Phase 1 plan, Section 8) — the deterministic parsing engine exposed as a stdio MCP server for coding agents, built this session: `src/agentic_pr_analyzer/mcp/` (`adapter.py`, `paths.py`, `server.py`), path allow-listing via `paths.resolve_allowed`, the stdio server with all three tools (`analyze_ci_log`, `get_cluster_detail`, `get_full_report`), the `agentic-pr-analyzer-mcp` console script, and matching tests (`tests/test_mcp_*.py` per the plan's §8, plus `tests/test_mcp_fetch_discipline.py` guarding the recipe below). **Not done — documented as manual/fast-follow, not overclaimed as shipped**: the Codex `~/.codex/config.toml` registration snippet is written down for a developer to apply themselves (`AGENTS.md` §10), and the Claude Code `.mcp.json` + `/ci-doctor` skill fast-follow is not yet built. Full design: [`docs/plans/section-8-mcp-adapter.md`](docs/plans/section-8-mcp-adapter.md).
- [ ] Slice 3 — diff extraction and correlation (not started)
- [ ] Slice 4 — one-shot LLM baseline (not started)
- [ ] Slice 5 — agent loop (not started)
- [ ] Slice 6 — evaluation harness (not started)
- [ ] Slice 7 — fix generation (not started)
- [ ] Slice 8+ — memory, dependency checking, private-repo support, OAuth, MCP, web UI, deployment, polish (not started)

**Standing rule: update this checklist as part of the commit that completes each slice/section.** It must never go stale — a future session (including a future instance of Claude Code) reads this section first to know where the project actually stands.

## 3. Architecture

Six layers, bottom-up (full rationale in the brief §3):

1. **Data layer** — pull CI logs and PR diffs from the GitHub Actions API. *(built: `github/client.py`, `github/ingestion.py`)*
2. **Core engine (the moat, zero AI)** — deterministic log-parsing engine (`parsing/`, 7 sections), diff correlator, breakage predictor, context assembler. *(in progress — parsing engine Sections 1-3/7 done; diff correlator+ = Slice 3+)*
3. **Model layer** — a cheap LLM API turns clean context into judgment (cause + fix). *(not started — Slice 4)*
4. **Orchestration loop** — hand-rolled plan → act → verify agent loop. *(not started — Slice 5)*
5. **Surfaces** — web app (primary), MCP server (secondary). *(not started)*
6. **Eval harness** — labeled test set + scoring, the thing that makes this a hero project and not a demo. *(not started — Slice 6, though Slice 1's fixtures already feed it)*

## 4. Repository layout

```
src/agentic_pr_analyzer/
├── __init__.py       # thin re-export of cli.main
├── cli.py             # argparse CLI: verify, ingest, parse
├── config.py           # .env loading, GITHUB_TOKEN
├── exceptions.py        # typed exception hierarchy
├── github/
│   ├── client.py          # authenticated GitHub REST wrapper (data layer)
│   ├── models.py            # RawLog evidence model + save_raw_log/load_raw_log disk seam
│   └── ingestion.py           # resolve failed run -> fetch logs -> save fixtures
└── parsing/              # deterministic log-parsing engine (Slice 2, built section by section)
    ├── model.py              # canonical dataclasses/enums + to_dict/to_json, SCHEMA_VERSION, TestOutcome
    ├── limits.py              # ParseLimits (bounds + defaults)
    ├── sanitize.py             # 13-rule ordered secret-masking matrix + mask_counted/MASK_TOKENS (Section 7)
    ├── confidence.py            # deterministic confidence rule table (constants, never model-derived)
    ├── normalizer.py             # raw text -> LogLine list (dual raw_text/text, ANSI/timestamp/marker)
    ├── segmentation.py            # LogLine list -> LogSection tree (group/endgroup nesting)
    ├── stacktrace.py               # Section 3: parse_python_traceback/parse_js_stack + primary_frame, shared
    ├── process_failure.py           # shared `##[error]...exit code N` -> PROCESS_FAILURE (extracted from GenericParser)
    ├── clustering.py                 # Section 5: dedup_key + correlation rules -> FailureCluster tree
    ├── pipeline.py                   # parse_log(): stages + ParseLimits + total-function guard + _base_stats
    ├── providers/                     # LogProvider seam: github_actions.py, generic.py fallback
    └── parsers/                        # registry: pytest_parser.py, js_test_parser.py (jest+vitest), compiler_parser.py (tsc+eslint), generic_parser.py
tests/
├── secret_examples.py  # the 13 planted example literals + negative controls, shared by 4 test files
├── test_config.py, test_github_client.py, test_ingestion.py, test_load_raw_log.py, test_cli_parse.py
├── test_parsing_*.py   # providers/normalizer/sanitize/segmentation/confidence/limits/stacktrace/
│                         generic_parser/pytest_parser/js_test_parser/pipeline/golden-snapshot/fuzz-security
│                         + clustering/correlation-fixtures (S5), scale + perf (S6),
│                         stats/secret-leak-channels/fixture-secret-policy (S7)
└── fixtures/
    ├── raw_logs/<owner>/<repo>/<run_id>_<job_id>.{log,json}  # real captured fixtures, committed
    ├── raw_logs/SYNTHETIC/                                    # hand-written jest/vitest fixtures, clearly marked (see its README)
    └── parsed/<owner>/<repo>/<run_id>_<job_id>.json            # golden-snapshot FailureReport JSON
docs/project-brief.md   # full design doc
```

Module boundaries are deliberate: GitHub API access, evidence models, and ingestion orchestration are three separate files so the parser and correlator (Slice 3) can plug in without touching this code. Within `parsing/`, the provider seam and parser registry are themselves separable so Sections 2–7 add ecosystems/stages behind the frozen `parse_log`/`to_json`/`FailureReport`/`ParseLimits` interfaces, with no core rewrite.

## 5. Development setup

```bash
uv sync
cp .env.example .env   # then add your own GITHUB_TOKEN — see .env.example for scope guidance
uv run agentic-pr-analyzer verify
uv run agentic-pr-analyzer ingest <owner>/<repo>
uv run agentic-pr-analyzer parse <path-to-saved-.log-fixture>
uv run pytest
```

## 6. Operating rules for Claude Code

Carried forward from the brief (§2.1) — these are operating constraints, not suggestions:

- Read the repo before changing it; work in small vertical increments that leave the project runnable and testable.
- Preserve architectural boundaries: GitHub API integration / ingestion / deterministic parsing / evidence models / orchestration / evaluation stay separable modules.
- Tests before expansion — every deterministic behavior in the parser or GitHub integration gets a regression test or fixture.
- No hidden behavior — don't introduce an LLM, framework, background worker, database, or dependency "because it's convenient." Every addition needs a stated project-level reason.
- No secret exposure — tokens/keys live only in `.env`, never hardcoded, committed, printed, or placed in model context.
- Prefer the standard library and existing dependencies; keep the dependency surface deliberately small.
- Explain non-obvious decisions in code/docs — the implementation should be understandable without the AI assistant present.
- Don't silently change the plan — document the tradeoff (here and in the brief) if implementation forces a real architectural deviation.
- No GitHub OAuth/GitHub App auth in Phase 1 — a local read-only PAT via `.env` is the whole story for now.
- No auto-commit, ever — the agent diagnoses and proposes; a human applies fixes and reviews commits.

## 7. Testing conventions

- Unit tests are mocked/offline by default (`uv run pytest`) — no network access, no real token needed.
- Perf tests use the `perf` marker and are excluded by default (`addopts = "-m 'not integration and not perf'"`); run explicitly with `uv run pytest -m perf -s`. They generate multi-MB logs at test time from a seeded generator and need ~1 GB free. **Zero perf fixtures are committed.** Assertions are generous absolute ceilings plus one machine-independent *ratio* (`t50/t10 < 8`) — a tight wall-clock assertion would be flaky on another machine, and the first response to a flaky perf test is to delete it.
- `stats["runtime"]` is the ONE bucket holding wall-clock/host-dependent readings. The golden-snapshot comparison drops that single key **by name** (never by a `*_ms` suffix convention, which would silently swallow future fields), and its *shape* is asserted separately so a bug that drops timing entirely still fails.
- Real-network tests use the `integration` pytest marker and are excluded by default (`addopts = "-m 'not integration'"` in `pyproject.toml`); run explicitly with `uv run pytest -m integration`.
- `GitHubClient` is tested by injecting a mocked `requests.Session` via its constructor — no HTTP-mocking dependency needed.
- Fixtures under `tests/fixtures/raw_logs/` are real logs captured by actually running `agentic-pr-analyzer ingest` against a real public repo — not synthetic data, with one deliberate exception: `tests/fixtures/raw_logs/SYNTHETIC/` (Section 3's jest/vitest fixtures), hand-written and clearly marked because no red public jest/vitest run was captured yet — replace with a real `ingest` capture the first time one is seen.
- The parsing engine's golden-snapshot fixture lives at `tests/fixtures/parsed/<owner>/<repo>/<run_id>_<job_id>.json`. Regenerate it (after an intentional output-format change, never to paper over a real regression) with the one-liner documented at the top of `tests/test_parsing_golden_snapshot.py`.
- **Fixture-vetting policy (Section 7).** P1 — a real capture may be committed under `tests/fixtures/raw_logs/<owner>/<repo>/` **only** if its source workflow run is readable by an anonymous, unauthenticated GitHub user; those bytes are already world-readable, so committing them creates no new exposure. P2 — never commit a capture from a private repo, a self-hosted runner, or a repo you do not control; `tests/fixtures/raw_logs/PRIVATE/` is gitignored as a deliberate escape hatch. P3 — `tests/test_fixture_secret_policy.py` scans every committed `*.log` and `*.json` with the shipped matrix and fails on any match that is not GitHub's own `***` or an obviously-fake synthetic literal. It is a **shape** rule, so there is no per-file allowlist to go stale. P4 — **the human step, not automated**: P3 cannot see an internal hostname, a customer name, or an employee email. Read the diff of a new fixture before committing it.
- Write tests before implementing a new section/slice. If a test fails after implementation, the default assumption is the implementation is wrong; only change the test once you've confirmed the test itself was asserting the wrong thing, not merely inconvenient for the code as written.

## 8. Explicitly out of scope right now

Documented deferrals, not oversights — don't build ahead of the current slice:

- gcc/clang/rustc/javac compiler diagnostics — deferred as additive parsers (Section 4 shipped tsc + eslint only). Go/Java/Rust/C++ stack traces are likewise deferred (`stacktrace.py` is shaped so each is an additive `parse_<lang>_stack` function, not a refactor).
- **Streaming / chunked parsing — DECLINED, not deferred (Section 6, D1).** `PytestParser` back-slices arbitrarily, `JsTestParser` needs a full prior pass for tool detection, `find_process_failure` keeps `matches[-1]` so it needs EOF, and the cluster builder needs random access by line number. Streaming would rewrite every parser plus the registry contract, which §6 forbids — to solve a problem that does not exist (a 100 MB log already parses in seconds under default limits; real job logs are single-digit MB). Worse, a stream fed by line iteration **cannot reproduce `str.splitlines()`'s boundary set**, so it would be non-deterministic against `parse_log` on exactly the degenerate inputs it was meant to handle. Reopen only if a real job log exceeds `max_total_chars`. The `parse_log_lines(Iterable[str])` seam is written down in the plan; build it when a caller genuinely streams.
- **`SourceRange` byte offsets — DECLINED, deferral closed (Section 6, D6).** Not derivable from `splitlines()` output, and they would be the only field in a coordinate system no other field shares. Upgrade path is an on-demand `line_byte_offsets(content)` side table, not 2 ints on every range. The full argument is in `model.py`'s `SourceRange` comment.
- **Head+tail truncation with a gap sentinel — REJECTED (Section 6, D5).** Every parser assumes list adjacency implies log adjacency; a sentinel would silently corrupt evidence spans across four parsers. Only the bounded exit-code tail rescue ships.
- **Entropy-based secret heuristics — REJECTED outright (Section 7, §3.4),** not deferred pending effort. On the anchor fixture a canonical entropy rule masks six action-pin SHAs, two UUIDs, every wheel hash and the cache key. Any revival must be opt-in, default-off, and exclude hex/UUID shapes.
- **Cross-line PEM body masking** — rule 2 masks the armor header only; the base64 body is not recognizable per-line without cross-line state. Marked in `sanitize.py` with a `ponytail:` comment naming the ceiling.
- **Masking matrix rules 14-16** (credential-free DB DSNs, Azure/GCP service-account JSON, Twilio/SendGrid/Datadog/Vault, generic `?apikey=`) — each is one tuple entry plus a test pair. Add when a real capture shows one, not because a category list exists.
- **Real memory measurement (RSS / `tracemalloc` in-process)** — declined; `resource` is Unix-only and this repo is developed on Windows, and `tracemalloc` costs ~2.6x parse time. The deterministic proxies `bytes_processed` and `peak_lines_retained` ship instead; real RSS can go into the existing `stats["runtime"]` bucket with no test changes.
- **`hypothesis` as a dev dependency** — declined against §6's small-dependency rule. The properties here are differential over a tiny alphabet, where seeded stdlib `random` is near-exhaustive. Revisit if a section needs stateful or multi-argument properties.
- **Confidence calibration** — moved out of Section 7; see the re-scoping note in §2.
- Diff extraction, PR-diff correlation, any LLM/AI calls, autofixes (Slices 3–4+).
- GitHub Actions CI for this repo itself — technically possible now (`origin` already points at `github.com/glavin12/pr_analyzer`), but it's polish-checklist scope (brief §9), not required by any current slice.
- OAuth/GitHub App auth — explicitly v2 per the brief's first implementation rule.
- `ruff`/`mypy` tooling — not required for current functionality.
- Automatic retry/backoff on GitHub API errors — that's orchestration-loop-shaped behavior for Slice 5, not this deterministic ingestion code.
- Pagination beyond the most recent failed run — Slice 1 only needs the single latest failure.

## 9. Full design doc

See [`docs/project-brief.md`](docs/project-brief.md) for the complete architecture rationale, all key decisions and *why*, the full slice-by-slice build order, timeline, and the production-grade checklist.
