"""Path allow-listing for the MCP server's file-reading tools.

An agent supplies a file PATH; the server reads it server-side. This module
is the gate that stops that path from escaping a small set of allowed root
directories (via `..` traversal, an absolute path elsewhere, or a symlink
that resolves outside), before any file I/O happens.
"""

import os
import tempfile
from pathlib import Path


class PathNotAllowedError(ValueError):
    """Raised when a path resolves outside every allowed root."""


def allowed_roots() -> list[Path]:
    """Resolved allow-list roots.

    From env CI_LOG_PARSER_ALLOWED_ROOTS (os.pathsep-separated) if set,
    else [tempfile.gettempdir(), os.getcwd()].
    """
    raw = os.environ.get("CI_LOG_PARSER_ALLOWED_ROOTS")
    if raw:
        parts = [p for p in raw.split(os.pathsep) if p]
    else:
        parts = [tempfile.gettempdir(), os.getcwd()]
    return [Path(p).resolve() for p in parts]


def resolve_allowed(path, roots: list[Path] | None = None) -> Path:
    """Resolve `path` and require it to sit inside one of `roots`.

    Resolution follows symlinks (Path.resolve()), so containment is checked
    against the real target. Does not check existence/readability.
    """
    if roots is None:
        roots = allowed_roots()
    resolved = Path(path).resolve()
    if any(resolved == r or resolved.is_relative_to(r) for r in roots):
        return resolved
    raise PathNotAllowedError(f"{resolved} is outside allowed roots {roots}")
