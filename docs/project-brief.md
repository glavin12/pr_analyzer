# CI Diagnosis Agent — Project Brief & Research Doc

> An agentic AI tool that explains **why a build/PR is red**, **proposes fixes**, and **predicts merge breakage before it happens** — by reading CI logs and code changes, reasoning over them, and remembering what it learned per repo.

**Status:** design complete, build starting (repo initialized, structure in place).
**Goal:** a public, production-grade "hero project" for landing agentic-AI internships/jobs, with a path to becoming a real product.

---

## 1. The Problem & Why This Project

**The problem.** Builds break constantly, and figuring out *why* is slow and painful: you dig through thousands of lines of CI logs to find the one real error, connect it to whatever the PR changed, and guess at a fix. Dependency conflicts and version mismatches are among the most common causes.

**Why agentic (not a chatbot).** The value is in the AI *doing* multi-step work — fetching logs, parsing, correlating, reasoning, verifying, remembering — not answering a single question. If a one-shot chatbot could solve it, it wouldn't be an agentic project.

**The north star — "not another wrapper."** The criticism "just another GitHub/LLM wrapper" is about *depth*, not domain. A wrapper is `LLM + prompt + chat box`. This project avoids that by putting the hard engineering where wrappers skip it: log parsing, correlation, self-verification, evals, and reliability. The domain being crowded is nearly irrelevant — depth is what separates a toy from a hero project.

---

## 2. Core Design Principles

These recur through every feature. Consistency across them is itself the signal of a well-designed system.

