"""Canonical data model for the deterministic log-parsing engine.

All dataclasses are frozen (immutable evidence records). Enums serialize to
their `.value` via `to_dict`/`to_json`. `to_json` uses `ensure_ascii=True`:
the anchor fixture contains a lone Unicode surrogate (`\\udcff`, from a
Windows FileNotFoundError message) that crashes `json.dumps` under
`ensure_ascii=False` and would mangle the Windows console either way.

"primary diagnostic" / "failure origin candidate", never "root cause" -- a
deterministic parser cannot justify causal claims.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..github.models import RawLog

SCHEMA_VERSION = "1.0"


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class WorkflowMarker(Enum):
    GROUP = "group"
    ENDGROUP = "endgroup"
    ERROR = "error"
    WARNING = "warning"
    COMMAND = "command"
    SECTION = "section"
    DEBUG = "debug"
    NOTICE = "notice"


class DiagnosticType(Enum):
    PROCESS_FAILURE = "process_failure"
    UNKNOWN = "unknown"
    # Defined now for schema stability; only emitted once their parser +
    # fixture exist (Sections 2-4).
    TEST_FAILURE = "test_failure"
    EXCEPTION = "exception"
    COMPILER_ERROR = "compiler_error"
    LINT_ERROR = "lint_error"
    DEPENDENCY_ERROR = "dependency_error"


@dataclass(frozen=True)
class LogLine:
    raw_lineno: int
    raw_text: str
    text: str
    timestamp: str | None
    marker: WorkflowMarker | None
    marker_body: str | None
    section_id: int | None


@dataclass(frozen=True)
class LogSection:
    id: int
    title: str | None
    kind: str
    start_lineno: int
    end_lineno: int
    parent_id: int | None


@dataclass(frozen=True)
class SourceRange:
    # 1-based inclusive raw line numbers. Byte offsets are deferred to
    # Section 6 (streaming) and will be documented there.
    start: int
    end: int


@dataclass(frozen=True)
class StackFrame:
    file_path: str | None
    line_number: int | None
    column: int | None
    function: str | None
    raw_lineno: int
    in_project: bool
    raw_text: str


@dataclass(frozen=True)
class StackTrace:
    exception_type: str | None
    message: str | None
    frames: tuple[StackFrame, ...]


@dataclass(frozen=True)
class Diagnostic:
    type: DiagnosticType
    severity: Severity
    tool: str | None
    message: str | None
    file: str | None
    line: int | None
    column: int | None
    source_range: SourceRange | None
    stack_trace: StackTrace | None
    test_id: str | None
    exit_code: int | None
    confidence: float
    evidence: tuple[int, ...]
    metadata: dict
    parser: str


@dataclass(frozen=True)
class FailureCluster:
    primary: Diagnostic
    related: tuple[Diagnostic, ...]
    section_id: int | None
    classification: DiagnosticType


@dataclass(frozen=True)
class LogSource:
    owner: str
    repo: str
    run_id: int
    run_attempt: int
    job_id: int
    job_name: str
    workflow_name: str
    conclusion: str
    head_sha: str
    html_url: str
    fetched_at: str

    @classmethod
    def from_raw_log(cls, raw: "RawLog") -> "LogSource":
        meta = raw.metadata_dict()
        return cls(
            owner=meta["owner"],
            repo=meta["repo"],
            run_id=meta["run_id"],
            run_attempt=meta["run_attempt"],
            job_id=meta["job_id"],
            job_name=meta["job_name"],
            workflow_name=meta["workflow_name"],
            conclusion=meta["conclusion"],
            head_sha=meta["head_sha"],
            html_url=meta["html_url"],
            fetched_at=meta["fetched_at"],
        )


@dataclass(frozen=True)
class FailureReport:
    schema_version: str
    source: LogSource | None
    provider: str
    sections: tuple[LogSection, ...]
    diagnostics: tuple[Diagnostic, ...]
    clusters: tuple[FailureCluster, ...]
    primary_cluster: FailureCluster | None
    exit_code: int | None
    raw_line_count: int
    truncated: bool
    stats: dict = field(default_factory=dict)


def _serialize(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _serialize(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (tuple, list)):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value


def to_dict(report: FailureReport) -> dict:
    return _serialize(report)


def to_json(report: FailureReport) -> str:
    return json.dumps(to_dict(report), ensure_ascii=True, indent=2)
