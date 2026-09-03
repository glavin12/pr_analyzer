"""Pure adapter: FailureReport -> tiered, evidence-backed summary dict for
coding agents (MCP integration layer, target contract 4.3).

Thin translation only: no LLM/AI/embeddings, no diagnosis/root-cause/fix
(evidence only), the parser core is never modified, and this module is pure
(no file I/O, no network, no MCP/protocol knowledge -- it receives the
already-read `content` string and the already-built `FailureReport`).

Field-by-field mapping (reconciled against the real parser):

- schemaVersion = "1.0" -- the ADAPTER's own version, NOT report.schema_version.
- reportId = "sha256:" + hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest().
- status: "parser_error" if report.stats.get("fatal"); else "no_failures_found"
  if len(report.diagnostics) == 0; else "failures_found".
- source.path = str(path); source.bytes = len(content.encode("utf-8", "surrogatepass"));
  source.truncated = report.truncated. (report.source is a DIFFERENT thing --
  git metadata LogSource -- do not confuse with adapter `source`, which is
  file provenance.)
- summary.totalDiagnostics = len(report.diagnostics); totalClusters =
  len(report.clusters); clustersShown = min(len(report.clusters), max_clusters_shown).
- summary.jobsFailed = [report.source.job_name] if report.source is not None else [].
- summary.stepsFailed: distinct section titles of the shown clusters (for each
  shown cluster, the report.sections entry whose .id == cluster.section_id,
  take its .title if not None), de-duped, order preserved. [] if none.
- clusters: iterate report.clusters[:clustersShown] in the parser's EXISTING
  order -- never re-ranked (report.clusters[0] is report.primary_cluster).
  clusterId = f"c{i+1}" for 0-based index i. primaryDiagnostic maps
  cluster.primary:
    - tool = primary.tool; severity = primary.severity.value;
      kind = primary.type.value; testName = primary.test_id;
      message = primary.message.
    - location = {"file": primary.file, "line": primary.line, "column": primary.column}.
    - evidence.lines = list(primary.evidence); evidence.excerpt = masked text
      of the first min(5, limits.max_context_lines) of those lines, joined by
      "\n", built by re-running the parser's own normalize().
    - confidence: bucket primary.confidence via the confidence.py constants
      (>= KNOWN_SUMMARY -> "high"; >= GENERIC_FILE_LINE_ERROR -> "medium";
      else "low").
    - relatedDiagnosticsCount = len(cluster.related).
    - occurrences = 1 + count of DiagnosticRole.DUPLICATE in cluster.related_roles.
- omitted: included ONLY when something was collapsed/hidden
  (totalDiagnostics > clustersShown or totalClusters > clustersShown); the
  key is omitted entirely otherwise (never set to null).
"""

from __future__ import annotations

import hashlib

from ..parsing.confidence import GENERIC_FILE_LINE_ERROR, KNOWN_SUMMARY
from ..parsing.limits import ParseLimits
from ..parsing.model import DiagnosticRole, FailureReport
from ..parsing.normalizer import normalize
from ..parsing.providers import detect_provider

ADAPTER_SCHEMA_VERSION = "1.0"

# Matches pipeline.py's _PROVIDER_SAMPLE_CHARS. Not imported directly -- that
# name is private to pipeline.py; duplicating one int is cheaper than reaching
# across the module boundary for it.
_PROVIDER_SAMPLE_CHARS = 4000


def _confidence_bucket(score: float) -> str:
    if score >= KNOWN_SUMMARY:
        return "high"
    if score >= GENERIC_FILE_LINE_ERROR:
        return "medium"
    return "low"


def _status(report: FailureReport) -> str:
    if report.stats.get("fatal"):
        return "parser_error"
    if len(report.diagnostics) == 0:
        return "no_failures_found"
    return "failures_found"


def _steps_failed(report: FailureReport, shown_clusters) -> list[str]:
    sections_by_id = {s.id: s for s in report.sections}
    titles: list[str] = []
    for cluster in shown_clusters:
        section = sections_by_id.get(cluster.section_id)
        if section is not None and section.title is not None and section.title not in titles:
            titles.append(section.title)
    return titles


