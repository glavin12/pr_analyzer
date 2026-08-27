"""Shared example secret literals for the Section 7 tests.

**Every literal here is deliberately fake** and must stay that way. Each one
either contains the ASCII substring `EXAMPLE`, or has a variable body that is a
run of a single repeated character. That is the convention `gitleaks`,
`detect-secrets` and GitHub push protection allowlist on, so none of these can
trip a real secret scanner, and a reviewer can see the fakeness by eye.

`test_fixture_secret_policy.py::test_synthetic_secret_fixture_contains_only_obviously_fake_secrets`
enforces that mechanically against the fixture. This module is where the
literals are defined once, so four test files reference them rather than
re-typing a credential-shaped string five times.

Keyed by the secret class name each is expected to trigger -- the same names
`sanitize.SECRET_CLASSES` exposes.
"""

# The github_token body is a 36-char run of one character: GitHub PATs carry a
# CRC32 checksum in their trailing characters, so a constant run fails it and
# GitHub's scanner will not flag it -- while our charset+length regex still
# matches. This is already the convention in test_parsing_sanitize.py.
EXAMPLE_SECRETS: dict[str, str] = {
    "github_token": "ghp_" + "A" * 36,
    # Armor header only (rule 2 is header-only by design -- see R6). "EXAMPLE"
    # is not a real key type, which is what makes it obviously synthetic while
    # still exercising the `(?: [A-Z]+)*` branch.
    "private_key": "-----BEGIN EXAMPLE PRIVATE KEY-----",
    "url_creds": "https://exampleuser:EXAMPLEPASSWORD@github.com/org/repo.git",
    "http_auth": "AUTHORIZATION: basic EXAMPLEBASE64BLOB=",
    "bearer_token": "Bearer EXAMPLEEXAMPLETOKEN",
    # base64({"alg":"none"}) . base64({"sub":"example"}) . unverifiable signature
    "jwt": "eyJhbGciOiJub25lIn0.eyJzdWIiOiJleGFtcGxlIn0.EXAMPLESIGNATURE",
    # AWS's own published documentation example key id.
    "aws_key_id": "AKIAIOSFODNN7EXAMPLE",
    # AWS's own published documentation example secret. Rule 8 keeps the
    # variable NAME and masks only the value.
    "env_secret": "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "slack_token": "xoxb-aaaaaaaaaaaa-aaaaaaaaaaaa-EXAMPLENOTAREALTOKEN",
    "stripe_key": "sk_test_EXAMPLEFAKE00000",
    "npm_token": "npm_EXAMPLE" + "A" * 29,
    "pypi_token": "pypi-EXAMPLENOTAREALTOKEN" + "A" * 12,
    "google_api_key": "AIzaEXAMPLE" + "A" * 28,
}

# Real CI-log content that a careless rule would eat. Each is a validated
# no-match: rules R1-R5 in the design say these must stay readable, because
# they are exactly the evidence the engine exists to preserve.
NEGATIVE_CONTROLS: dict[str, str] = {
    # R5 -- content-addressed identifiers are not secrets.
    "action_pin_sha": "actions/checkout@3d3c42e5aac5b8ba9e5c0e0e9b7a0a1a2b3c4d5e",
    "worker_uuid": "Worker ID: {f7d8261d-1a2b-3c4d-5e6f-708192a3b4c5}",
    # R2 -- a parametrized test id containing "=" must survive verbatim.
    "parametrized_test_id": "tests/test_api.py::test_query[api_key=abc]",
    # R1 -- a path-valued env var whose name merely *contains* a keyword.
    "path_valued_env_var": "SSH_KEY_PATH=/home/runner/.ssh/id_rsa",
    # Pins the rule-14 deferral: a credential-free DSN is debugging evidence.
    "credential_free_dsn": "postgres://localhost:5432/test",
}


def is_obviously_fake(span: str) -> bool:
    """F2, mechanically. A matched span is acceptable in a committed fixture
    only if it is self-evidently synthetic: it says EXAMPLE, or its body is a
    long run of a single repeated character.

    The run is measured as the longest single-character run anywhere in the
    span, NOT as the count of distinct characters across the whole span --
    every rule's match necessarily includes its own fixed prefix (`ghp_`,
    `npm_`, `AKIA`, ...), so a distinct-character count can never see the
    body on its own. `ghp_` + `A`*36 is the case that proves it.

    16 is comfortably longer than any run that occurs in a real credential:
    tokens are high-entropy by construction, so this cannot green-light a
    live secret.
    """
    if "EXAMPLE" in span:
        return True

    longest = run = 1
    for previous, current in zip(span, span[1:]):
        run = run + 1 if current == previous else 1
        longest = max(longest, run)
    return longest >= 16
