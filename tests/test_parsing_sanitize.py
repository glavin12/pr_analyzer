from agentic_pr_analyzer.parsing.sanitize import mask


def test_mask_redacts_ghp_token():
    text = "token=ghp_" + "a" * 36
    result = mask(text)
    assert "ghp_" not in result
    assert "«REDACTED:github_token»" in result


def test_mask_redacts_github_pat_token():
    text = "auth: github_pat_" + "B1c2D3e4F5" * 3
    result = mask(text)
    assert "github_pat_" not in result
    assert "«REDACTED:github_token»" in result


def test_mask_redacts_url_credentials():
    text = "cloning https://user123:hunter2secret@github.com/org/repo.git"
    result = mask(text)
    assert "hunter2secret" not in result
    assert "user123" not in result
    assert "«REDACTED:url_creds»" in result
    assert "github.com/org/repo.git" in result


def test_mask_redacts_bearer_token():
    text = "Authorization: Bearer sk-abcdef0123456789"
    result = mask(text)
    assert "sk-abcdef0123456789" not in result
    assert "«REDACTED:bearer_token»" in result


def test_mask_leaves_non_secrets_untouched():
    text = "Successfully set up CPython (3.14.7)"
    assert mask(text) == text


def test_mask_leaves_short_gh_prefixed_words_untouched():
    text = "ghost story, ghp_short"
    result = mask(text)
    assert result == text


# ==========================================================================
# Section 7: the 13-rule matrix. Every rule gets a mask/no-mask PAIR -- the
# no-match half is the one that matters, because the top risk in this section
# is OVER-masking. Masking that destroys evidence is a bug.
# ==========================================================================

import re

import pytest

from agentic_pr_analyzer.parsing.sanitize import (
    MASK_TOKENS,
    SECRET_CLASSES,
    mask_counted,
)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from secret_examples import EXAMPLE_SECRETS, NEGATIVE_CONTROLS  # noqa: E402


def _masked(text: str, cls: str) -> str:
    result = mask(text)
    assert MASK_TOKENS[cls] in result, f"{cls} did not fire on {text!r}"
    return result


# ---------------------------------------------------------------- per-rule pairs


def test_mask_redacts_aws_access_key_id():
    literal = EXAMPLE_SECRETS["aws_key_id"]
    result = _masked(f"key is {literal}", "aws_key_id")
    assert literal not in result


def test_mask_leaves_uppercase_word_that_is_not_an_aws_key():
    for text in ("AKIA", "AKIAIOSFODNN7", "AN AKIA PREFIX ALONE"):
        assert mask(text) == text


def test_mask_redacts_jwt():
    literal = EXAMPLE_SECRETS["jwt"]
    assert literal not in _masked(f"token {literal}", "jwt")


def test_mask_leaves_base64ish_word_untouched():
    for text in ("eyJhbGci", "eyJ", "the eyJhbGciOiJub25lIn0 fragment"):
        assert mask(text) == text


def test_mask_redacts_private_key_header():
    assert "PRIVATE KEY" not in _masked(
        "-----BEGIN RSA PRIVATE KEY-----", "private_key"
    )


def test_mask_leaves_public_key_header_untouched():
    for text in ("-----BEGIN PUBLIC KEY-----", "-----BEGIN CERTIFICATE-----"):
        assert mask(text) == text


def test_mask_redacts_authorization_header():
    """The line-79 shape from the real anchor fixture is the canonical case."""
    result = _masked("AUTHORIZATION: basic QUJDOmRlZg==", "http_auth")
    assert "QUJDOmRlZg==" not in result


def test_mask_authorization_header_stops_at_a_quote():
    """`[^\s"']+` deliberately stops at a quote so the closing `"` of a
    shell-quoted header is not swallowed."""
    result = mask('curl -H "Authorization: Bearer ABCDEFGHIJKL" https://x.invalid')
    assert result.endswith(' https://x.invalid')
    assert '"' in result


def test_mask_redacts_env_secret_assignment_preserving_key_name():
    """Knowing WHICH variable was set is high-value debugging evidence and
    leaks nothing about the value."""
    result = _masked(
        "+ export API_KEY=supersecretvalue0000", "env_secret"
    )
    assert "API_KEY=" in result
    assert "supersecretvalue0000" not in result


