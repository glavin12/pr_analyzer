"""raw log -> normalize -> segment -> run parser registry -> FailureReport.

`parse_log` never raises: a top-level guard catches any stage failure and
returns a valid FailureReport carrying a single UNKNOWN diagnostic plus
`stats["fatal"]`, rather than propagating (graceful degradation).
"""

import dataclasses
import time

from .clustering import build_clusters, dedup_key
from .confidence import BARE_ERROR_MARKER
from .limits import ParseLimits
from .model import (
    SCHEMA_VERSION,
    Diagnostic,
    DiagnosticType,
    FailureCluster,
    FailureReport,
    LogSource,
    Severity,
)
from .normalizer import normalize
from .parsers import PARSER_REGISTRY
from .process_failure import find_process_failure
from .providers import detect_provider
from .segmentation import build_sections

_PROVIDER_SAMPLE_CHARS = 4000

# D5: how much of the ORIGINAL content to re-normalize when head truncation
# dropped the tail. CI's highest-value artifacts (`##[error]...exit code N`,
# pytest's short summary, jest's footer) all live at the tail, which head
# truncation is guaranteed to lose.
_TAIL_RESCUE_CHARS = 64_000


def _base_stats() -> dict:
    """The full stats key set, zero-valued.

    Both the success path and the fatal path emit exactly these keys, so a
    consumer never has to branch on key presence (`stats["fatal"]` used to
    exist only on the fatal path).

    Every value here is a count, a bool, or an identifier drawn from a closed
    set. No value is ever derived from log text -- see invariant S7-M1 and
    tests/test_parsing_stats.py.

    `diagnostics_deduplicated` and `clusters_built` are OWNED BY SECTION 5.
    This function supplies only their default; the clustering stage
    overwrites them. Never assign them unconditionally after clustering --
    that would zero out Section 5's work (reconciliation C2).
    """
    return {
        # --- volume ---
        "logs_processed": 1,
        "lines_processed": 0,
        "bytes_processed": 0,
        "sections": 0,
        "unknown_sections": 0,
        # --- detection ---
        "diagnostics_detected": 0,
        "diagnostics_deduplicated": 0,
        "clusters_built": 0,
        "parser_selected": None,
        "parsers_fired": [],
        "fallback_parser_used": False,
        "parse_failures": 0,
        # --- limits / degradation (Section 6) ---
        "truncated_lines": False,
        "lines_over_limit": 0,
        "diagnostics_truncated": False,
        "truncated_chars": False,
        "evidence_truncated": False,
        "tail_rescued": False,
        "peak_lines_retained": 0,
        "fatal": False,
        # --- security ---
        "secrets_masked": 0,
        "secrets_masked_by_class": {},
        # --- non-deterministic bucket, excluded from golden snapshots by name ---
        "runtime": {"parse_ms": 0},
    }


def _cap_evidence(diagnostics: list, limits: ParseLimits) -> tuple[list, bool]:
    """D4. Cap Diagnostic.evidence centrally, at the one choke point every
    diagnostic routes through, rather than in each parser -- three parsers
    build evidence today and parser #5 would be a fourth place to forget.

    Keeps the FIRST N entries (reconciliation C3): Section 5 ranks on
    min(evidence) and derives section_id from evidence[0], so a tail-biased
    or sampled cap would silently change clustering. Nothing is lost --
    source_range still carries the authoritative full extent, so evidence is
    a *sample* and the range is the *extent*.
    """
    capped, truncated = [], False
    for d in diagnostics:
        if len(d.evidence) > limits.max_context_lines:
            truncated = True
            d = dataclasses.replace(d, evidence=d.evidence[: limits.max_context_lines])
        capped.append(d)
    return capped, truncated


def _rescue_tail_exit_code(content, provider, limits):
    """D5. Head truncation loses the exit code; re-normalize a bounded tail
    slice and re-run the shared extractor over it.

    Touches zero parsers and preserves line-number fidelity exactly -- by
    making NO line claims at all. The slice's line numbers do not correspond
    to the report's, so the rescued diagnostic carries evidence=() and
    source_range=None and says so via metadata["from_truncated_tail"].
    A mid-line slice cannot forge a marker either (the marker regex is
    anchored at payload start), so the worst case is that it finds nothing.
    """
    tail_lines, _ = normalize(content[-_TAIL_RESCUE_CHARS:], provider, limits)
    found = find_process_failure(tail_lines, "pipeline")
    if found is None:
        return None
    return dataclasses.replace(
        found,
        evidence=(),
        source_range=None,
        metadata={**found.metadata, "from_truncated_tail": True},
    )


def parse_log(
    content: str, source: LogSource | None = None, limits: ParseLimits = ParseLimits()
) -> FailureReport:
    try:
        return _parse_log_inner(content, source, limits)
    except Exception:
        return _fatal_report(source, len(content) if isinstance(content, str) else 0)


def _fatal_stats(bytes_processed: int) -> dict:
    stats = _base_stats()
    stats.update({"fatal": True, "bytes_processed": bytes_processed, "clusters_built": 1})
    return stats


