"""raw log -> normalize -> segment -> run parser registry -> FailureReport.

`parse_log` never raises: a top-level guard catches any stage failure and
returns a valid FailureReport carrying a single UNKNOWN diagnostic plus
`stats["fatal"]`, rather than propagating (graceful degradation).
"""

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
from .providers import detect_provider
from .segmentation import build_sections

_PROVIDER_SAMPLE_CHARS = 4000


def parse_log(
    content: str, source: LogSource | None = None, limits: ParseLimits = ParseLimits()
) -> FailureReport:
    try:
        return _parse_log_inner(content, source, limits)
    except Exception:
        return _fatal_report(source)


def _fatal_report(source: LogSource | None) -> FailureReport:
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
        stats={"fatal": True},
    )


def _parse_log_inner(content: str, source: LogSource | None, limits: ParseLimits) -> FailureReport:
    provider = detect_provider(content[:_PROVIDER_SAMPLE_CHARS])
    lines, norm_stats = normalize(content, provider, limits)
    lines, section_list = build_sections(lines)

    diagnostics, selected_parser, parse_failures = _run_registry(lines, section_list)

    diagnostics_truncated = len(diagnostics) > limits.max_diagnostics
    if diagnostics_truncated:
        diagnostics = diagnostics[: limits.max_diagnostics]
    diagnostics = tuple(diagnostics)

    clusters, primary_cluster = _build_clusters(diagnostics, lines)
    exit_code = next((d.exit_code for d in diagnostics if d.exit_code is not None), None)

    truncated = norm_stats["truncated_lines"] or norm_stats["lines_over_limit"] > 0 or diagnostics_truncated

    stats = {
        "lines_processed": len(lines),
        "sections": len(section_list),
        "diagnostics_detected": len(diagnostics),
        "parser_selected": selected_parser,
        "parse_failures": parse_failures,
        "truncated_lines": norm_stats["truncated_lines"],
        "lines_over_limit": norm_stats["lines_over_limit"],
        "diagnostics_truncated": diagnostics_truncated,
    }

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


def _run_registry(lines, sections) -> tuple[list[Diagnostic], str | None, int]:
    """Registry contract: specialized (non-fallback) parsers run first and

    their results are concatenated; GenericParser only contributes when no
    specialized parser produced a diagnostic.
    """
    specialized_diagnostics: list[Diagnostic] = []
    specialized_ran = False
    parse_failures = 0

    for parser in PARSER_REGISTRY:
        if parser.is_fallback:
            continue
        try:
            if parser.detect(lines, sections):
                specialized_ran = True
                specialized_diagnostics.extend(parser.parse(lines, sections))
        except Exception:
            parse_failures += 1

    if specialized_ran and specialized_diagnostics:
        return specialized_diagnostics, "specialized", parse_failures

    for parser in PARSER_REGISTRY:
        if not parser.is_fallback:
            continue
        try:
            if parser.detect(lines, sections):
                return parser.parse(lines, sections), parser.name, parse_failures
        except Exception:
            parse_failures += 1

    return [], None, parse_failures


def _build_clusters(diagnostics, lines):
    lines_by_no = {line.raw_lineno: line for line in lines}
    clusters = []
    for diagnostic in diagnostics:
        section_id = None
        if diagnostic.evidence:
            evidence_line = lines_by_no.get(diagnostic.evidence[0])
            if evidence_line is not None:
                section_id = evidence_line.section_id
        clusters.append(
            FailureCluster(
                primary=diagnostic,
                related=(),
                section_id=section_id,
                classification=diagnostic.type,
            )
        )
    clusters = tuple(clusters)

    primary_diagnostic = _pick_primary(diagnostics)
    primary_cluster = next(
        (c for c in clusters if c.primary is primary_diagnostic), None
    ) if primary_diagnostic is not None else None
    return clusters, primary_cluster


def _pick_primary(diagnostics) -> Diagnostic | None:
    if not diagnostics:
        return None
    located = [d for d in diagnostics if d.file is not None]
    pool = located if located else diagnostics
    return max(pool, key=lambda d: d.confidence)
