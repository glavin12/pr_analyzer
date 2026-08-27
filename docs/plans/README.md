# Slice 2 — remaining section plans (5, 6, 7)

Design specs for the last three sections of the deterministic log-parsing engine
(`src/agentic_pr_analyzer/parsing/`). Sections 1–4 are shipped; see `CLAUDE.md` §2.

| Section | Plan | Scope |
|---|---|---|
| 5 | [`section-5-correlation.md`](section-5-correlation.md) | correlation, deduplication, failure clustering |
| 6 | [`section-6-scale.md`](section-6-scale.md) | scale & robustness (complexity, bounds, truncation, degradation) |
| 7 | [`section-7-security.md`](section-7-security.md) | security & observability hardening |

Each plan is written to the standard in `CLAUDE.md` §6–§7: deterministic only, stdlib
only, tests before implementation, additive contract changes, explicit deferrals. The
quality gate is that another engineer can implement the section from the document
without re-deriving the architecture.

---

## Standing constraints (all three sections)

- **Zero AI/LLM/embeddings/semantic similarity/vector search.** Explicit rules, ordering,
  nesting, exit codes, normalized keys, source ranges, stack frames only.
- **Stdlib only.** No new runtime dependency; no new dev dependency without arguing it
  against `CLAUDE.md` §6's small-dependency-surface rule.
- **Frozen public interface**: `parse_log(content, source, limits) -> FailureReport`,
  `to_json`, `to_dict`, `ParseLimits`, `FailureReport`. Additive changes only.
- **`parse_log` is a total function** — it never raises, for any input.
- **Tests before implementation.** A test failing after implementation means the
  implementation is wrong, unless the test is confirmed to assert the wrong thing.
- **Naming**: "primary diagnostic" / "failure origin candidate". Never "root cause".

---

## Verification status of the design claims

Claims were re-checked against the running code rather than taken on trust. Note the
methodology trap: `detect_provider` requires a timestamp-prefixed line within the first
50 lines, otherwise it falls through to `GenericProvider`, `##[...]` markers are never
parsed, and marker-dependent bugs cannot fire. A first verification pass missed both live
bugs for exactly this reason.

| Claim | Status | Evidence |
|---|---|---|
| `marker_body` is never masked (LEAK-1) | **Confirmed** | `##[group]Run curl -H "Authorization: Bearer ghp_A×36"` with timestamps → raw token present in `to_json`, via `LogSection.title` and `Diagnostic.message` |
| `build_sections` is O(n·depth) | **Confirmed** | 2000→0.070s, 5000→0.378s, 10000→1.445s, 20000→5.798s (10× lines → 83× time) |
| `max_context_lines` is dead config | **Confirmed** | zero call sites outside `limits.py` |
| `models.py:81` strict decode vs `client.py:103` `errors="replace"` | **Confirmed** | asymmetric decode boundary |
| All four parsers call `find_process_failure` | **Confirmed** | duplicate `PROCESS_FAILURE` across co-firing parsers is structurally real |
| Section 6's 10/50/100 MB timings and memory peaks | **NOT re-verified** | recorded as the design's measurements; **must be re-measured** before landing in `docs/perf-baselines.md` |

---

## Cross-section reconciliations

The three section designs were produced independently. These are their conflicts. **Each
must be applied, or the sections break each other.** Three of them (C1, C2, C3) would
otherwise produce broken code.

### C1 — `SCHEMA_VERSION` chain

Sections 5 and 7 each independently claimed `"1.1" → "1.2"`. Sequenced:

| Stage | Version | Reason |
|---|---|---|
| Hotfix | `1.1` | no schema-surface change |
| Section 5 | **`1.2`** | new `DiagnosticRole` enum + two `FailureCluster` fields |
| Section 6 | `1.2` (no bump) | new keys land in `stats`/`metadata`, both already free-form `dict` — the Section 4 precedent |
| Section 7 | **`1.3`** | `stats` key set changes, and free-text field *content* changes under the expanded matrix |

### C2 — stats key naming collision

