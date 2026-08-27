"""Deterministic secret masking, applied once at the LogLine construction
choke point in normalizer.py. The byte-faithful .log fixture on disk is
never rewritten; this only touches in-memory LogLine text fields.

Rule-selection principle: a rule ships only if it has near-zero
false-positive risk against real CI log text. Entropy heuristics are
deliberately excluded -- applied to the anchor fixture, a canonical entropy
rule masks six action-pin SHAs, two UUIDs, every wheel hash and the cache
key, i.e. exactly the evidence this engine exists to preserve. See
docs/plans/section-7-security.md Sec 3.4.

ORDERING IS PART OF THE CONTRACT. Rules run in tuple order via sequential
`Pattern.subn` calls, each rule seeing the text already rewritten by every
earlier rule -- so an earlier rule's mask token can never be re-matched by
a later, more generic rule (that would misattribute the class and, worse,
could make a rule appear not to have fired at all on a fixture where it
only ever occurs in that position). Concretely:

  1 github_token, 2 private_key, 3 url_creds, 4 http_auth, 5 bearer_token,
  6 jwt, 7 aws_key_id, 8 slack_token, 9 stripe_key, 10 npm_token,
  11 pypi_token, 12 google_api_key, 13 env_secret

Two deliberate, tested deviations from a naive "value-shape specific rules
first" reading of the design doc's rule table:

  * `env_secret` (rule 8 in the design doc's table) runs LAST, not 8th.
    It is the most generic rule in the matrix -- it matches on the
    VARIABLE NAME only ("...TOKEN=", "...SECRET=", ...) and swallows
    whatever non-whitespace/quote text follows the "=", regardless of that
    value's shape. Every other rule identifies a secret by its VALUE's
    shape. If env_secret ran earlier, "token=ghp_AAAA..." would be
    consumed by env_secret before github_token ever saw it (matching
    keyword "TOKEN"), which both misclassifies it and breaks the
    pre-existing `test_mask_redacts_ghp_token` guarantee. Running it last
    lets every shape-specific rule claim its value first; env_secret only
    fires on genuinely un-shaped secrets (a plain env-var value with no
    dedicated rule, e.g. AWS_SECRET_ACCESS_KEY's opaque base64 blob).
  * `env_secret`'s value group has a negative lookahead for the mask-token
    prefix (see MASK_TOKENS below) -- it refuses to match a value that is
    already a mask token. Without this guard, even running last,
    env_secret would re-swallow (and misattribute) a value an earlier rule
    already masked whenever the variable name is *also* keyword-shaped,
    e.g. `.npmrc`'s `:_authToken=<npm token>` (the npmrc keyword ends in
    "Token", so env_secret's own pattern would otherwise match it after
    npm_token already fired).
  * `http_auth`'s scheme match excludes "bearer" (`(?!bearer\b)`, case
    insensitive) because Bearer has its own, more specific rule (5) with a
    tighter, RFC-6750-scoped charset and length floor. Without the
    exclusion, "Authorization: Bearer <token>" would always be claimed by
    the generic http_auth rule before bearer_token got a chance --
    breaking the pre-existing `test_mask_redacts_bearer_token` guarantee
    and the ordering contract's own worked example ("Bearer ghp_AAAA...
    masks as github_token, not bearer_token, because the more specific
    rule runs first" -- the same principle applied one level up).

R6 (docs/plans/section-7-security.md Sec 3.3): rule 2 masks the PEM armor
header line only -- see the ponytail comment on that rule below.

Mask-token format is frozen: guillemet-quoted, "REDACTED:<class>", e.g.
"github_token" masks as the literal below. Keep the guillemets -- they
effectively never appear in CI tool output, so log content cannot forge a
token a downstream consumer would mistake for a real redaction; ASCII
"[REDACTED:...]" could be forged this way. The token carries only the
class name -- no prefix, suffix, length or hash of the original value --
so two different secrets of the same class produce byte-identical output
(non-reversible).

CONSTRAINT FOR CALLERS: `to_json` serializes with `ensure_ascii=True`, so
the guillemets survive as `«`/`»` in JSON output. Never `print()`
a raw `Diagnostic.message` or `LogSection.title` directly (only `to_json`
output) -- a cp1252 Windows console raises `UnicodeEncodeError` on them.
"""

import re

_TOKEN_TEMPLATE = "«REDACTED:{}»"