def test_mask_leaves_pytest_parametrized_id_with_equals_untouched():
    """R2 guard. The `[`-excluding boundary class in rule 8 is what saves this;
    Diagnostic.test_id is the primary correlation key for Section 5 and Slice 3."""
    text = NEGATIVE_CONTROLS["parametrized_test_id"]
    assert mask(text) == text


def test_mask_leaves_path_valued_env_var_untouched():
    """R1 guard. The keyword must be the SUFFIX of the name, immediately
    adjacent to `=`, so SSH_KEY_PATH= does not match."""
    text = NEGATIVE_CONTROLS["path_valued_env_var"]
    assert mask(text) == text


def test_mask_leaves_git_sha_and_uuid_untouched():
    """R5 / the anti-entropy guard. These are content-addressed identifiers,
    i.e. evidence. An entropy heuristic would eat all of them."""
    for key in ("action_pin_sha", "worker_uuid"):
        text = NEGATIVE_CONTROLS[key]
        assert mask(text) == text


def test_mask_leaves_credential_free_db_dsn_untouched():
    """Pins the rule-14 deferral: rule 3 already covers the credentialed form,
    and masking credential-free DSNs destroys evidence for zero security gain."""
    text = NEGATIVE_CONTROLS["credential_free_dsn"]
    assert mask(text) == text


def test_mask_leaves_paths_containing_secret_words_untouched():
    for text in (
        "tests/test_secrets.py::test_token_parsing",
        r"D:\a\_temp\secrets\config.json",
        "~/.ssh/id_rsa",
    ):
        assert mask(text) == text


@pytest.mark.parametrize(
    "cls", ["slack_token", "stripe_key", "npm_token", "pypi_token", "google_api_key"]
)
def test_mask_redacts_provider_tokens(cls):
    literal = EXAMPLE_SECRETS[cls]
    assert literal not in _masked(f"configured with {literal}", cls)


def test_mask_tightened_bearer_no_longer_eats_english_prose():
    """The old `\bBearer\s+\S+` masked the next word of any sentence
    containing "bearer". `{8,}` plus the RFC-6750 charset fixes that."""
    assert mask("the bearer of bad news") == "the bearer of bad news"


# ---------------------------------------------------------------- matrix properties


def test_mask_is_idempotent():
    """No mask token matches any rule, so mask(mask(x)) == mask(x). This is
    what would make a future output-boundary pass safe."""
    for literal in EXAMPLE_SECRETS.values():
        once = mask(f"prefix {literal} suffix")
        assert mask(once) == once


def test_mask_rule_order_prefers_specific_class():
    """Order is part of the contract: `Bearer ghp_...` is a github_token, the
    more specific class, not a bearer_token."""
    result = mask("Authorization: Bearer " + EXAMPLE_SECRETS["github_token"])
    assert MASK_TOKENS["github_token"] in result
    assert MASK_TOKENS["bearer_token"] not in result


def test_mask_counted_reports_per_class_counts():
    token = EXAMPLE_SECRETS["github_token"]
    aws = EXAMPLE_SECRETS["aws_key_id"]
    _, counts = mask_counted(f"{token} and {token} and {aws}")
    assert counts == {"github_token": 2, "aws_key_id": 1}


def test_mask_counted_is_empty_for_clean_text():
    result, counts = mask_counted("Successfully set up CPython (3.14.7)")
    assert counts == {}
    assert result == "Successfully set up CPython (3.14.7)"


def test_mask_tokens_are_stable_constants():
    assert set(MASK_TOKENS) == set(SECRET_CLASSES)
    for cls, token in MASK_TOKENS.items():
        assert re.fullmatch(r"«REDACTED:[a-z0-9_]+»", token), token
        assert cls in token


def test_mask_matrix_ships_thirteen_rules():
    assert len(SECRET_CLASSES) == 13


def test_mask_never_raises_on_random_bytes():
    import random

    rng = random.Random(20260827)
    for _ in range(1000):
        s = "".join(chr(rng.randint(0, 255)) for _ in range(rng.randint(0, 60)))
        assert isinstance(mask(s), str)
