"""Basic secret masking applied to normalized line text before it leaves the

engine. High-risk patterns only (GitHub tokens, URL-embedded credentials,
bearer tokens) -- the full provider matrix is Section 7. The byte-faithful
.log fixture on disk is never rewritten; this only touches in-memory
LogLine.text/raw_text used for the FailureReport output.
"""

import re

_GITHUB_TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_URL_CREDS_RE = re.compile(r"://[^\s:@/]+:[^\s@/]+@")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+\S+")

_GITHUB_TOKEN_MASK = "«REDACTED:github_token»"
_URL_CREDS_MASK = "://«REDACTED:url_creds»@"
_BEARER_MASK = "Bearer «REDACTED:bearer_token»"


def mask(text: str) -> str:
    text = _GITHUB_TOKEN_RE.sub(_GITHUB_TOKEN_MASK, text)
    text = _URL_CREDS_RE.sub(_URL_CREDS_MASK, text)
    text = _BEARER_RE.sub(_BEARER_MASK, text)
    return text
