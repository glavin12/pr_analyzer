# Section 7 — Security & Observability Hardening

> Part of the Slice 2 engine plan. Shared constraints and cross-section
> reconciliations: [`README.md`](README.md). Sequenced **last**, after Section 6.
>
> **Removed from this section by reconciliation:** the LEAK-1 `marker_body` masking fix
> moved into the Step 0 hotfix (this section builds on it). **Added:** this section owns
> the ANSI/mask fast path ([C5](README.md#c5--the-ansimask-fast-path-is-one-change-claimed-twice))
> and must re-record `docs/perf-baselines.md`
> ([C6](README.md#c6--section-7-invalidates-section-6s-perf-baselines)).

---

## 1. Current-state audit

### What is protected today

Masking is applied at exactly one place, `normalizer.py:42-43`, per line, before `LogLine`
construction:

```python
text = mask(_ANSI_RE.sub("", payload))
raw_text = mask(payload)
```

Three patterns exist (`sanitize.py:11-17`): GitHub tokens, URL-embedded credentials,
`Bearer <x>`. Because `LogLine.text`/`raw_text` are masked at construction, everything
derived from them inherits masking: `Diagnostic.message` (`pytest_parser.py:222-231`,
`js_test_parser.py:106-120`, `compiler_parser.py:80/113`, `generic_parser.py:44`);
`Diagnostic.metadata` values; `StackFrame.raw_text` (`stacktrace.py:79`, `:125`);
`StackTrace.message`/`exception_type` (`stacktrace.py:95-99`). Those are clean.

`LogSource` (`model.py:146-175`) is populated from the `.json` sidecar — GitHub API
metadata only. `html_url` is a public `github.com` URL. Clean.

### Leak channels

**LEAK-1 — `LogLine.marker_body` is never masked. FIXED IN STEP 0; this section keeps the
regression tests.**

`normalizer.py:41` computes `marker, marker_body = provider.marker_of(payload)` from the
**raw, unmasked** payload, before the two `mask()` calls on the next two lines.
`marker_body` then flows into serialized output in two places:

- `segmentation.py:31` — `"title": line.marker_body` → `LogSection.title` → `to_json`
- `process_failure.py:35` — `message=line.marker_body` → `Diagnostic.message` → `to_json`

Independently reproduced against the real pipeline (timestamps required, or
`detect_provider` falls through to `GenericProvider` and markers are never parsed):

```
input:  ##[group]Run curl -H "Authorization: Bearer ghp_AAAA…(36)"
        ##[error]Process completed with exit code 1 token=ghp_AAAA…(36).

raw token present in to_json: True
section title:      Run curl -H "Authorization: Bearer ghp_AAAA…AAAA"
diagnostic message: Process completed with exit code 1 token=ghp_AAAA…AAAA.
```

The existing guard `test_parsing_fuzz_security.py:84-89` passes only because it plants the
token in an ordinary (non-marker) line. Every real GitHub Actions log is dense with
`##[group]`/`##[command]`/`##[error]` lines, and `##[group]Run <shell command>` is exactly
where a `curl -H "Authorization: …"` shows up.

**LEAK-2 — `cli.py:91`** prints `str(e)` for any `requests.exceptions.RequestException`.
`client.py:84-96` documents that `get_job_log` follows GitHub's 302 to a short-lived
**signed** blob URL. A `ConnectionError`/`ReadTimeout` against that redirected URL
stringifies the full URL including its SAS query parameters onto stderr. Not the parsing
engine's choke point, but it is a credential path to stderr and this section owns it.

### Non-leaks checked and cleared

- `stats` (`pipeline.py:89-98`) — all values are `int`/`bool`/`str`-from-a-closed-set. No
  log text. **No invariant enforces this**, though — §7.4 adds one.
- `cli.py:46` — `print(to_json(report))`; `to_json` uses `ensure_ascii=True`
  (`model.py:210`), so output is pure ASCII. The only `print` calls in `src/` are in
  `cli.py`.
- `config.py:23-28` — `ConfigError` never interpolates the token.
- `github/models.py:66` — `save_raw_log` writes the log **byte-faithful, unmasked** to
  disk. Deliberate (the parser needs messy input) and correct, but it is why the
  fixture-vetting policy in §6 is load-bearing.

### Real-world baseline from the anchor fixture

Scanning `tests/fixtures/raw_logs/pallets/click/…log` (2323 lines): four `***`
occurrences — GitHub Actions' own masking of registered secrets (`token: ***`,
`github-token: ***`, and at line 79 `"AUTHORIZATION: basic ***"` from `actions/checkout`).
Zero raw credentials.

**This is the calibration fact: GitHub already masks registered secrets.** Our matrix's job
is the residual — secrets that were never registered (a PAT hardcoded in a script, an `env`
dump, a `set -x` trace, a third-party tool printing its own key).

**Verdict:** the single choke point is *architecturally* right and covers all text fields —
it was merely *incompletely applied*. With LEAK-1 fixed in Step 0, the choke point genuinely
covers everything.

---

## 2. Threat model

**What secret reaches this system.** Only one input carries secrets: the raw CI log text
fetched by `ingest`. Our own `GITHUB_TOKEN` never enters `parsing/`. Realistic classes,
ranked by observed frequency:

1. **Not-registered credentials echoed by build steps** — `set -x` traces
   (`+ export API_KEY=…`), `env`/`printenv` dumps, `npm config list`, `docker login`
   echoes, a `curl -H "Authorization: …"`. **The dominant real leak:** GitHub can only mask
   what was declared as a secret.
2. **Credentials in URLs** — `git clone https://user:token@host/…`, an authenticated pip
   index, `postgres://user:pass@host/db` printed by a failing test.
3. **Third-party tool output** — a CLI printing its own API key on an auth failure.
4. **Base64 auth blobs** — `AUTHORIZATION: basic <base64(user:token)>`, the exact shape at
   line 79 of our anchor fixture.
5. **Private key blocks** — a workflow that `cat`s a deploy key on failure.

**How exposure happens.** The `FailureReport` is not a terminal artifact:

`raw log` → `FailureReport` → **(a)** `to_json` printed to a terminal / CI job output;
→ **(b)** committed as a golden fixture under `tests/fixtures/parsed/`, i.e. *published to
a public GitHub repo, permanently, in git history*; → **(c)** Slice 4: fed verbatim into a
third-party LLM API as context; → **(d)** Slice 8+: rendered in a web UI.

**(b) and (c) are the ones that matter.** A secret that survives into
`tests/fixtures/parsed/*.json` cannot be un-published. A secret that survives into Slice
4's context is transmitted to a third-party model provider — a direct violation of
`CLAUDE.md` §6's "never … placed in model context." Everything downstream of `parse_log`
inherits whatever `parse_log` emitted, so the mitigation has to be here.

**Out of the threat model** (stated so nobody builds for it): we are not defending against
a malicious log author trying to evade masking. CI logs come from the repo's own workflow;
if an attacker controls the workflow they already own the secrets. We defend against
*accidental* leakage — which is why deterministic pattern matching is proportionate and
entropy heuristics are not.

**Explicitly accepted residual risk:** secrets in formats not covered by the matrix
survive. A regex masker is a filter, not a guarantee. The compensating control is the
fixture-vetting policy (§6), not a bigger regex.

---

## 3. Masking design

### 3.1 The pattern matrix

An **ordered** tuple; earlier rules win on overlapping matches (`Bearer ghp_AAAA…` masks as
`github_token`, the more specific class, because it runs first). **Order is part of the
contract** and must be documented in the module docstring.

All patterns were run against every committed fixture (`pallets/click` 2323 lines + 4
SYNTHETIC samples): **1 match, 0 false positives**, and the golden snapshot's masked text
is **byte-identical** before/after. That is the empirical basis for "ship now".

| # | Secret class | Pattern (Python `re`) | Mask token | False-positive risk | Tier |
|---|---|---|---|---|---|
| 1 | GitHub token | `\b(?:gh[pousr]_[A-Za-z0-9]{20,}\|github_pat_[A-Za-z0-9_]{20,})\b` | `«REDACTED:github_token»` | **None.** Prefix + length is unique. Existing test pins `ghp_short` as no-match | **Ship** (already) |
| 2 | Private key block | `-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----` | `«REDACTED:private_key»` | **None.** Literal PEM armor. Header only — see R6 | **Ship** |
| 3 | URL-embedded creds | `://[^\s:@/]+:[^\s@/]+@` | `://«REDACTED:url_creds»@` | **Very low.** Preserves scheme and host — `github.com/org/repo.git` stays readable | **Ship** (already) |
| 4 | Authorization header | `(?i)\b(?:proxy-)?authorization\s*:\s*[A-Za-z]+\s+[^\s"']+` | `Authorization: «REDACTED:http_auth»` | **Low.** `[^\s"']+` deliberately stops at a quote so the closing `"` of a shell-quoted header is not swallowed | **Ship** — the anchor's only real hit (line 79) |
| 5 | Bearer token | `(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}` | `Bearer «REDACTED:bearer_token»` | **Low, and reduced from today**: the current `\bBearer\s+\S+` masks the next word of any English sentence containing "bearer". `{8,}` + RFC-6750 charset fixes that | **Ship** (tightening) |
| 6 | JWT | `\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{4,}` | `«REDACTED:jwt»` | **None.** `eyJ` is base64(`{"`) and the three-segment structure is unmistakable | **Ship** |
| 7 | AWS access key id | `\b(?:AKIA\|ASIA\|AGPA\|AIDA\|AROA\|ANPA\|ANVA\|APKA)[0-9A-Z]{16}\b` | `«REDACTED:aws_key_id»` | **None.** Fixed prefix + fixed length + uppercase-only | **Ship** |
| 8 | Env/shell secret assignment | `(?i)(^\|[\s;&\|(:])([A-Z0-9_]*(?:TOKEN\|SECRET\|PASSWORD\|PASSWD\|API_?KEY\|ACCESS_KEY\|PRIVATE_KEY))=[^\s"']+` → repl `\g<1>\g<2>=«REDACTED:env_secret»` | key name **preserved**, value masked | **Low, and measured.** Two validated guards: (a) the keyword must be the *suffix* of the name, immediately adjacent to `=`, so `SSH_KEY_PATH=/home/runner/.ssh/id_rsa` does **not** match; (b) the leading class excludes `[`, so `test_foo[api_key=abc]` does **not** match. Including `:` catches `.npmrc`'s `:_authToken=` | **Ship** — where the real residual leaks live |
| 9 | Slack token | `\bxox[baprse]-[A-Za-z0-9-]{10,}\b` | `«REDACTED:slack_token»` | **None** | **Ship** |
| 10 | Stripe key | `\b(?:sk\|rk)_(?:live\|test)_[A-Za-z0-9]{16,}\b` | `«REDACTED:stripe_key»` | **None** | **Ship** |
| 11 | npm token | `\bnpm_[A-Za-z0-9]{36}\b` | `«REDACTED:npm_token»` | **None.** Exact length | **Ship** |
| 12 | PyPI token | `\bpypi-[A-Za-z0-9_-]{32,}` | `«REDACTED:pypi_token»` | **None** | **Ship** |
| 13 | Google API key | `\bAIza[A-Za-z0-9_-]{35}\b` | `«REDACTED:google_api_key»` | **None.** Exact length | **Ship** |
| 14 | DB connection string (no `@`) | `\b(?:postgres(?:ql)?\|mysql\|mongodb(?:\+srv)?\|redis\|amqp)://\S+` | — | **Medium — why it's deferred.** Rule 3 already covers the credentialed form. This would additionally mask credential-free DSNs like `postgres://localhost:5432/test`, common in test-failure messages and pure debugging evidence. Masking them destroys evidence for zero security gain | **Later** |
| 15 | Azure/GCP service-account JSON, Twilio, SendGrid, Datadog, Vault, Docker Hub PAT, generic `?apikey=` | — | — | each is real, none has appeared in any fixture, each is one tuple entry | **Later** |
| 16 | High-entropy generic heuristic | — | — | **Rejected outright — §3.4** | **Never** |

Rules 14–16 do not ship. Rules 1–13 do: 13 entries, one tuple, ~20 lines of data.

### 3.2 Where masking occurs — one choke point, correctly applied

**Decision: keep exactly one choke point, in `normalizer.py`. No second output-boundary
pass.**

The reasoning is structural. `LogLine` has exactly three free-text fields: `raw_text`,
`text`, `marker_body` (`model.py:73-80`). Every string in the serialized `FailureReport`
derives from one of those three, or from `LogSource` (GitHub API metadata, no log content).
Two were masked before Step 0; the third (`marker_body`) was the leak. Masking all three at
construction makes the invariant complete and *structural*: **no unmasked log text exists
anywhere past `normalize()`.**

A second pass at the output boundary was considered and rejected:

- it duplicates work on data that is already provably masked;
- it would need to walk a nested `metadata: dict` of arbitrary shape — exactly where a
  re-mask could corrupt a non-string value;
- it defends against a hypothetical future parser reading from somewhere other than
  `LogLine` — but no such source *exists*: parsers receive `list[LogLine]` and
  `tuple[LogSection, ...]` and nothing else (`parsers/base.py:13-17`). Closing that door
  with a **test** costs nothing at runtime and fails just as loudly.

Enforcement: mask all three `LogLine` text fields, plus a structural test
(`test_every_logline_str_field_is_masked`) that iterates `dataclasses.fields(LogLine)` and
asserts no `str`-typed field carries the planted secret. That test breaks the day someone
adds a fourth text field without masking it — the actual failure mode being guarded.

**Concurrent change ([C5](README.md#c5--the-ansimask-fast-path-is-one-change-claimed-twice)):**
`mask()` is currently called **twice per line**. Since only 1 of 2323 anchor lines contains
an ESC byte, the second call is pure waste on 99.96% of lines. Compute the ANSI-stripped
form first (preserving the current strip-then-mask order, which is what catches a token
split by an embedded escape), then reuse it when no ESC was present. Halves masking cost.

### 3.3 What must remain readable — the evidence-preservation rules

Masking that destroys evidence is a bug. Each rule is backed by a validated no-match case:

- **R1 — Paths are always evidence, never secrets.** File paths, Windows drive paths
  (`D:\a\_temp\…`), paths containing `secret`/`token`/`key` (`tests/test_secrets.py`,
  `~/.ssh/id_rsa`), runner temp paths — never masked. Enforced structurally: no rule
  matches a bare word; rule 8 requires the keyword immediately adjacent to `=`, so
  `SSH_KEY_PATH=/home/runner/.ssh/id_rsa` survives intact. A path is masked *only* when it
  is the value of a credential-shaped assignment, and that is correct.
- **R2 — Test ids survive verbatim.** `tests/test_types.py::test_file_surrogates[type1]` —
  validated no-match. Parametrized ids containing `=` (`test_foo[api_key=abc]`) — validated
  no-match via the `[`-excluding boundary class in rule 8. `Diagnostic.test_id` is the
  primary correlation key for Section 5 and Slice 3.
- **R3 — Line numbers, columns, exception types, exit codes, error codes and rule ids are
  structurally unmaskable.** Integers or short identifiers extracted by capture group; no
  rule can match them.
- **R4 — Assertion text, `Expected:`/`Received:` values and stack frames stay readable.**
  They *could* be masked, but only if they contain a credential-shaped literal, in which
  case masking is correct. Nothing in the matrix matches prose.
- **R5 — Content-addressed identifiers are NOT secrets.** Git SHAs (40 hex), action pins
  (`actions/checkout@3d3c42e5aac5…` — validated no-match), UUIDs
  (`Worker ID: {f7d8261d-…}` — validated no-match), container digests, cache keys, the
  `git-credentials-52fe931a-….config` temp filename. All present in the anchor fixture, all
  evidence, all preserved. **This rule is what rejects entropy heuristics.**
- **R6 — Private-key *bodies* are deliberately not line-masked.** Rule 2 masks the armor
  line. The base64 body lines are not individually recognizable as secret without cross-line
  state, and adding cross-line state to a per-line masker is a much bigger change. Named as
  a **known ceiling**, not an oversight: in practice a leaked PEM in a CI log is almost
  always a single-line `$'…\n…'` shell expansion, which rule 2 catches at the head. Mark in
  code: `# ponytail: header-only; cross-line PEM body masking needs stateful masking`.

### 3.4 Rejecting entropy heuristics (explicit)

The spec allows a fully-specified deterministic entropy rule. **We ship none.** Argued from
the data:

A canonical rule (Shannon entropy ≥ 4.0 bits/char over a ≥20-char `[A-Za-z0-9+/=_-]` run)
applied to the anchor fixture would mask: six 40-char action-pin SHAs, the runner Worker ID
UUID, the git-credentials temp filename UUID, every uv/pip wheel hash, and the setup-python
cache key. That is a golden-snapshot rewrite, destroyed action-version evidence, and
destroyed cache-debugging evidence — in exchange for catching a hypothetical unknown-format
secret that has never appeared in any fixture. **The false-positive cost is not merely
high, it is concentrated exactly on the evidence the engine exists to preserve.**

If ever revisited it must be (a) opt-in and default-off, (b) exclude 32/40/64-char pure-hex
and RFC-4122 UUID shapes by rule, and (c) justified by a real captured leak.

### 3.5 Mask-token format

Format: `«REDACTED:<class_name>»`. Keep the guillemets — do not change to ASCII brackets.

- **Stable.** The literal is a module constant, referenced by tests via
  `sanitize.MASK_TOKENS[...]`, never re-typed as a string literal. Golden snapshots embed
  these tokens, so the format is frozen alongside `SCHEMA_VERSION`.
- **Non-forgeable.** `«`/`»` (U+00AB/U+00BB) effectively never appear in CI tool output, so
  log content cannot fabricate a token a downstream consumer would mistake for a real
  redaction. ASCII `[REDACTED:…]` could be. This collision-resistance is a real security
  property and is why the non-ASCII choice stays.
- **Non-reversible.** A token carries the *class* and nothing else — no prefix, suffix,
  length, hash or character count. Two different secrets of the same class produce
  byte-identical output. The structural exceptions are rule 3 (preserves `://` and `@` —
  URL shape, not secret material) and rule 8 (preserves the *variable name*, e.g.
  `AWS_SECRET_ACCESS_KEY=«REDACTED:env_secret»`) — knowing *which* variable was set is
  high-value debugging evidence and leaks nothing about the value.
- **Idempotent.** No mask token matches any rule, so `mask(mask(x)) == mask(x)`. Pinned by
  a test — this is what would make a future output-boundary pass safe.
- **ASCII-safe on output.** `to_json` uses `ensure_ascii=True`, so `«` serializes as
  `\u00ab`. Constraint for the docstring: **never `print()` a raw `Diagnostic.message` /
  `LogSection.title`** — only `to_json` output — because a cp1252 Windows console would
  raise on the guillemets. `cli.py:46` already complies.

### 3.6 CLI/stdout

`cli.py:46` prints `to_json(report)` only; every string in it is `LogLine`-derived and
therefore masked. No other `print` in `src/` touches report content. LEAK-2 (`cli.py:91`)
is the one stderr path that can carry a signed URL; fix by printing the exception *type*
and request path rather than `str(e)`.

---

## 4. Contract changes

All additive. No field removed, no type changed, no signature broken.

### 4.1 `SCHEMA_VERSION`: `"1.2"` → `"1.3"`

Per [C1](README.md#c1--schema_version-chain). Section 7 changes two observable things:
(a) the key set of `stats`, and (b) the *content* of free-text fields
(`LogSection.title`, `Diagnostic.message`) for any log containing a newly-covered secret
class. **(b) is the real reason:** a consumer diffing two reports across this change would
see message-text differences it cannot attribute to anything in the schema shape. The
version is the cheapest possible attribution mechanism.

### 4.2 `sanitize.py` — new public surface

```python
"""Deterministic secret masking, applied once at the LogLine construction
choke point in normalizer.py. Rules are ORDERED: the first rule that matches
a span wins, so more specific classes (github_token) must precede more
general ones (bearer). The byte-faithful .log fixture on disk is never
rewritten; this only touches in-memory LogLine text fields.

Rule-selection principle: a rule ships only if it has near-zero false-positive
risk against real CI log text. Entropy heuristics are deliberately excluded
(they mask git SHAs, UUIDs and cache keys -- see docs/plans/section-7-security.md).
"""

import re

# (class_name, compiled_pattern, replacement_template)
_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (...)

MASK_TOKENS: dict[str, str] = {name: ... for name, _, _ in _RULES}
"""class name -> the literal mask token it emits. Tests reference this rather
than re-typing string literals: the token format is frozen because committed
golden snapshots embed it."""

SECRET_CLASSES: frozenset[str] = frozenset(MASK_TOKENS)


def mask(text: str) -> str:
    """Unchanged signature -- existing callers and tests keep working."""
    return mask_counted(text)[0]


def mask_counted(text: str) -> tuple[str, dict[str, int]]:
    """mask() plus a per-class substitution count, for stats["secrets_masked"].

    Uses Pattern.subn so counting is free (no second scan). The returned dict
    contains only classes that actually fired.
    """
```

`mask_counted` is the only new function; `mask` becomes a one-line wrapper, so
`tests/test_parsing_sanitize.py` needs no rewrite.

### 4.3 `normalizer.py` — changed block

```python
        timestamp, payload = provider.split_line(raw)

        # Masking before marker extraction is not possible (masking could alter
        # the "##[group]" prefix shape), so marker_body must be masked
        # separately: it is derived from the raw payload and flows into
        # LogSection.title (segmentation.py) and Diagnostic.message
        # (process_failure.py). That was a real leak until the Section 7 hotfix.
        marker, marker_body = provider.marker_of(payload)

        # ANSI is stripped BEFORE masking so an escape embedded inside a token
        # cannot split it past the pattern. When the payload has no ESC byte
        # (99.9%+ of real lines) the stripped form IS the payload, so one
        # mask() call serves both fields.
        stripped = _ANSI_RE.sub("", payload)
        text, counts = mask_counted(stripped)
        if "\x1b" in payload:
            raw_text, raw_counts = mask_counted(payload)
            _merge_counts(counts, raw_counts)   # same spans; take the max per class
        else:
            raw_text = text
        if marker_body is not None:
            marker_body = mask(marker_body)
        _merge_counts(secrets_masked, counts)
```

`normalize`'s returned `stats` gains one key: `"secrets_masked": dict[str, int]`. Its
signature is unchanged.

> **Counting note:** `text` and `raw_text` cover the same spans, so counts must be merged
> as a per-class **max**, not a sum, or every masked line double-counts. State this in the
> helper's docstring — it is the kind of detail that silently doubles a metric.

### 4.4 `pipeline.py` — final `stats` schema

```python
def _base_stats() -> dict:
    """The full stats key set, zero-valued. Both the success path and the
    fatal path emit exactly these keys, so a consumer never has to branch on
    key presence (today `stats["fatal"]` exists only on the fatal path).

    Every value here is a count, a bool, or an identifier drawn from a closed
    set. No value is ever derived from log text -- see invariant S7-M1 and
    tests/test_parsing_stats.py.
    """
    return {
        # --- volume ---
        "logs_processed": 1,
        "lines_processed": 0,
        "bytes_processed": 0,
        "sections": 0,
        "unknown_sections": 0,
        # --- detection ---
        "diagnostics_detected": 0,
        "diagnostics_deduplicated": 0,   # OWNED BY SECTION 5 -- default only, never clobber
        "clusters_built": 0,             # owned by Section 5
        "parser_selected": None,         # "specialized" | <parser.name> | None
        "parsers_fired": [],             # sorted specialized parser names
        "fallback_parser_used": False,
        "parse_failures": 0,
        # --- limits / degradation (Section 6) ---
        "truncated_lines": False,
        "lines_over_limit": 0,
        "diagnostics_truncated": False,
        "truncated_chars": False,
        "evidence_truncated": False,
        "tail_rescued": False,
        "peak_lines_retained": 0,
        "fatal": False,
        # --- security ---
        "secrets_masked": 0,
        "secrets_masked_by_class": {},   # class name -> count, non-zero only
        # --- non-deterministic bucket (excluded from golden snapshots) ---
        "runtime": {"parse_ms": 0},
    }
```

`parser_selected` keeps its exact current values (`"specialized"` / `"generic"` / `None`) —
`test_parsing_pipeline.py:27` depends on it and preserving it costs nothing.
`parsers_fired` is the additive fix for the information `"specialized"` throws away.

> **[C2](README.md#c2--stats-key-naming-collision):** `diagnostics_deduplicated` and
> `clusters_built` are **Section 5's** values. `_base_stats()` supplies the default; the
> clustering stage **overwrites** it. Never assign these unconditionally after clustering —
> that would zero out Section 5's work.

---

## 5. Files & modules

**No new source module.** A `parsing/metrics.py` to build one dict would be an abstraction
with a single caller; the stats dict is built where its data lives.

| File | Change | Why this boundary |
|---|---|---|
| `parsing/sanitize.py` | 3 patterns → 13-rule ordered table; add `mask_counted`, `MASK_TOKENS`, `SECRET_CLASSES` | already *is* the masking boundary; its docstring already declares Section 7 owns the full matrix |
| `parsing/normalizer.py` | single-mask fast path; thread mask counts into the returned stats | the single choke point — one guard at the shared site, not one per parser |
| `parsing/pipeline.py` | `_base_stats()`; both paths emit the full key set; `stats["runtime"]["parse_ms"]`; compute `unknown_sections`, `parsers_fired`, `fallback_parser_used`, `peak_lines_retained`, `bytes_processed` | `stats` already lives here; extending it is a diff, not a framework |
| `parsing/model.py` | one line: `SCHEMA_VERSION = "1.3"` | — |
| `parsing/parsers/__init__.py` | export `PARSER_NAMES = frozenset(p.name for p in PARSER_REGISTRY)` | the stats-invariant test needs the closed set of legal values; deriving it from the registry means it can't drift. Two lines |
| `cli.py` | one line: LEAK-2 — print exception type + request path instead of `str(e)` | leaving a known credential-bearing stderr path open while writing a security section is not defensible |
| `docs/perf-baselines.md` | **re-record** ([C6](README.md#c6--section-7-invalidates-section-6s-perf-baselines)) | 3 → 13 rules against a stage that is ~77% of runtime |

Six source files touched, zero created.

---

## 6. Fixtures

### 6.1 Policy for committed real captures (the vetting rule)

- **P1 — public-source-only.** `tests/fixtures/raw_logs/<owner>/<repo>/` may contain a
  byte-faithful real capture **only** if the source workflow run is readable by an
  anonymous, unauthenticated GitHub user. The bytes are already world-readable at their
  source, so committing them creates no new exposure. This is the entire justification for
  committing real logs and should be written down in `.gitignore`'s note.
- **P2 — never committed.** A capture from a private repo; from an internal/self-hosted
  runner; obtained with a `repo`-scoped PAT against anything non-public; or from a repo the
  author does not control. Add `tests/fixtures/raw_logs/PRIVATE/` to `.gitignore` as a
  deliberate escape hatch, so this is easy to comply with rather than a rule people route
  around.
- **P3 — the executable gate.** A test scans **every** `*.log` under
  `tests/fixtures/raw_logs/` with the shipped matrix and fails unless every match is either
  (a) GitHub's own mask literal `***`, or (b) an approved-synthetic literal per F2. **No
  per-file allowlist** — allowlisting is by *shape*, so there is nothing to go stale. A new
  fixture carrying a live-shaped secret fails the suite the moment it is added.
- **P4 — the human step, honestly labelled.** P3 cannot see what the matrix cannot match:
  an internal hostname, a customer name, a private S3 bucket, an employee email. The
  ingester reads the diff of a new fixture before committing. Document as a checklist line
  in the fixtures README; **do not pretend it is automated.**

### 6.2 The synthetic secret-bearing fixture

Location: `tests/fixtures/raw_logs/SYNTHETIC/secrets-sample/sample.log`, with a section
appended to the existing `SYNTHETIC/README.md` — reusing the directory and marking
convention Sections 3 and 4 established.

Shape: a GitHub-Actions-formatted log (timestamp prefixes, `##[group]`/`##[command]`/
`##[error]` markers) whose lines carry one planted example of each shipped rule —
**including inside `##[group]` and `##[error]` markers**, so it is a permanent regression
fixture for LEAK-1.

**F2 — the fakeness guarantee, mechanically checkable.** Every planted literal must either
(1) contain the ASCII substring `EXAMPLE`, or (2) have a variable body that is a run of a
single repeated character. This is the convention `gitleaks`, `detect-secrets` and GitHub's
push protection allowlist on, and it is human-obvious in review.

| Rule | Planted literal | Why it can't be real |
|---|---|---|
| github_token | `ghp_` + `A`×36 | GitHub PATs carry a CRC32 checksum in their trailing characters; a constant run fails it, so GitHub's scanner will not flag it — while our charset+length regex still matches. Already the convention in `test_parsing_sanitize.py:5` |
| aws_key_id | `AKIAIOSFODNN7EXAMPLE` | AWS's own published documentation example key; allowlisted by every scanner |
| env_secret | `AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY` | AWS's published example secret |
| stripe_key | `sk_test_EXAMPLEFAKE00000` | test-mode prefix (never scanned as live) + `EXAMPLE` |
| slack_token | `xoxb-aaaaaaaaaaaa-aaaaaaaaaaaa-EXAMPLENOTAREALTOKEN` | zero-runs + `EXAMPLE` |
| jwt | base64 of `{"alg":"none"}` / `{"sub":"example"}`, signature `EXAMPLESIGNATURE` | `alg:none`, unverifiable signature |
| private_key | `-----BEGIN RSA PRIVATE KEY-----` + `EXAMPLENOTAREALKEY` | armor header with a non-base64-length body |
| npm / pypi / google | `npm_`+`A`×36, `pypi-…EXAMPLE…`, `AIza`+`EXAMPLE…` padded to length | repeated-run or `EXAMPLE` |
| bearer / http_auth | `Authorization: Bearer EXAMPLEEXAMPLEEXAMPLE` | `EXAMPLE` |
| url_creds | `https://exampleuser:EXAMPLEPASSWORD@github.com/org/repo.git` | `EXAMPLE` |

**F3 — F2 is enforced by a test**, not by good intentions:
`test_synthetic_secret_fixture_contains_only_obviously_fake_secrets` runs the matrix over
the fixture and, for each match, asserts the matched span contains `EXAMPLE` or its body has
≤2 distinct characters. If someone pastes a real token into this fixture, the suite fails
before the commit.

The fixture must also contain **negative-control lines** — a git SHA action pin, a UUID
worker id, a pytest parametrized id containing `=`, an `SSH_KEY_PATH=` assignment, a
`postgres://localhost:5432/test` DSN — so rules R1–R5 have a permanent regression home.

---

## 7. Observability design

### 7.1 Determinism classification

**D** = deterministic, included in golden-snapshot comparison. **N** = excluded.

| Key | Type | Class | Definition / source |
|---|---|---|---|
| `logs_processed` | int | **D** | always `1` (including on the fatal path — a fatal log was still processed). Exists so Slice 6's eval harness can sum `stats` across reports without special-casing a denominator |
| `lines_processed` | int | **D** | `len(lines)` — unchanged |
| `bytes_processed` | int | **D** | `len(content)` in characters. The deterministic input-size metric |
| `sections` | int | **D** | `len(section_list)` — unchanged |
| `unknown_sections` | int | **D** | count of `LogSection` with `title is None` — an anonymous/unbalanced `##[group]`. A genuine segmentation-quality signal. (The alternative reading — "sections not matching a known taxonomy" — is meaningless here: the engine has no section taxonomy. **Stating the chosen definition in the docstring is mandatory**, otherwise the number is uninterpretable) |
| `diagnostics_detected` | int | **D** | unchanged |
| `diagnostics_deduplicated` | int | **D** | **Section 5's.** Section 7 defines only the key, type and default |
| `clusters_built` | int | **D** | **Section 5's.** Same |
| `parser_selected` | str \| None | **D** | unchanged values |
| `parsers_fired` | list[str] | **D** | sorted names of specialized parsers producing ≥1 diagnostic. Recovers what `"specialized"` discards. Values constrained to `PARSER_NAMES` |
| `fallback_parser_used` | bool | **D** | `True` iff a parser with `is_fallback` produced the returned diagnostics |
| `parse_failures` | int | **D** | unchanged |
| `truncated_lines`, `lines_over_limit`, `diagnostics_truncated`, `truncated_chars`, `evidence_truncated`, `tail_rescued` | bool/int | **D** | Section 6's |
| `peak_lines_retained` | int | **D** | max count of `LogLine` objects held simultaneously. **Permanently equals `lines_processed`**, because Section 6 declined streaming ([C7](README.md#c7--peak_lines_retained-justification)). Kept as the deterministic memory proxy; the docstring says so honestly |
| `fatal` | bool | **D** | now always present (`False` on the success path). Removes the `stats.get("fatal")` branch from every consumer |
| `secrets_masked` | int | **D** | total substitutions across the log. Proves masking ran |
| `secrets_masked_by_class` | dict[str,int] | **D** | non-zero classes only. The diagnostic you need when investigating "is a pattern over-firing" — a count keyed by a class name, so it leaks nothing |
| `runtime` | dict | **N** | reserved bucket for wall-clock/host-dependent readings. Ships with exactly one key: `parse_ms: int` |

### 7.2 Resolving `processing_time` vs the golden snapshot

The conflict is real: `test_parsing_golden_snapshot.py:34-37` compares the entire report
dict, so any wall-clock value in `stats` fails on every run.

**Resolution: nest all non-deterministic metrics under one reserved key,
`stats["runtime"]`, and have the golden test drop exactly that key from both sides before
comparing.**

```python
def _drop_nondeterministic(report_dict: dict) -> dict:
    """stats["runtime"] is the ONE bucket holding wall-clock/host-dependent
    readings. Its *shape* is asserted separately (see
    test_stats_runtime_bucket_is_present_and_shaped); only its numeric values
    are excluded here. Everything else is still compared exactly.
    """
    report_dict["stats"].pop("runtime", None)
    return report_dict
```

Why this does not weaken the snapshot:

- Exactly **one** key is excluded, by name, greppable, auditable. Not a suffix convention
  (`*_ms`) that would silently swallow any future field someone happens to name that way.
- The *presence and shape* of `runtime` is asserted by a separate test:
  `set(stats["runtime"]) == {"parse_ms"}`, value is `int`, `>= 0`. A bug that drops timing
  entirely, or adds an unreviewed key to the bucket, still fails the suite.
- Everything else is compared exactly, as today.

Accepted wart: the committed `tests/fixtures/parsed/**.json` carries a stale
`runtime.parse_ms` from whenever it was regenerated. It is ignored by the comparison.
Document that in the golden test's docstring rather than complicating the regeneration
one-liner.

**Rejected alternative:** making timing opt-in via a `ParseLimits` flag defaulting off. It
solves the snapshot problem trivially, but an observability metric that is off by default
is not observability — the number you never collect is the number you never have when you
need it.

### 7.3 Resolving `memory_usage`

**Do not measure real memory.** `import resource` is Unix-only (this repo is developed on
Windows), and `tracemalloc` costs ~2.6× parse time (22 ms → 56 ms on the anchor). Paying
160% on every parse for a number that is non-deterministic, host-dependent, and excluded
from snapshots anyway is not a trade worth making.

Ship the deterministic proxies instead: `bytes_processed` (input size) and
`peak_lines_retained` (objects held). Together they answer the question anyone actually asks
of a memory metric — "did this log blow up, and how big was it?" — deterministically, at
zero cost, and snapshot-safe. Real RSS, if ever wanted, goes into the `stats["runtime"]`
bucket that already exists for it, with no further test changes.

### 7.4 The metrics-cannot-leak-secrets invariant

> **Invariant S7-M1.** Every value reachable from `FailureReport.stats` is one of: `int`,
> `float`, `bool`, `None`; a `str` drawn from a closed, code-defined set
> (`PARSER_NAMES ∪ {"specialized"}` for `parser_selected`/`parsers_fired`,
> `SECRET_CLASSES` for `secrets_masked_by_class` keys); or a `dict`/`list` whose leaves
> recursively satisfy the same rule. **No value in `stats` is ever derived from log text.**

Enforced by two tests: a *content* test (plant a unique sentinel on every line, assert it
appears nowhere in `json.dumps(stats)`) and a *structural* test (recursively walk `stats`,
assert every leaf is a permitted scalar or a member of its declared closed set). **The
structural one is the important one** — it catches "someone put a message in stats" even
when no sentinel happens to be present.

---

## 8. Tests (written first)

### `tests/test_parsing_sanitize.py` — extend

For each of the 13 shipped rules, a mask/no-mask **pair**:

| Test | Asserts |
|---|---|
| `test_mask_redacts_aws_access_key_id` | `AKIAIOSFODNN7EXAMPLE` → mask token present, literal absent |
| `test_mask_leaves_uppercase_word_that_is_not_an_aws_key` | `"AKIA"` alone and `"AKIAIOSFODNN7"` (short) unchanged |
| `test_mask_redacts_jwt` / `test_mask_leaves_base64ish_word_untouched` | 3-segment `eyJ…` masked; a bare `eyJhbGci` fragment unchanged |
| `test_mask_redacts_private_key_header` / `test_mask_leaves_public_key_header_untouched` | `BEGIN RSA PRIVATE KEY` masked; `BEGIN PUBLIC KEY` unchanged |
| `test_mask_redacts_authorization_header` | `AUTHORIZATION: basic QUJDOmRlZg==` masked; the **line-79 shape** from the real fixture is the canonical case |
| `test_mask_redacts_env_secret_assignment_preserving_key_name` | `+ export API_KEY=…` → key name **still present**, value absent |
| `test_mask_leaves_pytest_parametrized_id_with_equals_untouched` | `tests/t.py::test_x[api_key=abc]` **unchanged** — the R2 guard |
| `test_mask_leaves_path_valued_env_var_untouched` | `SSH_KEY_PATH=/home/runner/.ssh/id_rsa` **unchanged** — the R1 guard |
| `test_mask_leaves_git_sha_and_uuid_untouched` | action pin and worker UUID **unchanged** — the R5 / anti-entropy guard |
| `test_mask_leaves_credential_free_db_dsn_untouched` | `postgres://localhost:5432/test` unchanged — pins the rule-14 deferral |
| `test_mask_redacts_slack_stripe_npm_pypi_google_tokens` | parametrized over the five provider rules |
| `test_mask_is_idempotent` | `mask(mask(s)) == mask(s)` over every planted literal |
| `test_mask_rule_order_prefers_specific_class` | `Bearer ghp_AAAA…` → `github_token`, not `bearer_token`. Pins the ordering contract |
| `test_mask_counted_reports_per_class_counts` | two GitHub tokens + one AWS key on one line → `{"github_token": 2, "aws_key_id": 1}` |
| `test_mask_tokens_are_stable_constants` | every value matches `«REDACTED:<class>»`; `set(MASK_TOKENS) == SECRET_CLASSES` |

### `tests/test_parsing_secret_leak_channels.py` — new

The first two land in the **Step 0 hotfix** and are carried here as permanent regressions.

| Test | Asserts |
|---|---|
| `test_group_marker_body_is_masked_in_section_title` | `report.sections[0].title` lacks the literal, has the mask token |
| `test_error_marker_body_is_masked_in_process_failure_message` | message masked **and `exit_code == 1` still extracted** |
| `test_command_marker_body_is_masked` | `##[command]git config … "AUTHORIZATION: basic <blob>"` masked; `JsTestParser.detect`/`CompilerParser.detect` still see their `marker_body` tool keywords |
| `test_every_logline_str_field_is_masked` | **structural guard** — for every `str`-typed field in `dataclasses.fields(LogLine)`, the planted literal is absent. Fails when a future text field is added without masking |
| `test_no_known_secret_literal_survives_into_to_json` | **the headline invariant.** For each of the 13 classes, plant its literal in five positions (plain line, `##[group]`, `##[error]`, `##[command]`, and inside a pytest `FAILURES` block so it reaches `Diagnostic.message`/`StackFrame.raw_text`); assert absent from `to_json`. 65 assertions from one parametrized test |
| `test_masking_survives_ansi_escape_inside_token` | `ghp_AAA\x1b[0mAAA…` → the reassembled token is masked (proves the strip-then-mask order survives the fast-path refactor) |

### `tests/test_parsing_stats.py` — new

| Test | Asserts |
|---|---|
| `test_stats_has_exactly_the_documented_key_set` | `set(report.stats) == EXPECTED_KEYS` |
| `test_fatal_report_has_the_same_stats_key_set` | monkeypatch `normalize` to raise; same key set, `fatal is True`, `logs_processed == 1` |
| `test_stats_runtime_bucket_is_present_and_shaped` | `set(stats["runtime"]) == {"parse_ms"}`; `int`; `>= 0`. Keeps the snapshot exclusion honest |
| `test_stats_leaf_types_are_scalars_or_closed_set_strings` | **S7-M1 structural** |
| `test_stats_never_contains_log_text` | **S7-M1 content** — every line embeds a unique sentinel; no sentinel in `json.dumps(report.stats)` |
| `test_parsers_fired_names_specialized_parsers` | pytest fixture → `["pytest"]`, `fallback_parser_used is False` |
| `test_fallback_parser_used_true_for_generic_only_log` | bare `path:line: error:` → `True`, `parsers_fired == []` |
| `test_unknown_sections_counts_anonymous_groups` | empty-bodied `##[group]` → `1`; titled → `0`. Pins the chosen definition |
| `test_secrets_masked_counts_match_planted_secrets` | 3 tokens across 2 lines → `secrets_masked == 3`, `{"github_token": 3}` |
| `test_secrets_masked_is_zero_for_clean_log` | `0` and `{}`. Guards against a pattern firing on ordinary text |
| `test_section5_owned_stats_keys_are_not_clobbered` | **[C2](README.md#c2--stats-key-naming-collision) guard** — on the click fixture, `diagnostics_deduplicated` and `clusters_built` hold Section 5's computed values, not `_base_stats()`'s zeros |
| `test_bytes_and_peak_lines_are_deterministic_across_runs` | two parses → identical values for every key except `runtime` |

### `tests/test_fixture_secret_policy.py` — new

| Test | Asserts |
|---|---|
| `test_committed_raw_log_fixtures_contain_no_live_shaped_secrets` | P3 over every `raw_logs/**/*.log`. Passes today with exactly one match (`AUTHORIZATION: basic ***`) |
| `test_committed_parsed_fixtures_contain_no_live_shaped_secrets` | same over `parsed/**/*.json` — the *output* fixtures, the ones published in a public repo |
| `test_synthetic_secret_fixture_contains_only_obviously_fake_secrets` | F3 |
| `test_synthetic_secret_fixture_exercises_every_shipped_rule` | every name in `SECRET_CLASSES` fires at least once. Prevents a rule shipping without a fixture |
| `test_synthetic_secret_fixture_parses_to_a_fully_masked_report` | end-to-end: none of the planted literals in `to_json`, and still a `PROCESS_FAILURE` with the right `exit_code` (masking preserved parseability) |

### `tests/test_parsing_fuzz_security.py` — extend

Deterministic pseudo-randomness via `random.Random(20260827)` — property-based *style*
without adding `hypothesis`.

| Test | Asserts |
|---|---|
| `test_planted_secret_never_survives_random_surroundings` | 500 iterations: random class literal wrapped in random junk (random prefix/suffix, random marker prefix from `{"", "##[group]", "##[error]", "##[command]", "[command]"}`, random timestamp presence, random ANSI noise); literal absent from `to_json` |
| `test_planted_secret_never_survives_random_line_position` | 200 iterations: insert at a random index inside a copy of the anchor's first 200 lines |
| `test_mask_never_raises_on_random_bytes` | `mask` over 1000 random latin-1 strings never raises, always returns `str` |
| `test_parse_log_still_never_raises_with_full_matrix` | the total-function guarantee re-asserted against the expanded matrix |
| `test_no_rule_exhibits_pathological_backtracking` | each rule against 20,000-char adversarial strings (`"a"*20000`, `"A"*10000+"="`, `"://"+"a"*10000`) under a generous bound. `max_line_length` is 20,000, so that is the real worst case |

### `tests/test_parsing_golden_snapshot.py` — edit

- `test_golden_snapshot_matches_committed_json` → apply `_drop_nondeterministic` to both sides
- `test_key_facts_about_the_real_failure` → `schema_version == "1.3"`
- **New** `test_golden_snapshot_masked_text_is_unchanged_by_section_7`: `LogSection.title`
  values and all `Diagnostic.message` values equal the committed pre-Section-7 values.
  **The anti-over-masking canary on real data** — what proves the matrix expansion didn't
  quietly rewrite the anchor's evidence
- docstring note: the committed `stats.runtime.parse_ms` is a stale reading, ignored by comparison

### `tests/test_cli_parse.py` — extend

- `test_cli_parse_output_is_pure_ascii_and_contains_no_planted_secret` — run
  `main(["parse", <secrets fixture>])`, assert all `ord(c) < 128` and no planted literal.
  Closes the "nothing unmasked reaches stdout" claim end-to-end.

---

## 9. Implementation steps (ordered TDD checklist)

> Steps 1–2 of the original design shipped in the **Step 0 hotfix**. This section starts
> from a codebase where `marker_body` is already masked.

1. **Write the full unit matrix tests** in `tests/test_parsing_sanitize.py` — all 13
   mask/no-mask pairs plus ordering, idempotence and the R1/R2/R5 over-masking guards. All
   new ones fail (rules don't exist).
2. **Implement the rule table** in `sanitize.py`: convert the three loose module constants
   into the ordered `_RULES` tuple, add the ten new rules, derive
   `MASK_TOKENS`/`SECRET_CLASSES`, add `mask_counted`, reduce `mask` to a wrapper. Step 1
   goes green. **The four pre-existing sanitize tests must pass unmodified** — if they
   don't, a rule regressed an existing guarantee.
3. **Verify zero regression on real data.** Run the golden snapshot. It must pass
   **unchanged at this point** (no stats work yet). If it fails, a new rule is over-masking
   real evidence — **fix the rule, not the snapshot.**
4. **Apply the single-mask fast path** ([C5](README.md#c5--the-ansimask-fast-path-is-one-change-claimed-twice))
   in `normalizer.py`. Add `test_masking_survives_ansi_escape_inside_token` **first** — it
   is the test that catches getting the order backwards.
5. **Add the structural guards** `test_every_logline_str_field_is_masked` and
   `test_no_known_secret_literal_survives_into_to_json`. Both should now pass; if either
   fails, there is a leak channel this plan missed — **stop and trace it.**
6. **Build the synthetic fixture** and `tests/test_fixture_secret_policy.py`.
   `test_synthetic_secret_fixture_exercises_every_shipped_rule` tells you which rules the
   fixture is still missing.
7. **Write the observability tests** (`tests/test_parsing_stats.py`) — all fail.
8. **Implement `_base_stats()`** in `pipeline.py`; wire both success and fatal paths
   through it; add the `runtime` bucket with `perf_counter`-based `parse_ms`; compute
   `unknown_sections`, `parsers_fired`, `fallback_parser_used`, `peak_lines_retained`,
   `bytes_processed`; thread `secrets_masked`/`secrets_masked_by_class` up from
   `normalize`. Add `PARSER_NAMES` to `parsers/__init__.py`. **Confirm
   `test_section5_owned_stats_keys_are_not_clobbered` passes** ([C2](README.md#c2--stats-key-naming-collision)).
9. **Bump `SCHEMA_VERSION` to `"1.3"`.**
10. **Update the golden snapshot test**: add `_drop_nondeterministic`, update the version
    assertion, add the masked-text canary. Then regenerate the committed snapshot —
    **only** to absorb the intentional `stats`/`schema_version` change. Review the diff
    line by line: **the only changes permitted are inside the `stats` block and the
    `schema_version` field.** Any change to a `title`, `message`, `file` or `test_id` value
    is an over-masking bug, not a snapshot update.
11. **Extend the fuzz suite** with the seeded property-style tests and the ReDoS smoke test.
12. **Close LEAK-2**: one line in `cli.py:91`, plus the pure-ASCII CLI test.
13. **Re-record `docs/perf-baselines.md`** ([C6](README.md#c6--section-7-invalidates-section-6s-perf-baselines))
    with the masking-cost delta called out.
14. **Update the docs**: `CLAUDE.md` §2 (tick Section 7, note the `SCHEMA_VERSION` bump,
    the `stats.runtime` snapshot-exclusion convention, **and the
    [C8](README.md#c8--confidence-calibration-is-descoped-and-must-be-recorded-as-such)
    confidence-calibration re-scoping note**), `CLAUDE.md` §7 (fixture vetting rules
    P1–P4), `.gitignore` (add `tests/fixtures/raw_logs/PRIVATE/` and the public-source-only
    rule), `docs/project-brief.md`.

---

## 10. Acceptance criteria

1. `uv run pytest` green, and **no pre-existing test modified** except
   `test_parsing_golden_snapshot.py` (version assertion + `runtime` exclusion) and the
   regenerated `tests/fixtures/parsed/**.json`.
2. Planting any of the 13 example literals in **any** of {plain line, `##[group]` body,
   `##[error]` body, `##[command]` body, pytest `FAILURES` block} → the literal appears
   **nowhere** in `to_json(report)`. (65 cases.)
3. `LogSection.title` and `Diagnostic.message` for the anchor fixture are
   **byte-identical** to their pre-Section-7 values.
4. `tests/test_types.py::test_file_surrogates[type1]` is still the anchor's
   `primary_cluster.primary.test_id`; `file == "tests\\test_types.py"`, `line == 288`,
   `report.exit_code == 1`.
5. Scanning every committed `*.log` and `*.json` under `tests/fixtures/` with the shipped
   matrix yields only `***` matches or F2-compliant synthetic literals.
6. `set(report.stats)` is identical between the success path and the fatal path, and equals
   the documented key set.
7. Two consecutive `parse_log` calls on identical input produce identical `stats` for every
   key except `stats["runtime"]`.
8. `diagnostics_deduplicated` and `clusters_built` hold Section 5's computed values on the
   click fixture, not `_base_stats()`'s zeros.
9. Every leaf value in `stats` is a scalar or a member of a code-defined closed set; no
   log-derived text appears in `json.dumps(report.stats)` for a sentinel-per-line log.
10. `SCHEMA_VERSION == "1.3"`; `parse_log`, `to_json`, `to_dict`, `ParseLimits`,
    `FailureReport`, `DiagnosticType` signatures and fields unchanged.
11. `parse_log` still never raises: the full fuzz suite plus the ReDoS smoke test pass.
12. No new runtime dependency; `pyproject.toml` `dependencies` unchanged.
13. Zero new source modules; six existing source files changed.
14. `docs/perf-baselines.md` carries a post-Section-7 column.
15. `CLAUDE.md` §2 carries the confidence-calibration re-scoping note.

---

## 11. Potential regressions & risks

**R1 — Over-masking breaks a parser or destroys evidence.** *Highest-consequence risk.*
Mitigated by measurement: the full 13-rule matrix was run against all five committed
fixtures (2414 lines) → **1 match, 0 false positives**, and the produced report is
byte-identical to today's. The three highest-risk shapes pass through unchanged: pytest
parametrized ids containing `=`, path-valued env vars, and content-addressed hex/UUIDs.
Residual: a repo whose logs contain shapes absent from our fixtures. The permanent control
is acceptance criterion 3 — the anchor-fixture text canary, which fails loudly on any
future rule addition that touches real evidence. **New rules must be validated against the
fixture corpus before merge.**

**R2 — Golden-snapshot churn.** Bounded and reviewable: exactly the `stats` block and
`schema_version` change; nothing else may. Step 10 makes that a review gate rather than a
rubber stamp. The `stats.runtime` exclusion is the one place snapshot coverage is
deliberately reduced, compensated by an explicit shape assertion.

**R3 — Per-line regex cost, and the Section 6 interaction.** Measured on the 2323-line
anchor: 3 rules = 8.5 ms/pass; 13 rules = **49 ms/pass**. `mask` is called twice per line
today, so today's real cost is ~17 ms and the naive new cost would be ~98 ms. The
single-mask fast path brings it back to ~49 ms — parse time roughly triples for the anchor.
At the 200,000-line ceiling that extrapolates to ~4 s of masking. Acceptable now (real CI
logs are 2–20k lines). Two facts for the baselines:

- Masking is **per-line, stateless and order-independent across lines**, so it would drop
  into a streaming loop unchanged.
- A combined single-alternation regex was benchmarked as an optimization and is **worse**:
  147 ms vs 49 ms (Python's `re` optimizes each pattern's literal prefix but not an
  alternation of them). **Do not "optimize" that way.** A `search()`-then-`sub()` gate was
  also benchmarked and was slower still (272 ms) because the search duplicates the scan. If
  masking ever becomes the bottleneck, try a cheap literal prefilter gate — **with a
  measurement first**.

**R4 — ReDoS.** Every rule is anchored on a literal prefix or a fixed-length class with
bounded quantifiers; none nests an unbounded quantifier inside another.
`max_line_length = 20 000` bounds the worst case. Covered by
`test_no_rule_exhibits_pathological_backtracking`.

**R5 — Mask-token encoding on a Windows console.** `«»` are non-ASCII. `to_json`'s
`ensure_ascii=True` makes CLI output safe, but any future code that `print()`s a raw
`Diagnostic.message` on a cp1252 console will raise `UnicodeEncodeError`. Documented as a
constraint in `sanitize.py`'s docstring and covered by the pure-ASCII CLI test. This repo
has already been bitten by exactly this class of bug (see `model.py`'s docstring), so it is
a live risk, not a theoretical one.

**R6 — Section 5 collision on the stats keys.** Section 7 defines only the key, type and
default. `_base_stats()` must not clobber values Section 5 computes — implement as
"default in `_base_stats()`, overwritten by the clustering stage." Guarded by
`test_section5_owned_stats_keys_are_not_clobbered`.

**R7 — `test_parse_log_stats_and_metadata_populated`** (`test_parsing_pipeline.py:24-29`)
asserts `parser_selected == "generic"` and a `schema_version`. The version line needs
updating; `parser_selected` is deliberately preserved so the rest is untouched. Flagged so
it is a conscious edit, not a surprise.

**R8 — False sense of completeness.** A 13-rule regex matrix will not catch every secret,
and a green suite can read as "secrets are handled." The compensating control is the
fixture policy (§6) — the public-source-only rule is what actually bounds exposure. Say so
in the docstring and in `CLAUDE.md`, so a future reader does not over-trust the masker.

---

## 12. Explicitly deferred

- **Rules 14–16 of the matrix**: credential-free DB DSNs, Azure/GCP service-account JSON,
  Twilio/SendGrid/Datadog/Vault/Docker-Hub tokens, generic `?apikey=` query params. Each is
  one tuple entry + one fixture line + one test pair. Add when a real capture shows one, not
  because a category list exists.
- **Entropy-based heuristics** — rejected outright with data (§3.4), not deferred pending
  effort. Any revival must be opt-in, default-off, and exclude hex/UUID shapes.
- **Cross-line PEM body masking** — rule 2 masks the armor header only. Marked in code with
  a `ponytail:` comment naming the ceiling.
- **A runtime output-boundary re-mask pass** — deliberately not built (§3.2). The structural
  `LogLine` test provides the same guarantee at zero runtime cost. If a future section
  introduces a diagnostic-text source that is not `LogLine`-derived, that is when this earns
  its place.
- **Real memory measurement (RSS / `tracemalloc`)** — declined (§7.3). The
  `stats["runtime"]` bucket exists to receive it with no further test changes.
- **`diagnostics_deduplicated` / `clusters_built` computation** — Section 5 owns them.
- **Confidence calibration** — `docs/project-brief.md` lists this under Section 7; this
  design covers security + observability only and leaves `confidence.py` untouched.
  **[C8](README.md#c8--confidence-calibration-is-descoped-and-must-be-recorded-as-such):
  Section 7 is not marked complete without an explicit re-scoping note in `CLAUDE.md` §2.**
  It needs either its own increment or a documented drop.
- **Secret scanning as a CI check on this repo** — the fixture-policy tests run under
  `uv run pytest` like everything else. Wiring GitHub Actions for this repo remains
  polish-checklist scope.
- **Redaction auditing / reversible masking / a redaction log** — never. Storing anything
  that could reconstruct a masked value defeats the purpose.
- **Masking the on-disk raw log** — never. `save_raw_log` stays byte-faithful; that is what
  makes the fixtures real regression evidence. Exposure is controlled by the commit policy
  (§6), not by rewriting evidence.