def _tok(name: str) -> str:
    return _TOKEN_TEMPLATE.format(name)


# (class_name, compiled_pattern, replacement_template). See the module
# docstring for why this exact order and not the design doc's raw row
# numbering.
_RULES: tuple[tuple[str, "re.Pattern[str]", str], ...] = (
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
        _tok("github_token"),
    ),
    (
        "private_key",
        # ponytail: header-only; cross-line PEM body masking needs stateful
        # masking across LogLine boundaries -- upgrade only if a real
        # leaked-body case ever shows up (see R6, module docstring).
        re.compile(r"-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----"),
        _tok("private_key"),
    ),
    (
        "url_creds",
        re.compile(r"://[^\s:@/]+:[^\s@/]+@"),
        f"://{_tok('url_creds')}@",
    ),
    (
        "http_auth",
        # (?!bearer\b) -- Bearer has its own, more specific rule (below).
        re.compile(r"(?i)\b(?:proxy-)?authorization\s*:\s*(?!bearer\b)[A-Za-z]+\s+[^\s\'\"]+"),
        f"Authorization: {_tok('http_auth')}",
    ),
    (
        "bearer_token",
        # {8,} + the RFC-6750 token68 charset: today's `\bBearer\s+\S+`
        # masked the next word of any English sentence containing
        # "bearer" ("the bearer of bad news"); this does not.
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
        f"Bearer {_tok('bearer_token')}",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{4,}"),
        _tok("jwt"),
    ),
    (
        "aws_key_id",
        re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|APKA)[0-9A-Z]{16}\b"),
        _tok("aws_key_id"),
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprse]-[A-Za-z0-9-]{10,}\b"),
        _tok("slack_token"),
    ),
    (
        "stripe_key",
        re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
        _tok("stripe_key"),
    ),
    (
        "npm_token",
        re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
        _tok("npm_token"),
    ),
    (
        "pypi_token",
        re.compile(r"\bpypi-[A-Za-z0-9_-]{32,}"),
        _tok("pypi_token"),
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),
        _tok("google_api_key"),
    ),
    (
        "env_secret",
        # Runs LAST -- see module docstring. Leading boundary class
        # deliberately excludes "[" so a pytest parametrized id like
        # `test_x[api_key=abc]` survives untouched (R2); the keyword must
        # be the SUFFIX of the variable name, immediately adjacent to "=",
        # so `SSH_KEY_PATH=/home/runner/.ssh/id_rsa` survives untouched
        # (R1). The value group's negative lookahead refuses to re-match a
        # value an earlier, more specific rule already masked.
        #
        # "-" is in the class because a CLI flag is an ordinary way to pass a
        # credential: `curl --password=hunter2`, `-Dpassword=x`. Without it
        # the keyword is preceded by "-" and the rule silently misses. It
        # cannot cause an R1/R2 false positive: "[" stays excluded, and
        # "--secret-name=foo" still does not match because "-" is not in the
        # name class, so the name adjacent to "=" is "name", not a keyword.
        re.compile(
            r"(?i)(^|[-\s;&|(:])([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_KEY|PRIVATE_KEY))"
            r"=(?!«REDACTED:)[^\s\'\"]+"
        ),
        r"\g<1>\g<2>=" + _tok("env_secret"),
    ),
)

MASK_TOKENS: dict[str, str] = {name: _tok(name) for name, _, _ in _RULES}
"""class name -> the literal mask token it emits. Tests reference this
rather than re-typing string literals: the token format is frozen because
committed golden snapshots embed it."""

SECRET_CLASSES: frozenset[str] = frozenset(MASK_TOKENS)


def mask(text: str) -> str:
    """Unchanged signature -- existing callers and tests keep working."""
    return mask_counted(text)[0]


def mask_counted(text: str) -> tuple[str, dict[str, int]]:
    """mask() plus a per-class substitution count, for stats["secrets_masked"].

    Uses Pattern.subn so counting is free (no second scan). The returned
    dict contains only classes that actually fired on this call. Rules run
    in the ORDERED sequence above, each seeing the previous rule's output --
    see the module docstring for why that matters and cannot be
    parallelized/reordered casually.
    """
    counts: dict[str, int] = {}
    for name, pattern, repl in _RULES:
        text, n = pattern.subn(repl, text)
        if n:
            counts[name] = n
    return text, counts
