import json as json_module
from unittest.mock import Mock

import pytest
import requests


def make_response(
    status_code: int,
    json_data: dict | None = None,
    text: str = "",
    headers: dict | None = None,
    content: bytes | None = None,
) -> Mock:
    """Builds a fake requests.Response for mocking GitHubClient's session.request calls.

    Pass `content=` to exercise the real `resp.content.decode(...)` path (e.g.
    get_job_log), which is byte-oriented rather than reading `.text`.
    """
    response = Mock(spec=requests.Response)
    response.status_code = status_code
    response.headers = headers or {}
    response.text = text or (json_module.dumps(json_data) if json_data is not None else "")
    response.content = content if content is not None else b""
    response.json.return_value = json_data if json_data is not None else {}
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
    else:
        response.raise_for_status.return_value = None
    return response


@pytest.fixture
def mock_response():
    return make_response
