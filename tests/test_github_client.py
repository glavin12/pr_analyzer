from unittest.mock import Mock

import pytest
import requests

from agentic_pr_analyzer.exceptions import (
    GitHubAuthError,
    GitHubForbiddenError,
    GitHubNotFoundError,
)
from agentic_pr_analyzer.github.client import GitHubClient


def _client_with_mock_session(mock_response, response):
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.request.return_value = response
    client = GitHubClient(token="fake-token", session=session)
    return client, session


def test_verify_access_success(mock_response):
    response = mock_response(200, {"resources": {"core": {"remaining": 4999, "limit": 5000}}})
    client, _ = _client_with_mock_session(mock_response, response)
    data = client.verify_access()
    assert data["resources"]["core"]["remaining"] == 4999


def test_auth_header_is_set_on_the_session():
    session = Mock(spec=requests.Session)
    session.headers = {}
    GitHubClient(token="my-secret-token", session=session)
    assert session.headers["Authorization"] == "Bearer my-secret-token"


def test_401_raises_auth_error(mock_response):
    response = mock_response(401, {"message": "Bad credentials"})
    client, _ = _client_with_mock_session(mock_response, response)
    with pytest.raises(GitHubAuthError):
        client.verify_access()


def test_403_raises_forbidden_error_with_headers(mock_response):
    response = mock_response(
        403,
        {"message": "API rate limit exceeded"},
        headers={"Retry-After": "60", "X-RateLimit-Reset": "1700000000"},
    )
    client, _ = _client_with_mock_session(mock_response, response)
    with pytest.raises(GitHubForbiddenError) as exc_info:
        client.verify_access()
    assert exc_info.value.retry_after == "60"
    assert exc_info.value.rate_limit_reset == "1700000000"


def test_404_raises_not_found_error(mock_response):
    response = mock_response(404, {"message": "Not Found"})
    client, _ = _client_with_mock_session(mock_response, response)
    with pytest.raises(GitHubNotFoundError):
        client.list_workflow_runs("owner", "repo")


def test_list_workflow_runs_passes_status_filter(mock_response):
    response = mock_response(200, {"workflow_runs": [{"id": 1}]})
    client, session = _client_with_mock_session(mock_response, response)
    runs = client.list_workflow_runs("owner", "repo", status="failure", per_page=5)
    assert runs == [{"id": 1}]
    _, kwargs = session.request.call_args
    assert kwargs["params"] == {"status": "failure", "per_page": 5}


def test_get_job_log_decodes_raw_bytes_as_utf8(mock_response):
    # The real endpoint returns a charset-less blob; get_job_log must decode
    # resp.content as UTF-8 itself, not read resp.text (which requests would
    # mis-decode as Latin-1). Passing raw bytes here exercises that path.
    response = mock_response(200, content=b"##[error] pytest failed\n1 test failed")
    client, _ = _client_with_mock_session(mock_response, response)
    log_text = client.get_job_log("owner", "repo", 123)
    assert "pytest failed" in log_text


def test_get_job_log_preserves_bom_accents_and_crlf_byte_for_byte(mock_response):
    # GitHub Actions logs begin with a UTF-8 BOM and use \r\n line endings, and
    # may contain accented characters. Decoding the raw UTF-8 bytes must round-trip
    # them exactly — the old `.text` (Latin-1) path mojibaked all of these.
    original = "﻿Café build\r\n"
    response = mock_response(200, content=original.encode("utf-8"))
    client, _ = _client_with_mock_session(mock_response, response)
    log_text = client.get_job_log("owner", "repo", 123)
    assert log_text == original


def test_every_request_passes_an_explicit_timeout(mock_response):
    response = mock_response(200, {"resources": {"core": {"remaining": 1}}})
    client, session = _client_with_mock_session(mock_response, response)
    client.verify_access()
    _, kwargs = session.request.call_args
    assert kwargs["timeout"] == 10.0
