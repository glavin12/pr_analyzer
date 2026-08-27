"""Canonical data model for the deterministic log-parsing engine.

All dataclasses are frozen (immutable evidence records). Enums serialize to
their `.value` via `to_dict`/`to_json`. `to_json` uses `ensure_ascii=True`
because a genuine lone surrogate crashes `json.dumps` under
`ensure_ascii=False` and would mangle the Windows console either way
(`tests/test_parsing_fuzz_security.py` exercises a real one).

Correction (Section 6): this docstring used to claim the anchor fixture
itself contains a lone surrogate. It does not -- line 2283 contains the six
ASCII characters `\\udcff`, Python's *repr* of the surrogate inside a
Windows FileNotFoundError message. The decision is still right; the
evidence originally cited for it was not there.

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

SCHEMA_VERSION = "1.3"


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class TestOutcome(Enum):
    """Shared status taxonomy test-runner parsers (pytest, jest, vitest, ...)

    map their tool-specific labels onto. Nuances that don't fit this
    taxonomy (timeout, setup/teardown phase) live in `Diagnostic.metadata`
    instead of growing this enum.
    """

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    XFAILED = "xfailed"
    XPASSED = "xpassed"


class WorkflowMarker(Enum):
    GROUP = "group"
    ENDGROUP = "endgroup"
    ERROR = "error"
    WARNING = "warning"
    COMMAND = "command"
    SECTION = "section"
    DEBUG = "debug"
    NOTICE = "notice"


class DiagnosticRole(Enum):
    """The role a diagnostic plays *inside its cluster* (Section 5).

    Not a property of the diagnostic itself -- a parser never sets this; it
    is assigned by `clustering.build_clusters` from the correlation rule
    that attached the diagnostic. "Job-level" is not a role: it describes a
    cluster whose only member is a PROCESS_FAILURE because nothing else in
    the log was parseable.
    """

    PRIMARY = "primary"
    SECONDARY = "secondary"
    CONSEQUENCE = "consequence"
    SUMMARY = "summary"
    DUPLICATE = "duplicate"


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


# `slots=True`: LogLine is the only type materialized once per log line, so
# it is the only one where removing the per-instance __dict__ is worth it.
# Measured 27.2 MB -> 19.2 MB at the 200k-line ceiling. Compatible with
# frozen=True and with dataclasses.replace (segmentation.py relies on both)
# and with dataclasses.fields (model._serialize relies on that); LogLine is
# never serialized into FailureReport, so this has no schema impact.
@dataclass(frozen=True, slots=True)
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
    # 1-based inclusive raw line numbers. Byte offsets were deferred to
    # Section 6 and are DECLINED there, deliberately:
    #
    #  * splitlines() discards which of its 9 boundaries was used, and they
    #    encode to 1-3 UTF-8 bytes each, so byte offsets are not derivable
    #    from the current split -- computing them means abandoning
    #    splitlines(), the decision the anchor fixture's line numbers rest on
    #    (see normalizer.py's module docstring).
    #  * There are three coordinate systems here: raw file bytes, timestamp-
    #    stripped payload chars, and masked text chars (mask() changes
    #    length). Every other field in this model lives in the third. A byte
    #    offset would be the only field in the first.
    #  * Upgrade path if a surface ever needs seek-without-load: a separate
    #    on-demand line_byte_offsets(content) side table, computed once,
    #    O(n) ints total -- not 2 ints on every SourceRange, paid by 100% of
    #    reports for a capability used by 0% of them.
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
    # Section 5, additive. Index-aligned with `related`: related_roles[i] is
    # the role of related[i]. `primary` is implicitly DiagnosticRole.PRIMARY
    # and is not listed here. Invariant: len(related_roles) == len(related).
    related_roles: tuple[DiagnosticRole, ...] = ()
    # Section 5, additive. `clustering.dedup_key(primary)` -- the normalized
    # identity these diagnostics were grouped under. Stable across runs, so
    # the golden snapshot doubles as a readable spec of the dedup rules.
    key: str | None = None


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
