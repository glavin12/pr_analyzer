# CLAUDE.md

This is the fast-orientation file for this repo — what to read first to know what this project is, what's built, and what rules to follow while working on it. For the full design rationale, roadmap, and reasoning behind every decision below, see [`docs/project-brief.md`](docs/project-brief.md); this file summarizes and links rather than re-explaining it, so a changed detail only ever needs updating in one place.

## 1. Project summary

`agentic_pr_analyzer` is an agentic AI tool that explains *why* a GitHub CI build/PR is red, proposes fixes, and — eventually — predicts merge breakage before it happens, by reading CI logs and PR diffs, reasoning over them, and remembering what it learned per repo. It's built as a public, production-grade portfolio project: discipline and small, verifiable increments matter as much as raw function. Full context: [`docs/project-brief.md`](docs/project-brief.md).

## 2. Current status

**Phase 1, Slice 0 (dev access) + Slice 1 (GitHub CI ingestion): done.**

- [x] Slice 0 — repo contract: `.env`/`.env.example`, `.gitignore`, config loader, GitHub client, `verify` CLI command working against the live API.
- [x] Slice 1 — GitHub CI ingestion: auto-discovers the latest failed workflow run, fetches failed job logs, saves them as committed fixtures (`tests/fixtures/raw_logs/<owner>/<repo>/`). `ingest` CLI command working against the live API.
- [ ] Slice 2 — log normalization and deterministic parser (not started)
- [ ] Slice 3 — diff extraction and correlation (not started)
- [ ] Slice 4 — one-shot LLM baseline (not started)
- [ ] Slice 5 — agent loop (not started)
- [ ] Slice 6 — evaluation harness (not started)
- [ ] Slice 7 — fix generation (not started)
- [ ] Slice 8+ — memory, dependency checking, private-repo support, OAuth, MCP, web UI, deployment, polish (not started)

**Standing rule: update this checklist as part of the commit that completes each slice.** It must never go stale — a future session (including a future instance of Claude Code) reads this section first to know where the project actually stands.

## 3. Architecture

Six layers, bottom-up (full rationale in the brief §3):

1. **Data layer** — pull CI logs and PR diffs from the GitHub Actions API. *(built: `github/client.py`, `github/ingestion.py`)*
2. **Core engine (the moat, zero AI)** — deterministic log parser, diff correlator, breakage predictor, context assembler. *(not started — Slice 2+)*
3. **Model layer** — a cheap LLM API turns clean context into judgment (cause + fix). *(not started — Slice 4)*
4. **Orchestration loop** — hand-rolled plan → act → verify agent loop. *(not started — Slice 5)*
5. **Surfaces** — web app (primary), MCP server (secondary). *(not started)*
6. **Eval harness** — labeled test set + scoring, the thing that makes this a hero project and not a demo. *(not started — Slice 6, though Slice 1's fixtures already feed it)*

## 4. Repository layout

```
src/agentic_pr_analyzer/
├── __init__.py       # thin re-export of cli.main
├── cli.py             # argparse CLI: verify, ingest
├── config.py           # .env loading, GITHUB_TOKEN
├── exceptions.py        # typed exception hierarchy
└── github/
    ├── client.py          # authenticated GitHub REST wrapper (data layer)
    ├── models.py            # RawLog evidence model
    └── ingestion.py           # resolve failed run -> fetch logs -> save fixtures
tests/
├── test_config.py, test_github_client.py, test_ingestion.py
└── fixtures/raw_logs/<owner>/<repo>/<run_id>_<job_id>.{log,json}  # real captured fixtures, committed
docs/project-brief.md   # full design doc
```

Module boundaries are deliberate: GitHub API access, evidence models, and ingestion orchestration are three separate files so the parser/correlator (Slice 2+) can plug in without touching this code.

## 5. Development setup

```bash
uv sync
cp .env.example .env   # then add your own GITHUB_TOKEN — see .env.example for scope guidance
uv run agentic-pr-analyzer verify
uv run agentic-pr-analyzer ingest <owner>/<repo>
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
- Real-network tests use the `integration` pytest marker and are excluded by default (`addopts = "-m 'not integration'"` in `pyproject.toml`); run explicitly with `uv run pytest -m integration`.
- `GitHubClient` is tested by injecting a mocked `requests.Session` via its constructor — no HTTP-mocking dependency needed.
- Fixtures under `tests/fixtures/raw_logs/` are real logs captured by actually running `agentic-pr-analyzer ingest` against a real public repo — not synthetic data. They're committed (not gitignored) since they're regression fixtures, not build noise.

## 8. Explicitly out of scope right now

Documented deferrals, not oversights — don't build ahead of the current slice:

- Log parsing/normalization, diff correlation, any LLM calls (Slices 2–4).
- GitHub Actions CI for this repo itself — technically possible now (`origin` already points at `github.com/glavin12/pr_analyzer`), but it's polish-checklist scope (brief §9), not required by any current slice.
- OAuth/GitHub App auth — explicitly v2 per the brief's first implementation rule.
- `ruff`/`mypy` tooling — not required for current functionality.
- Automatic retry/backoff on GitHub API errors — that's orchestration-loop-shaped behavior for Slice 5, not this deterministic ingestion code.
- Pagination beyond the most recent failed run — Slice 1 only needs the single latest failure.

## 9. Full design doc

See [`docs/project-brief.md`](docs/project-brief.md) for the complete architecture rationale, all key decisions and *why*, the full slice-by-slice build order, timeline, and the production-grade checklist.