Section 5 originally named its dedup counter `duplicates_collapsed`; Section 7's
`_base_stats()` declares `diagnostics_deduplicated` (the spec's name). **Section 5 uses
`diagnostics_deduplicated`.** Section 5 additionally keeps `clusters_built`.

Section 7 defines only the key, type and default — never the value. Implement as a
default in `_base_stats()` that the clustering stage **overwrites**, never as an
unconditional assignment after clustering (which would zero out Section 5's work).

### C3 — the evidence cap must be head-first

Section 6 (D5) caps `Diagnostic.evidence` to `max_context_lines`. Section 5's ranking
ladders use `min(d.evidence)`, and `section_id` derives from `evidence[0]`. **The cap
keeps the first N entries**, so both survive. Pinned by
`test_evidence_cap_preserves_first_entry`.

Standing handoff rule, repeated in both documents: **correlate on `source_range`, sample
from `evidence`.**

### C4 — `_build_clusters` ownership → micro-optimization declined

Section 6 (D9) proposed replacing the `raw_lineno` dict at `pipeline.py:151` with index
math. Section 5 deletes that function outright and moves the logic to `clustering.py`.

**Declined, not deferred.** Measured at 0.004s (~0.5% of parse), and it would land in a
file Section 5 has just written. Recorded here so it is a decision, not an omission.

### C5 — the ANSI/mask fast path is one change, claimed twice

Section 6 (step 9) and Section 7 (step 6) describe the same optimization: strip ANSI
once, reuse the result when the payload has no ESC byte. **Section 7 owns it** — it
rewrites that exact block for `marker_body` masking anyway. Removed from Section 6.

### C6 — Section 7 invalidates Section 6's perf baselines

Section 7 takes masking from 3 rules to 13, and `normalize` is ~77% of parse runtime.
Section 6 writes `docs/perf-baselines.md`; **Section 7 must re-record it** as its final
step, with the masking-cost delta called out. Without this the table goes stale one
section after it is written.

### C7 — `peak_lines_retained` justification

Section 7 justified this metric as "becomes meaningful the moment Section 6 streams."
Section 6 **declines streaming** (D1), so it permanently equals `lines_processed`. The
metric stays — it is still the deterministic memory proxy alongside `bytes_processed` —
but its docstring says so honestly rather than promising a designed-away future.

### C8 — confidence calibration is descoped and must be recorded as such

`docs/project-brief.md` lists confidence calibration under Section 7. The Section 7
design covers security + observability only and leaves `confidence.py` untouched.

**Section 7 is not ticked in `CLAUDE.md` §2 without an explicit re-scoping note** saying
calibration moved out and where it went. Flagging a dropped deliverable beats silently
shipping seven of eight items.

---

## Build order

### Step 0 — hotfix first

Two live bugs in shipped code, plus one total-function hole. No schema change, no
golden-snapshot churn, red test first for each. Removed from Sections 6 and 7 so they are
not done twice.

| Fix | File | Change |
|---|---|---|
| LEAK-1 — mask `marker_body` | `parsing/normalizer.py:41` | mask before `LogLine` construction; closes the token-in-`##[group]`-title leak into `to_json` |
| O(n²) segmentation | `parsing/segmentation.py:37,43,50` | stop rewriting the whole `open_stack` per line; set `end_lineno` on pop + once at EOF |
| Decode asymmetry | `github/models.py:81` | `decode("utf-8", errors="replace")`, matching `client.py:103` — a `UnicodeDecodeError` here escapes *outside* `parse_log`'s guard |

Tests, written failing first:

- `test_group_marker_body_is_masked_in_section_title`
- `test_error_marker_body_is_masked_in_process_failure_message` (masked **and** `exit_code == 1` still extracted)
- `test_build_sections_is_linear_in_deep_nesting` (20k groups, 10× slack ceiling; today 5.8s)
- `test_build_sections_end_lineno_matches_last_line_in_scope`
- `test_build_sections_unbalanced_open_closes_at_last_line`
- `test_build_sections_unbalanced_close_is_ignored`
- `test_linear_segmentation_matches_reference_over_random_marker_soup` — seeded `random.Random`, 3000 traces over `{GROUP, ENDGROUP, ERROR, COMMAND, None}`, full tuple equality against a frozen copy of the old algorithm
- `test_load_raw_log_survives_invalid_utf8`

**Acceptance:** suite green; golden snapshot passes **byte-for-byte unchanged** (the
anchor's only credential-shaped line is GitHub's own `AUTHORIZATION: basic ***`);
`SCHEMA_VERSION` still `1.1`.

### Then Sections 5 → 6 → 7

Each per its own document. Sections 5 and 7 regenerate the golden snapshot; both carry a
subtree-unchanged guard so a regeneration cannot hide a regression.

---

## Explicit non-goals (all sections)

No LLM, semantic reasoning, RAG, embeddings, AI-generated causes or fixes, autonomous
code modification, or automatic PR comments. No new runtime dependency. No causal
language — a cluster carries a *failure origin candidate*, never a "root cause".
