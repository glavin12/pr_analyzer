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
