"""Offline tests for the MCP path allow-listing gate."""

from pathlib import Path

import pytest

from agentic_pr_analyzer.mcp.paths import PathNotAllowedError, allowed_roots, resolve_allowed


def test_inside_root_is_accepted(tmp_path):
    target = tmp_path / "job.log"
    result = resolve_allowed(target, roots=[tmp_path])
    assert result == target.resolve()


def test_traversal_escape_is_rejected(tmp_path):
    escape = tmp_path / ".." / ".." / "etc" / "passwd"
    with pytest.raises(PathNotAllowedError):
        resolve_allowed(escape, roots=[tmp_path])


def test_absolute_path_outside_root_is_rejected(tmp_path):
    outside = Path(tmp_path.anchor) / "definitely-not-in-tmp-path" / "secret.log"
    with pytest.raises(PathNotAllowedError):
        resolve_allowed(outside, roots=[tmp_path])


def test_symlink_escape_is_rejected(tmp_path):
    outside_dir = tmp_path.parent / "outside_target_dir"
    outside_dir.mkdir(exist_ok=True)
    link = tmp_path / "escape_link"
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks require privilege on this platform")

    with pytest.raises(PathNotAllowedError):
        resolve_allowed(link / "secret.log", roots=[tmp_path])


def test_env_override_reflected_in_allowed_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_LOG_PARSER_ALLOWED_ROOTS", str(tmp_path))
    roots = allowed_roots()
    assert roots == [tmp_path.resolve()]

    target = tmp_path / "job.log"
    result = resolve_allowed(target)
    assert result == target.resolve()
