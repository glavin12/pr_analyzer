"""MCP stdio server exposing the deterministic log-parsing engine as tools.

Plain `_impl` functions hold the logic (testable without stdio); `@server.tool`
wrappers just delegate. Imports only from `..parsing` and `.adapter`/`.paths`
plus the `mcp` SDK -- never `..github`, so this process has zero network
egress capability by construction.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..parsing import parse_log, to_dict
from ..parsing.model import _serialize
from ..parsing.sanitize import mask
from .adapter import build_summary
from .paths import PathNotAllowedError, resolve_allowed

server = MCPServer(
    name="ci-log-parser",
    version="0.1.0",
    instructions=(
        "Parses CI/CD job logs deterministically (no LLM) into a tiered, "
        "evidence-backed failure summary. Tools take a filesystem PATH to a "
        "log file the server reads itself -- never pass inline log text."
    ),
)

# ponytail: plain dict bounded by eviction-on-insert, no LRU library. Parsing
# is deterministic and content-hash-keyed, so re-parsing on a cache miss is
# always correct, just slower. Upgrade to functools.lru_cache-style recency
# if drill-down calls start missing in practice.
_MAX_CACHE = 32
_report_cache: dict[str, object] = {}


def _cache_put(report_id: str, report) -> None:
    if report_id not in _report_cache and len(_report_cache) >= _MAX_CACHE:
        _report_cache.pop(next(iter(_report_cache)))
    _report_cache[report_id] = report


def _error(kind: str, message: str) -> dict:
    return {"schemaVersion": "1.0", "status": "error", "error": {"kind": kind, "message": message}}


def _mask_response(obj):
    if isinstance(obj, dict):
        return {k: _mask_response(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_response(v) for v in obj]
    if isinstance(obj, str):
        return mask(obj)
    return obj


def analyze_ci_log_impl(path: str) -> dict:
    try:
        resolved = resolve_allowed(path)
    except PathNotAllowedError as e:
        return _error("path_not_allowed", str(e))
    try:
        content = resolved.read_text(encoding="utf-8", errors="surrogatepass")
    except FileNotFoundError:
        return _error("file_not_found", f"no such file: {resolved}")
    except OSError as e:
        return _error("decode_error", f"could not read {resolved}: {e}")

    report = parse_log(content, source=None)
    summary = build_summary(report, content, str(resolved))
    _cache_put(summary["reportId"], report)
    return _mask_response(summary)


def get_full_report_impl(report_id: str) -> dict:
    report = _report_cache.get(report_id)
    if report is None:
        return _error("unknown_report", f"no cached report for {report_id}")
    return _mask_response(to_dict(report))


def get_cluster_detail_impl(report_id: str, cluster_id: str) -> dict:
    report = _report_cache.get(report_id)
    if report is None:
        return _error("unknown_report", f"no cached report for {report_id}")
    if not cluster_id.startswith("c") or not cluster_id[1:].isdigit():
        return _error("unknown_cluster", f"malformed clusterId: {cluster_id}")
    index = int(cluster_id[1:]) - 1
    if index < 0 or index >= len(report.clusters):
        return _error("unknown_cluster", f"no such cluster: {cluster_id}")
    cluster = report.clusters[index]
    return _mask_response(
        {
            "schemaVersion": "1.0",
            "reportId": report_id,
            "clusterId": cluster_id,
            "cluster": _serialize(cluster),
        }
    )


@server.tool(
    description=(
        "Parse a CI/CD log FILE at `path` and return a tiered, evidence-backed "
        "failure summary. Pass only a filesystem path -- never paste log text."
    )
)
def analyze_ci_log(path: str) -> dict:
    return analyze_ci_log_impl(path)


@server.tool(
    description=(
        "Fetch full evidence (all related diagnostics + stack frames) for one "
        "cluster (e.g. 'c1') from a report previously returned by analyze_ci_log."
    )
)
def get_cluster_detail(report_id: str, cluster_id: str) -> dict:
    return get_cluster_detail_impl(report_id, cluster_id)


@server.tool(
    description=(
        "Fetch the complete FailureReport (every diagnostic, section and "
        "cluster) for a report previously returned by analyze_ci_log."
    )
)
def get_full_report(report_id: str) -> dict:
    return get_full_report_impl(report_id)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
