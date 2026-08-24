# agentic-pr-analyzer

An agentic AI tool that explains why a GitHub CI build/PR is red — by reading CI logs and PR diffs, reasoning over them, and remembering what it learned per repo.

Full design rationale and roadmap: [`docs/project-brief.md`](docs/project-brief.md). Current build status and operating rules: [`CLAUDE.md`](CLAUDE.md).

## Status

Phase 1, Slice 0 + Slice 1: a CLI that safely authenticates against the GitHub API and turns a real failed GitHub Actions run into a local, committed log fixture — with zero manual copying. See [`CLAUDE.md`](CLAUDE.md) for the full slice-by-slice checklist.

## Quickstart

```bash
uv sync
cp .env.example .env   # then add your own GITHUB_TOKEN — see .env.example for scope guidance

uv run agentic-pr-analyzer verify              # confirms the token works
uv run agentic-pr-analyzer ingest owner/repo   # fetches the latest failed CI run's logs as a fixture

uv run pytest                                   # offline test suite
```
