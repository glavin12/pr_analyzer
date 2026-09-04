---
name: ci-doctor
description: Diagnose why a GitHub CI run or PR check is red. Fetches the failed job log to a FILE, parses it with the ci_log_parser MCP tool, and reasons from the structured, evidence-backed failure summary. Use when a PR check or workflow run is failing and the user wants to know what broke and where.
---

# /ci-doctor

Turn a red CI run into precise, evidence-backed failure context using the
`ci_log_parser` MCP server (tool `analyze_ci_log`). The parser is deterministic
and LLM-free — it hands you clean evidence (file/line, test name, the exact
diagnostic, a short masked excerpt); YOUR reasoning turns that into the
explanation or fix.

## Hard rule: never read the raw log yourself

The raw CI log must **never** enter your context. It is noisy, ANSI-laden, and
often huge, and the tool already gives you cleaner information than the raw text
would. So:

- Fetch the log **redirected straight to a file** — never printed to stdout.
- Pass only the **file path** to `analyze_ci_log`.
- **Never** `cat`, `grep`, `head`, `tail`, `less`, or open/Read that log file.
  Work solely from the tool's structured response.

## Recipe

1. **Find the failing run.** For the current branch's PR:
   ```bash
   gh pr checks
   ```
   or list recent runs and pick the failed one:
   ```bash
   gh run list --limit 10
   ```

2. **Get the failed job id(s) for that run.** A run can have several failed
   jobs — handle every id this returns, not just the first.
   ```bash
   gh run view <run-id> --json jobs \
     -q '.jobs[] | select(.conclusion=="failure") | .databaseId'
   ```

3. **Fetch each failed job's RAW log to a file (redirect — do not print).**
   ```bash
   gh api "repos/{owner}/{repo}/actions/jobs/<job-id>/logs" > "${TMPDIR:-/tmp}/ci_<job-id>.log"
   ```
   Use the **raw** job-log API — *not* `gh run view --log`/`--log-failed`. That
   reformats every line with a job-name and step-name prefix (tab-separated),
   which pushes the timestamp off the start of the line; the parser detects
   GitHub Actions by a leading timestamp and reads `##[group]`/`##[error]`
   markers, so the reformatted shape defeats detection and the parser reports a
   false "no failures found". This endpoint returns the exact bytes `ingest`
   captures (timestamped lines, `##[group]`/`##[error]` markers, BOM) — the
   format the parser is proven against. `gh api` fills `{owner}/{repo}` from the
   current repo and follows GitHub's 302 to the signed blob automatically. The
   Bash result you see is only an exit code — the log content does not enter
   your context.

4. **Parse each log.** Call the MCP tool with only the path, once per fetched
   file: `analyze_ci_log(path="${TMPDIR:-/tmp}/ci_<job-id>.log")`

5. **Reason from the structured summary.** You get back a tiered
   `FailureReport` summary: ranked failure clusters, each with a
   `primaryDiagnostic` (tool, kind, test name, file/line, message, a short
   masked excerpt, rule-based confidence), plus an `omitted` block telling you
   how much was collapsed. Drill in only as needed:
   - `get_cluster_detail(reportId, clusterId)` — full evidence for one cluster
     (all related diagnostics, full stack frames).
   - `get_full_report(reportId)` — everything (deliberate, explicit call).

6. **Explain and/or propose a fix** from that evidence. The tool never claims a
   root cause or writes a fix — that judgment is yours.

## Notes

- The server reads the file server-side and masks secrets before anything
  crosses back to you; it makes no network calls and never handles CI
  credentials (your own `gh` auth does the fetch).
- Paths must sit under an allowed root (system temp dir or the project
  directory by default) — the `${TMPDIR}` fetch path above satisfies this.
