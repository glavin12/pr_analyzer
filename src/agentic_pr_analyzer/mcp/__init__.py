"""MCP integration layer (thin adapter over the deterministic parser).

`build_summary` turns a `FailureReport` into a tiered, evidence-backed
summary dict for coding agents -- no LLM, no diagnosis, pure translation.
The MCP server entry point is added later by another agent; this package
does not import it.
"""

from .adapter import build_summary

__all__ = ["build_summary"]