- **Facts vs judgment (the central split).** Deterministic code gets ground truth (what's in the log, what versions conflict, what changed). The LLM only *reasons over already-verified facts*. Never ask the model for facts it will hallucinate (versions, what exists) — only for judgment (what the error means, how to fix it).
- **Build vertical slices.** Get the ugliest possible full path working end-to-end first, then deepen each layer. Never build layers in isolation and assemble at the end — that's how projects stall. Always have something that runs.
- **Human-in-the-loop.** The agent diagnoses and proposes; the *user* applies the fix. Never auto-commit in v1. This is both a trust decision and (conveniently) what sidesteps the write-access/auth problem.
- **Least privilege.** Request read-only access, only the scopes needed. Low-permission tools get adopted; "give me write access to your repo" gets closed instantly.
- **Production-grade = disciplined, not big.** A small, clean, well-tested repo reads as more senior than a sprawling one. Restraint is the signal. Bake quality in per-commit; don't bolt it on at the end.

---

## 2.1 Current Implementation Mode — Claude Code

**Implementation environment:** Claude Code is the primary coding assistant for the build. The architecture and engineering standards remain ours; Claude Code is an implementation tool, not an architectural decision-maker.

### Rules for Claude Code

- **Read the repository before changing it.** Inspect the existing tree, configuration, tests, and current implementation before proposing edits.
- **Work in small vertical increments.** Each change should leave the project runnable and testable. Avoid large speculative rewrites.
- **Preserve architectural boundaries.** GitHub API integration, ingestion/normalization, deterministic parsing, evidence models, orchestration, and evaluation should remain separable.
- **Tests before expansion.** Every deterministic behavior added to the parser or GitHub integration gets a regression test or fixture.
- **No hidden behavior.** Do not introduce an LLM, framework, background worker, database, or dependency merely because it is convenient. Each addition needs a project-level reason.
- **No secret exposure.** GitHub tokens and model keys stay in `.env`; never hard-code them, commit them, print them, or place them in model context.
- **Prefer the standard library and existing dependencies when sufficient.** Keep the dependency surface deliberately small.
- **Explain non-obvious decisions in code/docs.** The goal is to make the implementation understandable without Claude Code being present.
- **Do not silently change the plan.** If implementation reveals a genuine architectural issue, document the tradeoff and update the project brief before making a major change.
- **Human review remains required for consequential changes.** Claude Code may implement authorized changes, but it does not get autonomous authority to merge, deploy, rotate credentials, or grant permissions.

### First implementation rule

We will **not build GitHub OAuth/GitHub App authentication first**. Phase 1 uses a local **read-only GitHub token/PAT** for development. Product-grade OAuth/GitHub App authorization is a later surface concern. This keeps authentication from blocking the deterministic CI-analysis work.

---

## 3. Architecture — The Layered Stack

Data flows bottom-up: raw inputs → deterministic engine (free, your moat) → rented LLM (judgment) → surfaces → user. The eval harness sits alongside, measuring the whole thing.

**1. Data layer — the raw material.**
Pull CI logs and PR diffs from the GitHub Actions API. (*CI* = the robot that runs tests on every push; "red" = that run failed. *API* = how your code asks GitHub for data instead of clicking the site.)

**2. Core engine — the moat (plain code, zero AI).**
Four deterministic jobs:
- *Log parser* — extract the real error from thousands of noisy lines. (This is the single most differentiating piece.)
- *Diff correlator* — link the changed file to the broken test.
- *Breakage predictor* — static analysis of what a PR touches vs what depends on it (no run needed).
- *Context assembler* — package clean, minimal context for the model.
Deterministic = same input → same output → testable, free, and the actual hard engineering.

**3. Model layer — the reasoning (rented).**
A cheap LLM API takes the clean context and gives judgment: likely cause + proposed fix. Models bill by *tokens* (~word chunks) and are *stateless* (resend context each call). Renting = no cost to build a "brain."

**4. Orchestration loop — YOUR agent (the hireable part).**
The logic tying tools + model together. Key concepts:
- *Tool calling* — you describe your engine's functions to the model; it replies "call parse_log with this id"; your code runs it and feeds the result back.
- *Agent loop* — repeat think → act → observe until done.
- **Plan → act → verify** (the shape that isn't a wrapper): form a hypothesis → gather evidence with tools → **check the hypothesis against the real log** → after proposing a fix, verify it actually addresses the identified error → recover if a tool returns junk or the hypothesis was wrong. Must have a termination condition (don't loop forever).

**5. Surfaces — how it's used.**
- *Web app (surface #1, primary):* paste a PR link, watch it diagnose live. The visceral demo that lands in interviews. Backend = FastAPI; frontend = Streamlit first (minimal), React later if time.
- *MCP server (surface #2, cheap add-on):* the same core exposed as tools any MCP host (Claude Code, Codex, Cursor) can call. Buys the composability story + the "MCP" resume keyword.

**6. Eval harness — what makes it a hero project.**
A labeled test set + scoring + a real number you can move. Two kinds:
- *Tool evals (free, constant):* saved real logs as fixtures — does the parser extract the right error? Tests your code deterministically, zero LLM calls.
- *End-to-end evals (cheap, occasional):* ~20–30 real failing PRs — does the full agent nail the diagnosis? Needs a host model.
The sentence that gets interviews: "improved diagnosis accuracy 41% → 68% by adding a verification node."

---

## 4. Features (current + planned)

- **Diagnosis (why red).** Core. Root-cause from logs + diff.
- **Fix proposal — 3-rung formatter (by confidence/complexity).**
  1. *Deterministic/safe* (version pin, typo, missing import) → real unified `diff`, one-click apply / downloadable `.patch`.
  2. *Likely, needs judgment* → explanation + suggested diff, framed as a suggestion.
  3. *Too involved to patch safely* → a ready-to-paste **handoff prompt** for the user's own Claude Code/Cursor, pre-loaded with the context you extracted.
  The agent *grades its own certainty* to pick the rung — an impressive, senior behavior. Diff is the workhorse format.
- **Merge/breakage prediction (before merge).** Different trigger, different data (no failing build yet — static analysis of what the PR touches vs its dependents). Build *after* diagnose+fix.
- **Memory (per-repo learning).** Store **learnings, not raw logs** (raw logs mislead — similar error text, different root cause). After a *confirmed* diagnosis, a write-memory node persists a compact structured learning; at run start, a retrieve-memory node pulls relevant past learnings for that repo. **v1 = structured store keyed by `(repo, error_type)` in SQLite/JSON — no embeddings.** v2 = add vector/semantic retrieval only where the structured filter falls short. Hard parts: only save confirmed successes; handle staleness (learnings from before a refactor may be wrong — version them, let newer evidence override).
- **Dependency check.** A `check_dependency_conflict` tool *inside* the loop (not a separate product). Deterministic: parse manifests, run the real resolver (pip/uv, npm), query the registry (PyPI/npm) **live** for what exists, hit an advisory DB (OSV) for vulnerabilities. The LLM only reasons over these verified findings. Full "scan & recommend upgrades across the tree" = v2 / separate project (that's Dependabot/Renovate/Snyk territory).

---

## 5. Key Decisions & Rationale (decisions log)

*(This section doubles as README material later.)*

| Decision | Choice | Why |
|---|---|---|
| Web app vs MCP first | **Web app first**, MCP as surface #2 | Web app is the visceral demo *and* makes YOU build the orchestration (the hireable skill); MCP hides the loop in the host. Shared core → MCP is a cheap add-on. |
| Framework for the loop | **Hand-roll** plan→act→verify; reach for LangGraph only if state gets hairy | "I built the orchestration myself" reads deeper than "I wired up LangGraph nodes" and dodges the wrapper critique. Proving you don't *need* the framework is the stronger flex. |
| Memory storage | **Learnings, not logs**; structured store before vector RAG | Embedding noisy logs gives misleading neighbors. Per-repo scale is dozens of items → a filter, not fuzzy search. Knowing when *not* to use RAG is the senior move. |
| Dependency/version facts | **Deterministic tooling** (resolver + live registry), not the LLM | Version compatibility has an exact answer; LLMs hallucinate it from stale training data. Wrong tool for facts. |
| Writing fixes | **Read-only; user applies the fix** | Sidesteps auth entirely, safer, more adoptable, and demonstrates human-in-the-loop maturity. |
| Private-repo access | **PAT (read-only) v1**, OAuth/GitHub App v2 | PAT unlocks private repos in hours with no OAuth machinery. OAuth is free but fiddly (callback, token refresh, storage) — a v2 complexity decision, not a cost one. |
| Hosting | **Later**, and simple hosts (Railway/Render/Fly) over AWS | Nothing to host until it runs locally. AWS free tier has footguns (12-mo vs always-free, auto-scaling bills). Local demo + screen recording is often more impressive anyway. |
| Model choice | Cheapest model that **tool-calls reliably** within budget | For agents, capability matters as much as price — cheap models often tool-call unreliably, tanking end-to-end success. Note the tradeoff in the writeup. |

---

## 6. Tech Stack (with why)

- **Language:** Python — ecosystem for GitHub APIs, LLM SDKs, parsing.
- **GitHub access:** PyGithub or raw HTTP requests. Public repos = no auth; private = read-only PAT.
- **LLM:** a cheap API model via its SDK (verify current model names/prices on the provider's page — they move monthly). Keys in `.env`.
- **Backend:** FastAPI (lightweight Python web server).
- **Frontend:** Streamlit first (Python → UI, minimal frontend code); React only if time allows.
- **Memory:** SQLite or JSON (v1); pgvector/Chroma (v2, only if needed).
- **Dependency tooling:** pip/uv resolver, PyPI/npm registry APIs, OSV advisory DB.
- **MCP:** the MCP Python SDK (surface #2).
- **Evals:** roll-your-own harness, or LangSmith (pairs with LangGraph).
- **Diffs:** generate clean unified diffs (`git apply`-compatible).

---

## 7. Build Order — The Slices

Each slice is a working, testable vertical increment. **The first milestone is intentionally deterministic.** We will not let agent orchestration, OAuth, UI work, or framework setup block the core diagnosis pipeline.

### Slice 0 — development access and repository contract

Use Claude Code against the initialized repository. Add `.env`/`.env.example`, `.gitignore` protection, basic project configuration, and a **read-only GitHub PAT for local development**. Verify authenticated API access with the smallest possible request. **Do not build OAuth/GitHub App authorization yet.**

*Done =* the local environment can safely call the GitHub API without exposing credentials.

### Slice 1 — GitHub CI ingestion

Automatically resolve a completed GitHub Actions workflow run → identify failed job(s) → fetch the raw job log through the API → save representative real logs as fixtures. The finished system must discover `run_id` and `job_id`; neither should be manually supplied by the user.

For V1 development, use a GitHub `workflow_run`-based trigger or an equivalent local/manual run resolver. The important boundary is `GitHub API → RawLog`; webhook infrastructure is not the goal yet.

*Done =* a real failed workflow can be converted into a local raw-log fixture without manual copying.

### Slice 2 — deterministic log-parsing engine

**Contract change (2026-08-24):** the original compact "normalizer + parser" scope below is superseded. Slice 2 is now a full production-grade deterministic log-processing engine (`src/agentic_pr_analyzer/parsing/`): raw log → normalized lines → sections → structured diagnostics → clusters → `FailureReport`. Still independent of GitHub, still zero AI/LLM/embeddings — stdlib only (`re`, `dataclasses`, `enum`, `pathlib`, `json`). Built **section by section**, each a runnable, tested vertical increment, so later sections add capability behind stable interfaces instead of forcing a core rewrite:

1. **Foundation, normalization core & engine contracts** — the canonical model, a `LogProvider` seam (detect + `GitHubActionsProvider` + `GenericProvider` fallback), a `ParseLimits` contract threaded through every stage, a total-function guarantee (`parse_log` never raises), basic secret masking, and a parser registry holding only the always-on `GenericParser`. *(done)*
2. **Python/pytest parser** — the first real tool-specific parser, validated against the committed `pallets/click` anchor fixture. *(done)*
3. **Stack-trace & test-runner abstractions across ecosystems** — jest/vitest + a shared status taxonomy. *(done — one `JsTestParser` covers both jest and vitest, since they share the `at file:line:col` stack format and `FAIL <file>` / `● <test>` markers closely enough that two near-identical classes would just be duplication; `parsing/stacktrace.py` holds the shared `parse_python_traceback`/`parse_js_stack`/`primary_frame` and `model.TestOutcome` is the shared taxonomy. Python + JS only this increment — other ecosystems are additive `parse_<lang>_stack` functions later, not a refactor. jest/vitest fixtures are hand-written and clearly marked synthetic under `tests/fixtures/raw_logs/SYNTHETIC/` — no red public jest/vitest run was captured yet; swap in a real `ingest` capture when one is seen.)*
4. **Compiler & static-analysis diagnostics** — tsc/eslint/gcc/clang/rustc/javac, each with its own real fixture. *(done — one `CompilerParser` covers tsc (`COMPILER_ERROR`) and eslint (`LINT_ERROR`), same "one class, two tools" shape as `JsTestParser`; error codes/rule ids live in `metadata["code"]`/`metadata["rule"]`, no schema change. gcc/clang/rustc/javac deferred as additive parsers later. tsc/eslint fixtures are hand-written and clearly marked synthetic under `tests/fixtures/raw_logs/SYNTHETIC/`, same precedent as Section 3 — no red public tsc/eslint run captured yet; swap in a real `ingest` capture when one is seen.)*
5. **Correlation, deduplication & failure clustering** — multi-diagnostic/multi-tool failure reports collapse into real clusters (Section 1 ships only trivial one-diagnostic-per-cluster).
6. **Scale & robustness** — streaming, bounded memory, perf, consuming the `ParseLimits` seam Section 1 already built.
7. **Security & observability hardening** — the full secret-masking provider matrix, metrics, confidence calibration.

A deterministic parser cannot justify causal claims: diagnostics are always framed as a "primary diagnostic" / "failure origin candidate", never a "root cause". Extract failure type, message, file/line/test information where available, stack trace/command metadata, and traceable evidence ranges (raw line numbers, `raw_text` alongside normalized `text`). Unknown failures stay `DiagnosticType.UNKNOWN` rather than being guessed. Every tool-specific parser (Sections 2–4) adds a regression fixture for the failure class it covers.

*Done (whole slice) =* `raw log → FailureReport`, with deterministic tests and real fixtures for every supported tool/ecosystem. *Done (Section 1, met) =* the engine spine, provider seam, `ParseLimits`, and total-function guarantee are in place and validated against the anchor fixture with golden-snapshot + fuzz/security tests, so Section 2's pytest parser plugs in with no core change. *Done (Sections 2+3, met) =* the pytest parser is the anchor fixture's primary cluster (`tests/test_types.py::test_file_surrogates[type1]`, `report.exit_code == 1` preserved via a shared `process_failure.py` extraction so the specialized parser co-emits it instead of relying on GenericParser's now-suppressed fallback), and the same `StackTrace`/`TestOutcome` spine is proven on a second ecosystem (jest + vitest) through one shared `JsTestParser` — no core rewrite in either case. *Done (Section 4, met) =* `CompilerParser` emits structured `COMPILER_ERROR`/`LINT_ERROR` diagnostics for tsc and eslint, registered in `PARSER_REGISTRY` ahead of `GenericParser`, co-emitting the shared `PROCESS_FAILURE` — again no core rewrite.

### Slice 3 — diff extraction and deterministic correlation

Fetch the PR diff and changed files, then correlate failure evidence with changed code without claiming causality. Produce relevance signals and explicit reasons that later agent reasoning can inspect.

*Done =* `failure evidence + PR changes → verified context bundle`.

### Slice 4 — one-shot LLM baseline

Feed the verified context bundle to a reliable structured-output model **without an agent loop**. Measure diagnosis quality against the first labeled evaluation set. This becomes the baseline that every later orchestration improvement must beat.

*Done =* a reproducible baseline score exists.

### Slice 5 — make it an agent

Hand-roll the controlled **plan → act → observe → verify → route** workflow. Add typed state, tool contracts, conditional routing, retries, re-planning, reflection only where useful, hard termination limits, cost/time budgets, and failure recovery.

*Done =* the agent can recover from a failed tool/hypothesis and terminate safely without an unbounded loop.

### Slice 6 — evaluation harness (hireable milestone)

Build a labeled set of real failing PRs and evaluate the baseline, agent, and agent + verification. Track root-cause accuracy, evidence quality, fix correctness, task completion, tool reliability, steps, latency, tokens/cost, retries, replans, and false-confidence behavior.

*Done =* we can state a real number and demonstrate which architectural change improved it.

### Slice 7 — fix generation

Implement the three-rung fix formatter: deterministic/safe patch, likely suggested diff, or complex-change handoff. Validate generated patches where possible. Keep the system read-only and require the user to apply the fix.

### Slice 8+ — product deepeners

Memory · deterministic dependency resolver · private-repo support · GitHub App/OAuth · MCP · web UI · deployment · polish. Add each only when the evaluation data or product requirement justifies it.

---

## 8. Timeline & Milestones

Roughly **6–10 weeks part-time** (~10–15 focused hrs/week) for the full thing. **A fully shippable, impressive version exists at ~week 5–6** (web app + working agent + real eval numbers).

- Wk 1: development access + GitHub ingestion + first real log fixtures.
- Wk 2: normalization + deterministic parser + evidence bundle.
- Wk 3: diff extraction/correlation + one-shot LLM baseline.
- Wk 4–5: controlled plan→act→verify agent + termination/recovery.
- Wk 5–6: evaluation harness + real numbers → **hireable milestone.**
- Wk 7+: fix generation, memory, dependency tooling, MCP, UI, private-repo auth, and polish.

Reality checks: coding is ~30% of the time; the rest is debugging integration seams, iterating on eval quality, context engineering, and the "last 20%." Consistency beats intensity — the biggest risk is the project going dormant, not difficulty. Estimate the next milestone, not the whole thing.

---

## 9. Production-Grade Checklist (do as you go, not at the end)

- **README** — the highest-leverage artifact. One-liner, 30-sec demo GIF, architecture + *why* (the decisions log above), eval numbers stated plainly. Draft it *before* finishing the build.
- **Tests** on the deterministic core (parser especially) + the eval harness as a distinct crown jewel.
- **Clean structure** — architecture visible in the file tree (core / surfaces / evals separated), clear names, type hints, docstrings.
- **Config & secrets** — `.env` + committed `.env.example`, no hardcoded keys or paths.
- **Error handling** — degrade gracefully on API timeouts, weird log formats, junk model output.
- **Polish** — LICENSE, story-telling commits, and (a beautiful flex here) a **CI Action running your tests** — your repo using the exact CI system your tool diagnoses.
- **Skip** deploy-grade infra (k8s, load balancing, monitoring, orchestration) — cargo-culting it reads as *less* senior. Production-*grade code* ≠ production-*deployed infra*.

---

## 10. Costs

- **Free:** GitHub App/OAuth registration, user authorization, reading repos (public + private, within rate limits), Actions on public repos.
- **Real recurring cost:** LLM API calls (~$10–20 covers building + evals). This is the only spend that matters now.
- **Hosting:** $0 for a long time (local dev; free tiers when needed). Not now.
- Caveat: free tiers, rate limits, and platform pricing shift — check current pricing pages rather than trusting fixed numbers.

---

## 11. Open Questions / Decisions Still To Make

- Which specific cheap model tool-calls reliably enough within budget? (Test a couple.)
- CI target beyond GitHub Actions? (GitLab / CircleCI / Jenkins — v2.)
- Exact schema for a "learning" record + retrieval logic for memory v1.
- The confidence/complexity signals the fix-formatter uses to pick a rung.
- Scoring approach for end-to-end evals: exact-match on error type vs LLM-as-judge for fix quality.

---

## 12. Topics To Research (concrete, grouped)

**Agent design & orchestration**
- The ReAct pattern; why plan→act→verify improves on blind ReAct
- Agent loop termination / avoiding infinite loops
- LangGraph internals (even if hand-rolling): state design, conditional edges, cycles, human-in-the-loop interrupts
- Reflection / self-correction patterns in agents

**Context engineering (your moat)**
- Extracting signal from large logs: failure-signature detection, error heuristics
- Relevance ranking + fitting a token budget
- Prompt structuring for feeding verified facts to a model

**Evals**
- Building an eval dataset for agents; labeling failing PRs
- Scoring: exact-match vs LLM-as-judge; pros/cons of each
- LangSmith basics; measuring before/after to prove improvements

**GitHub & CI**
- GitHub REST API: fetching Actions run logs, PR diffs, changed files
- GitHub Actions log format / structure
- Rate limits for authenticated vs unauthenticated requests

**Dependencies**
- How pip/uv (and npm) resolvers determine conflicts
- Querying PyPI / npm registry APIs for available versions
- OSV / GitHub Advisory database for vulnerability lookups
- Semantic versioning (major/minor/patch) and breaking-change signals

**Memory & RAG**
- RAG as a pattern vs embeddings+vector-DB as one implementation
- When structured retrieval (filters) beats semantic search
- Embeddings, vector stores (pgvector, Chroma) — for v2
- Memory staleness / versioning strategies

**Fix delivery**
- Unified diff format; generating `git apply`-compatible patches
- GitHub's PR "suggestion" feature

**Auth & security**
- Personal Access Tokens (scopes, read-only)
- OAuth / GitHub App flow (callback, token exchange, refresh) — v2
- Principle of least privilege; secure secret handling (`.env`, never log/commit, keep out of model context)

**Deployment (later)**
- FastAPI deployment basics
- Streamlit for quick UIs
- Free-tier hosts (Railway, Render, Fly.io); AWS free-tier caveats
- MCP server structure + the MCP Python SDK

---

## 13. The One Thing That Matters Most

The design is done and it's genuinely strong. The only thing between this and a hero project is **the first commit, then the next.** Start with Slice 1 (fetch + parse one real log), built *properly* (clean, tested, README stub). Quality and completeness are achieved one disciplined commit at a time — not by more planning.
