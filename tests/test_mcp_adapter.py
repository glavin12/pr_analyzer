"""Tests for `agentic_pr_analyzer.mcp.adapter.build_summary` -- the pure
FailureReport -> tiered summary dict translation (Wave-1, frozen). Exercised
against the real committed anchor fixture plus two SYNTHETIC fixtures.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from secret_examples import EXAMPLE_SECRETS  # noqa: E402

from agentic_pr_analyzer.mcp.adapter import _confidence_bucket, build_summary
from agentic_pr_analyzer.parsing import parse_log
from agentic_pr_analyzer.parsing.confidence import (
    BARE_ERROR_MARKER,
    EXACT_TOOL_FORMAT,
    GENERIC_FILE_LINE_ERROR,
    KNOWN_SUMMARY,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "raw_logs"
ANCHOR = FIXTURES_DIR / "pallets" / "click" / "32472305359_96741461054.log"
MULTI = FIXTURES_DIR / "SYNTHETIC" / "multi-test-sample" / "sample.log"
CASCADE = FIXTURES_DIR / "SYNTHETIC" / "cascade-sample" / "sample.log"

BASE_KEYS = {"schemaVersion", "reportId", "status", "source", "summary", "clusters"}

# One diagnostic, one cluster -> `1 > 1` is False on both counts, so
# `omitted` must be absent. Needs GitHub Actions-style timestamps: a bare
# `##[error]` line without them is detected as the generic provider, which
# does not recognize workflow-command markers.
CLEAN_LOG = (
    "2026-08-27T09:10:00.0000000Z ##[group]Run deploy\n"
    "2026-08-27T09:10:00.1000000Z deploying\n"
    "2026-08-27T09:10:00.2000000Z ##[endgroup]\n"
    "2026-08-27T09:10:01.0000000Z ##[error]Process completed with exit code 1.\n"
)


def _load(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="surrogatepass")


def _summarize(path: Path, **kwargs):
    content = _load(path)
    report = parse_log(content, source=None)
    return report, content, build_summary(report, content, path, **kwargs)


def test_shape_and_keys_anchor():
    report, content, summary = _summarize(ANCHOR)
    assert summary.keys() == BASE_KEYS | {"omitted"}
    assert summary["schemaVersion"] == "1.0"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", summary["reportId"])
    assert summary["status"] == "failures_found"
    assert summary["summary"] == {
        "totalDiagnostics": 2,
        "totalClusters": 1,
        "clustersShown": 1,
        "jobsFailed": [],
        "stepsFailed": [],
    }


def test_anchor_primary_cluster_fields():
    _, _, summary = _summarize(ANCHOR)
    cluster = summary["clusters"][0]
    assert cluster["clusterId"] == "c1"
    primary = cluster["primaryDiagnostic"]
    assert primary["tool"] == "pytest"
    assert primary["testName"] == "tests/test_types.py::test_file_surrogates[type1]"
    assert primary["confidence"] == "high"


def test_omitted_present_on_anchor():
    _, _, summary = _summarize(ANCHOR)
    omitted = summary["omitted"]
    assert omitted["diagnostics"] == 1
    assert omitted["reason"] == "collapsed_into_clusters"


@pytest.mark.parametrize("path", [MULTI, CASCADE])
def test_shape_and_omitted_present_on_synthetic_fixtures(path):
    report, content, summary = _summarize(path)
    assert BASE_KEYS <= summary.keys()
    assert summary["status"] == "failures_found"
    total_diagnostics = summary["summary"]["totalDiagnostics"]
    total_clusters = summary["summary"]["totalClusters"]
    clusters_shown = summary["summary"]["clustersShown"]
    assert total_diagnostics == len(report.diagnostics)
    assert total_clusters == len(report.clusters)
    if total_diagnostics > clusters_shown or total_clusters > clusters_shown:
        assert "omitted" in summary
    else:
        assert "omitted" not in summary


def test_omitted_absent_when_nothing_collapsed():
    report = parse_log(CLEAN_LOG, source=None)
    summary = build_summary(report, CLEAN_LOG, "clean.log")
    assert len(report.diagnostics) == 1
    assert len(report.clusters) == 1
    assert summary.keys() == BASE_KEYS
    assert "omitted" not in summary


@pytest.mark.parametrize("path", [ANCHOR, MULTI, CASCADE])
def test_deterministic_report_id_and_dict(path):
    content = _load(path)
    report = parse_log(content, source=None)
    first = build_summary(report, content, path)
    second = build_summary(report, content, path)
    assert first == second

    # A fully independent re-parse of the same bytes must land on the same id.
    report_again = parse_log(content, source=None)
    third = build_summary(report_again, content, path)
    assert third["reportId"] == first["reportId"]


def test_different_content_gives_different_report_id():
    content_a = CLEAN_LOG
    content_b = CLEAN_LOG + "an extra line\n"
    id_a = build_summary(parse_log(content_a, source=None), content_a, "a.log")["reportId"]
    id_b = build_summary(parse_log(content_b, source=None), content_b, "b.log")["reportId"]
    assert id_a != id_b


def test_confidence_bucket_boundaries():
    assert _confidence_bucket(EXACT_TOOL_FORMAT) == "high"
    assert _confidence_bucket(KNOWN_SUMMARY) == "high"
    assert _confidence_bucket(KNOWN_SUMMARY - 0.01) == "medium"
    assert _confidence_bucket(GENERIC_FILE_LINE_ERROR) == "medium"
    assert _confidence_bucket(GENERIC_FILE_LINE_ERROR - 0.01) == "low"
    assert _confidence_bucket(BARE_ERROR_MARKER) == "low"
    assert _confidence_bucket(0.0) == "low"


@pytest.mark.parametrize("path", [ANCHOR, MULTI, CASCADE])
def test_cluster_order_matches_parser_no_reranking(path):
    report, content, summary = _summarize(path)
    assert [c["clusterId"] for c in summary["clusters"]] == [
        f"c{i + 1}" for i in range(len(summary["clusters"]))
    ]
    assert report.clusters[0] is report.primary_cluster
    for i, cluster in enumerate(summary["clusters"]):
        primary = report.clusters[i].primary
        pd = cluster["primaryDiagnostic"]
        assert pd["tool"] == primary.tool
        assert pd["message"] == primary.message
        assert pd["location"]["file"] == primary.file


@pytest.mark.parametrize("path", [ANCHOR, CASCADE])
def test_excerpt_is_bounded_to_five_lines(path):
    # Both fixtures have a primary cluster whose evidence exceeds 5 lines
    # (anchor: 25, cascade c1: 9), so this actually exercises the cap.
    _, _, summary = _summarize(path)
    for cluster in summary["clusters"]:
        excerpt = cluster["primaryDiagnostic"]["evidence"]["excerpt"]
        assert isinstance(excerpt, str)
        assert len(excerpt.split("\n")) <= 5


def test_excerpt_and_message_are_masked_not_raw_secret():
    secret = EXAMPLE_SECRETS["github_token"]
    content = (
        "2026-08-27T09:10:00.0000000Z ##[group]Run deploy\n"
        f"2026-08-27T09:10:00.1000000Z deploying with token={secret}\n"
        "2026-08-27T09:10:00.2000000Z ##[endgroup]\n"
        f"2026-08-27T09:10:01.0000000Z ##[error]Process completed with exit code 1 token={secret}\n"
    )
    report = parse_log(content, source=None)
    # Sanity: the secret really lands inside the diagnostic's own evidence,
    # so this test is actually exercising masking rather than trivially
    # passing because nothing relevant was captured.
    assert len(report.diagnostics) == 1

    summary = build_summary(report, content, "deploy.log")
    primary = summary["clusters"][0]["primaryDiagnostic"]
    assert secret not in primary["message"]
    assert secret not in primary["evidence"]["excerpt"]
    assert "«REDACTED:github_token»" in primary["evidence"]["excerpt"]
    assert secret not in json.dumps(summary)


def test_max_clusters_shown_caps_and_marks_omitted():
    content = _load(MULTI)
    report = parse_log(content, source=None)
    summary = build_summary(report, content, MULTI, max_clusters_shown=1)
    assert summary["summary"]["clustersShown"] == 1
    assert len(summary["clusters"]) == 1
    assert summary["clusters"][0]["clusterId"] == "c1"
    assert "omitted" in summary
    assert summary["omitted"]["diagnostics"] == summary["summary"]["totalDiagnostics"] - 1