def _build_excerpt(content: str, limits: ParseLimits, evidence_lines: tuple[int, ...]) -> str:
    # ponytail: re-runs normalize() once per build_summary call rather than
    # threading masked lines through parse_log's return. Cheap (one pass over
    # `content`) next to the full parse it re-derives from; fold into
    # parse_log's return if this shows up in a profile.
    provider = detect_provider(content[:_PROVIDER_SAMPLE_CHARS])
    lines, _ = normalize(content, provider, limits)
    line_text = {ln.raw_lineno: ln.text for ln in lines}
    cap = min(5, limits.max_context_lines)
    return "\n".join(line_text.get(n, "") for n in evidence_lines[:cap])


def _diagnostic_dict(primary, content: str, limits: ParseLimits) -> dict:
    return {
        "tool": primary.tool,
        "severity": primary.severity.value,
        "kind": primary.type.value,
        "testName": primary.test_id,
        "message": primary.message,
        "location": {"file": primary.file, "line": primary.line, "column": primary.column},
        "evidence": {
            "lines": list(primary.evidence),
            "excerpt": _build_excerpt(content, limits, primary.evidence),
        },
        "confidence": _confidence_bucket(primary.confidence),
    }


def build_summary(
    report: FailureReport,
    content: str,
    path,
    limits: ParseLimits = ParseLimits(),
    max_clusters_shown: int = 10,
) -> dict:
    content_bytes = len(content.encode("utf-8", "surrogatepass"))
    clusters_shown_list = list(report.clusters[:max_clusters_shown])
    clusters_shown = min(len(report.clusters), max_clusters_shown)

    clusters = []
    for i, cluster in enumerate(clusters_shown_list):
        occurrences = 1 + sum(1 for r in cluster.related_roles if r == DiagnosticRole.DUPLICATE)
        clusters.append(
            {
                "clusterId": f"c{i + 1}",
                "primaryDiagnostic": _diagnostic_dict(cluster.primary, content, limits),
                "relatedDiagnosticsCount": len(cluster.related),
                "occurrences": occurrences,
            }
        )

    summary_dict = {
        "schemaVersion": ADAPTER_SCHEMA_VERSION,
        "reportId": "sha256:" + hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest(),
        "status": _status(report),
        "source": {"path": str(path), "bytes": content_bytes, "truncated": report.truncated},
        "summary": {
            "totalDiagnostics": len(report.diagnostics),
            "totalClusters": len(report.clusters),
            "clustersShown": clusters_shown,
            "jobsFailed": [report.source.job_name] if report.source is not None else [],
            "stepsFailed": _steps_failed(report, clusters_shown_list),
        },
        "clusters": clusters,
    }

    total_diagnostics = len(report.diagnostics)
    total_clusters = len(report.clusters)
    if total_diagnostics > clusters_shown or total_clusters > clusters_shown:
        omitted_diagnostics = total_diagnostics - clusters_shown
        summary_dict["omitted"] = {
            "diagnostics": omitted_diagnostics,
            "reason": "collapsed_into_clusters",
            "note": (
                f"{total_diagnostics} diagnostics collapsed into {total_clusters} clusters. "
                "Call get_cluster_detail(reportId, clusterId) for full evidence, or "
                "get_full_report(reportId) for everything."
            ),
        }

    return summary_dict


if __name__ == "__main__":
    from ..parsing.model import (
        Diagnostic,
        DiagnosticType,
        FailureCluster,
        Severity,
    )

    diag = Diagnostic(
        type=DiagnosticType.TEST_FAILURE,
        severity=Severity.ERROR,
        tool="pytest",
        message="AssertionError: boom",
        file="tests/test_x.py",
        line=1,
        column=None,
        source_range=None,
        stack_trace=None,
        test_id="test_x",
        exit_code=None,
        confidence=KNOWN_SUMMARY,
        evidence=(1,),
        metadata={},
        parser="pytest",
    )
    cluster = FailureCluster(primary=diag, related=(), section_id=None, classification=diag.type)
    report = FailureReport(
        schema_version="1.3",
        source=None,
        provider="generic",
        sections=(),
        diagnostics=(diag,),
        clusters=(cluster,),
        primary_cluster=cluster,
        exit_code=None,
        raw_line_count=1,
        truncated=False,
        stats={"fatal": False},
    )
    result = build_summary(report, "line one\n", "smoke.log")
    assert set(result.keys()) == {"schemaVersion", "reportId", "status", "source", "summary", "clusters"}
    assert result["schemaVersion"] == "1.0"
    assert result["reportId"].startswith("sha256:")
    assert result["clusters"][0]["clusterId"] == "c1"
    print("adapter smoke check OK")
