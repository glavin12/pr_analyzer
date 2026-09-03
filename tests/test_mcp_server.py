"""Tests for the MCP server (`agentic_pr_analyzer.mcp.server`), built
concurrently by agent B1. Guarded with `importorskip` so this whole module
skips cleanly -- rather than erroring collection -- if `server.py` doesn't
exist yet when this file runs.

Contract assumed (per the Wave-2 B1 brief, not yet verified against real
code):
- `analyze_ci_log_impl(path: str) -> dict`: resolve_allowed -> read
  server-side -> parse_log -> build_summary -> final mask. Returns the
  tiered summary, or `{"schemaVersion":"1.0","status":"error",
  "error":{"kind":..., "message":...}}` for path_not_allowed /
  file_not_found / decode_error.
- `get_cluster_detail_impl(report_id, cluster_id) -> dict` and
  `get_full_report_impl(report_id) -> dict`: cache-backed drill-down;
  unknown id -> error dict.
- `analyze_ci_log(path: str)`: the `@server.tool`-decorated wrapper,
  path-only signature.
- `main()`: stdio entry (not exercised here -- it blocks on stdio).
"""

import inspect
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from secret_examples import EXAMPLE_SECRETS  # noqa: E402

server = pytest.importorskip("agentic_pr_analyzer.mcp.server")

ANCHOR = (
    Path(__file__).parent
    / "fixtures"
    / "raw_logs"
    / "pallets"
    / "click"
    / "32472305359_96741461054.log"
)


@pytest.fixture
def allowed_tmp(tmp_path, monkeypatch):
    """tmp_path as the ONLY allowed root, regardless of where the OS/pytest
    puts its system temp dir -- avoids relying on tmp_path happening to sit
    under paths.allowed_roots()'s default.
    """
    monkeypatch.setenv("CI_LOG_PARSER_ALLOWED_ROOTS", str(tmp_path))
    return tmp_path


def _copy_anchor(dest_dir: Path) -> Path:
    dest = dest_dir / "job.log"
    shutil.copyfile(ANCHOR, dest)
    return dest


def test_path_outside_allowed_roots_is_rejected(allowed_tmp):
    # allowed_tmp's env override makes tmp_path the ONLY allowed root, so
    # the real (outside-tmp_path) anchor fixture path must be blocked.
    result = server.analyze_ci_log_impl(str(ANCHOR))
    assert result["schemaVersion"] == "1.0"
    assert result["status"] == "error"
    assert result["error"]["kind"] == "path_not_allowed"


def test_missing_file_under_allowed_root_is_reported(allowed_tmp):
    missing = allowed_tmp / "does-not-exist.log"
    result = server.analyze_ci_log_impl(str(missing))
    assert result["schemaVersion"] == "1.0"
    assert result["status"] == "error"
    assert result["error"]["kind"] == "file_not_found"


def test_analyze_ci_log_tool_schema_is_path_only():
    sig = inspect.signature(server.analyze_ci_log)
    assert set(sig.parameters) == {"path"}
    annotation = sig.parameters["path"].annotation
    # Accept either the real `str` type or the PEP-563 stringified form
    # ("str"), depending on whether the module uses `from __future__ import
    # annotations`.
    assert annotation is str or annotation == "str"


def test_idempotent_same_file_twice_gives_identical_result(allowed_tmp):
    log_path = _copy_anchor(allowed_tmp)
    first = server.analyze_ci_log_impl(str(log_path))
    second = server.analyze_ci_log_impl(str(log_path))
    assert first["reportId"] == second["reportId"]
    assert first == second


def test_drill_down_round_trip(allowed_tmp):
    log_path = _copy_anchor(allowed_tmp)
    summary = server.analyze_ci_log_impl(str(log_path))
    assert summary["status"] == "failures_found"
    report_id = summary["reportId"]

    detail = server.get_cluster_detail_impl(report_id, "c1")
    assert isinstance(detail, dict)
    assert "cluster" in detail

    full = server.get_full_report_impl(report_id)
    assert isinstance(full, dict)
    assert "clusters" in full


def test_drill_down_unknown_report_id_is_error_dict(allowed_tmp):
    # A well-formed but never-issued report id.
    bogus_report_id = "sha256:" + "0" * 64

    full = server.get_full_report_impl(bogus_report_id)
    assert full.get("status") == "error"
    assert "kind" in full.get("error", {})

    detail = server.get_cluster_detail_impl(bogus_report_id, "c1")
    assert detail.get("status") == "error"
    assert "kind" in detail.get("error", {})


def test_drill_down_unknown_cluster_id_is_error_dict(allowed_tmp):
    log_path = _copy_anchor(allowed_tmp)
    summary = server.analyze_ci_log_impl(str(log_path))
    report_id = summary["reportId"]

    detail = server.get_cluster_detail_impl(report_id, "c999")
    assert detail.get("status") == "error"
    assert "kind" in detail.get("error", {})


def test_zero_egress_during_analyze(allowed_tmp, monkeypatch):
    log_path = _copy_anchor(allowed_tmp)

    def boom(*args, **kwargs):
        raise RuntimeError("socket() called -- the parse path must not touch the network")

    monkeypatch.setattr("socket.socket", boom)

    result = server.analyze_ci_log_impl(str(log_path))
    assert result["schemaVersion"] == "1.0"
    assert result["status"] == "failures_found"
    assert "clusters" in result


def test_final_mask_defense_in_depth(allowed_tmp):
    # Reuses a planted literal from tests/secret_examples.py (shared with the
    # sanitize/fixture-policy tests) rather than typing a new credential-
    # shaped string. Needs GitHub Actions-style timestamps so the line is
    # recognized as a workflow-command marker and its exit-code line becomes
    # the process-failure diagnostic's own evidence -- see
    # test_mcp_adapter.py's identical recipe for why.
    secret = EXAMPLE_SECRETS["github_token"]
    content = (
        "2026-08-27T09:10:00.0000000Z ##[group]Run deploy\n"
        f"2026-08-27T09:10:00.1000000Z deploying with token={secret}\n"
        "2026-08-27T09:10:00.2000000Z ##[endgroup]\n"
        f"2026-08-27T09:10:01.0000000Z ##[error]Process completed with exit code 1 token={secret}\n"
    )
    log_path = allowed_tmp / "deploy.log"
    log_path.write_text(content, encoding="utf-8")

    result = server.analyze_ci_log_impl(str(log_path))
    assert secret not in json.dumps(result)