def _fatal_report(source: LogSource | None, bytes_processed: int = 0) -> FailureReport:
    diagnostic = Diagnostic(
        type=DiagnosticType.UNKNOWN,
        severity=Severity.ERROR,
        tool=None,
        message="Fatal parser error: input could not be processed.",
        file=None,
        line=None,
        column=None,
        source_range=None,
        stack_trace=None,
        test_id=None,
        exit_code=None,
        confidence=BARE_ERROR_MARKER,
        evidence=(),
        metadata={},
        parser="pipeline",
    )
    cluster = FailureCluster(
        primary=diagnostic, related=(), section_id=None, classification=diagnostic.type
    )
    return FailureReport(
        schema_version=SCHEMA_VERSION,
        source=source,
        provider="unknown",
        sections=(),
        diagnostics=(diagnostic,),
        clusters=(cluster,),
        primary_cluster=cluster,
        exit_code=None,
        raw_line_count=0,
        truncated=False,
        stats=_fatal_stats(bytes_processed),
    )


def _parse_log_inner(content: str, source: LogSource | None, limits: ParseLimits) -> FailureReport:
    started = time.perf_counter()
    provider = detect_provider(content[:_PROVIDER_SAMPLE_CHARS])
    lines, norm_stats = normalize(content, provider, limits)
    lines, section_list = build_sections(lines)

    diagnostics, selected_parser, parse_failures, parsers_fired = _run_registry(lines, section_list)

    diagnostics_truncated = len(diagnostics) > limits.max_diagnostics
    if diagnostics_truncated:
        diagnostics = diagnostics[: limits.max_diagnostics]

    # D5: only when the head was actually truncated AND it yielded no
    # process failure of its own -- otherwise this would add a second one.
    tail_rescued = False
    if norm_stats["truncated_lines"] and not any(
        d.type is DiagnosticType.PROCESS_FAILURE for d in diagnostics
    ):
        rescued = _rescue_tail_exit_code(content, provider, limits)
        if rescued is not None:
            diagnostics.append(rescued)
            tail_rescued = True

    diagnostics, evidence_truncated = _cap_evidence(diagnostics, limits)
    diagnostics = tuple(diagnostics)

    clusters, primary_cluster = build_clusters(diagnostics, lines)
    exit_code = next((d.exit_code for d in diagnostics if d.exit_code is not None), None)

    truncated = (
        norm_stats["truncated_lines"]
        or norm_stats["lines_over_limit"] > 0
        or diagnostics_truncated
        or norm_stats["truncated_chars"]
        or evidence_truncated
    )

    by_class = {cls: n for cls, n in norm_stats["secrets_masked"].items() if n}

    stats = _base_stats()
    stats.update(
        {
            "lines_processed": len(lines),
            "bytes_processed": len(content),
            "sections": len(section_list),
            # "unknown" here means an anonymous or unbalanced ##[group] -- a
            # LogSection with no title. The engine has no section taxonomy,
            # so the other reading of the word would be meaningless.
            "unknown_sections": sum(1 for s in section_list if s.title is None),
            "diagnostics_detected": len(diagnostics),
            "parser_selected": selected_parser,
            "parsers_fired": parsers_fired,
            "fallback_parser_used": selected_parser is not None
            and selected_parser != "specialized",
            "parse_failures": parse_failures,
            "truncated_lines": norm_stats["truncated_lines"],
            "lines_over_limit": norm_stats["lines_over_limit"],
            "diagnostics_truncated": diagnostics_truncated,
            "truncated_chars": norm_stats["truncated_chars"],
            "evidence_truncated": evidence_truncated,
            "tail_rescued": tail_rescued,
            # Permanently equals lines_processed: Section 6 declined streaming
            # (reconciliation C7), so nothing is ever released mid-parse. Kept
            # as the deterministic memory proxy alongside bytes_processed,
            # said honestly rather than promising a designed-away future.
            "peak_lines_retained": len(lines),
            "secrets_masked": sum(by_class.values()),
            "secrets_masked_by_class": by_class,
            # Section 5 owns these two -- computed here, never defaulted over.
            "clusters_built": len(clusters),
            "diagnostics_deduplicated": len(diagnostics) - len({dedup_key(d) for d in diagnostics}),
            "runtime": {"parse_ms": int((time.perf_counter() - started) * 1000)},
        }
    )

    return FailureReport(
        schema_version=SCHEMA_VERSION,
        source=source,
        provider=provider.name,
        sections=section_list,
        diagnostics=diagnostics,
        clusters=clusters,
        primary_cluster=primary_cluster,
        exit_code=exit_code,
        raw_line_count=len(lines),
        truncated=truncated,
        stats=stats,
    )


def _run_registry(lines, sections) -> tuple[list[Diagnostic], str | None, int, list[str]]:
    """Registry contract: specialized (non-fallback) parsers run first and

    their results are concatenated; GenericParser only contributes when no
    specialized parser produced a diagnostic.

    Also reports `parsers_fired` -- the sorted names of specialized parsers
    that produced at least one diagnostic. `parser_selected` collapses that
    to the single string "specialized", which throws away which ones; this
    recovers it additively without changing the existing value.
    """
    specialized_diagnostics: list[Diagnostic] = []
    specialized_ran = False
    parse_failures = 0
    fired: set[str] = set()

    for parser in PARSER_REGISTRY:
        if parser.is_fallback:
            continue
        try:
            if parser.detect(lines, sections):
                specialized_ran = True
                produced = parser.parse(lines, sections)
                if produced:
                    fired.add(parser.name)
                specialized_diagnostics.extend(produced)
        except Exception:
            parse_failures += 1

    if specialized_ran and specialized_diagnostics:
        return specialized_diagnostics, "specialized", parse_failures, sorted(fired)

    for parser in PARSER_REGISTRY:
        if not parser.is_fallback:
            continue
        try:
            if parser.detect(lines, sections):
                return parser.parse(lines, sections), parser.name, parse_failures, []
        except Exception:
            parse_failures += 1

    return [], None, parse_failures, []
