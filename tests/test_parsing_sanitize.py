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
