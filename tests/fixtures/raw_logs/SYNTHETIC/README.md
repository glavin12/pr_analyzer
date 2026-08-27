# SYNTHETIC fixtures

Everything under this directory is **hand-written**, not a real captured CI
log (unlike `tests/fixtures/raw_logs/pallets/click/`, which is a real
`agentic-pr-analyzer ingest` capture). Section 3 needs a jest and a vitest
failure fixture to prove the shared `stacktrace.parse_js_stack` /
`TestOutcome` spine generalizes across ecosystems, but no red public
jest/vitest run was captured yet via `ingest` when Section 3 was built
(2026-08-25).

These reproduce the real CLI output shape (GitHub Actions timestamp
prefixes, jest's `FAIL <file>` / `● <suite> › <test>` blocks, vitest's
`RUN v...` banner) closely enough to exercise `JsTestParser` end to end, but
they are not a substitute for a real fixture.

**Regression note:** replace these with a real `ingest` capture from a red
public jest/vitest run the first time one is seen (matches the project
brief's Phase 26 note), and delete this directory once both are real.

## tsc-sample / eslint-sample (Section 4)

Same situation: hand-written to reproduce `tsc`'s two diagnostic shapes
(`file:line:col - error TSxxxx: ...` and `file(line,col): error TSxxxx: ...`)
and eslint's "stylish" reporter output, because no red public tsc/eslint
`ingest` capture existed yet when Section 4 was built (2026-08-26). Swap in a
real capture the first time one is seen.

## multi-test / duplicate-sections / cascade / summary-echo (Section 5)

Four more hand-written fixtures, for correlation and clustering. The only real
capture in hand is `pallets/click`, a single-test-failure Python job; each
shape below needs a red public run that was not available when Section 5 was
built (2026-08-27). Same standing rule: **replace with a real `ingest` capture
the first time one is seen.**

| Fixture | Shape | Evidence strength |
|---|---|---|
| `multi-test-sample` | pytest, 3 failing tests across 2 files, `= FAILURES =` block, `short test summary info`, `##[error]...exit code 1` | **Strong** — a straight extension of the verified anchor's pytest output |
| `duplicate-sections-sample` | a tsc section *and* a pytest section in one log, so both parsers fire and emit two byte-identical `PROCESS_FAILURE`s differing only in `parser` | **Strong** — the duplication it pins is a verified property of `_run_registry` against real code, not an invention |
| `cascade-sample` | two failing steps: `##[group]Run npm run build` + 2 tsc errors, then `##[group]Run npm test` + 1 jest failure, then one `##[error]...exit code 1` | Medium — step shape matches real GHA, but a two-failing-step job needs `continue-on-error`; GHA otherwise halts at the first failure |
| `summary-echo-sample` | a tsc error printed by the tool, then echoed by a problem matcher as `##[error]src/auth.ts(42,7): error TS2339: ...` | **Weakest in the set.** The exact rendering of problem-matcher output in a raw job log is unconfirmed. **First to replace with a real capture.** |

`summary-echo-sample` also pins a known, deliberately-unfixed parser bug:
`_TSC_NONPRETTY_RE`'s unanchored `\S+` swallows the `##[error]` prefix into
`Diagnostic.file`, producing `file == "##[error]src/auth.ts"`. Section 5's rule
S1 is robust to it by matching on normalized message + line + column and never
on `file`. Fixing the regex is a one-line parser change filed separately — the
dedup layer is deliberately not where parser bugs get papered over.

## secrets-sample (Section 7)

Hand-written, and this one is hand-written **on purpose and permanently** — it
is not waiting for a real capture to replace it. A fixture whose job is to
prove secrets get masked must not contain real secrets.

Every planted literal either contains the ASCII substring `EXAMPLE` or has a
body that is a run of a single repeated character, so none of them can be a
live credential and none can trip a real secret scanner. The literals are
defined once in `tests/secret_examples.py`;
`tests/test_fixture_secret_policy.py::test_synthetic_secret_fixture_contains_only_obviously_fake_secrets`
enforces the fakeness rule mechanically, so pasting a real token in here fails
the suite before it can be committed.

The file plants each shipped rule's literal in an ordinary line **and** inside
`##[group]` / `##[command]` / `##[error]` marker bodies — the marker positions
are the permanent regression home for LEAK-1.

It also carries **negative controls**: an action-pin git SHA, a worker UUID, a
pytest parametrized id containing `=`, an `SSH_KEY_PATH=` assignment, a
credential-free `postgres://localhost:5432/test` DSN, and a `BEGIN PUBLIC KEY`
header. Those must survive masking **verbatim** — they are evidence, not
secrets, and over-masking them is a bug (rules R1–R6).
