"""Frozen public API for the deterministic log-parsing engine (Section 1).

Later sections add providers/parsers/pipeline stages behind this same
surface -- `parse_log`, `to_json`, `FailureReport`, `DiagnosticType`,
`ParseLimits`, `SCHEMA_VERSION` -- without a core rewrite.
"""

from .limits import ParseLimits
from .model import SCHEMA_VERSION, DiagnosticRole, DiagnosticType, FailureReport, to_dict, to_json
from .pipeline import parse_log

__all__ = [
    "parse_log",
    "to_json",
    "to_dict",
    "FailureReport",
    "DiagnosticType",
    "DiagnosticRole",
    "ParseLimits",
    "SCHEMA_VERSION",
]
