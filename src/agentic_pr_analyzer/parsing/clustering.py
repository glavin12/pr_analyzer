"""Section 5: correlation, deduplication & failure clustering.

One pass over `diagnostics` (already in emission order): [A] key each
diagnostic for dedup, [B] collapse exact duplicates down to a first-emitted
representative per key, [C] correlate the representatives into clusters via
a fixed rule ladder (S1 -> C1 -> C2a -> C2b -> C3), [D] rank each cluster's
members to pick its primary, [E] attach every PROCESS_FAILURE representative
to the best-ranked cluster (or, if nothing else clustered, let each stand
alone as a "job-level" cluster) and order the final `clusters` tuple.
See docs/plans/section-5-correlation.md for the full rule table and the
reasoning behind each inclusion/exclusion -- this module is the literal
implementation of that document, not a re-derivation.

`dedup_key`/`normalize_message` have exactly one consumer (this module) and
are kept public only so tests can exercise them directly; see the plan
doc's S4 for why this isn't split into a separate `dedup.py`.
"""

import re
from collections import defaultdict

from .model import (
    Diagnostic,
    DiagnosticRole,
    DiagnosticType,
    FailureCluster,
    Severity,
    WorkflowMarker,
)

# ---------------------------------------------------------------- normalization

_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
# Runs before the hex rule -- a temp-dir segment (e.g. "pytest-21") looks
# hex-ish enough to get eaten by it otherwise.
_TMP_PATH_RE = re.compile(r"(?i)(?:[a-z]:)?/(?:[^\s'\"]*/)?(?:tmp|temp)/[^\s'\"]*")
_HEX_RE = re.compile(r"(?i)\b(?:0x[0-9a-f]{4,}|[0-9a-f]{12,})\b")
_DURATION_RE = re.compile(r"(?i)\b\d+(?:\.\d+)?\s?(?:ms|s|sec|secs|seconds|m|min)\b")
_PID_RE = re.compile(r"(?i)\b(?:pid|port)[= ]\d+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_message(msg: str | None) -> str:
    """Key-construction only -- `Diagnostic.message` itself is never rewritten.

    Deliberately does NOT strip bare integers: that would collide "expected
    3 to be -3" with "expected 4 to be -4" (see
    test_normalize_message_keeps_bare_integers). The rules below already
    remove every source of per-run variance seen in the committed corpus;
    adding a bare-integer rule needs a fixture proving these aren't enough.
    """
    text = (msg or "").replace("\\", "/")
    text = _TIMESTAMP_RE.sub("<TS>", text)
    text = _TMP_PATH_RE.sub("<TMP>", text)
    text = _HEX_RE.sub("<HEX>", text)
    text = _DURATION_RE.sub("<DUR>", text)
    text = _PID_RE.sub("pid=<ID>", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text.casefold()


def norm_path(p: str | None) -> str:
    """Keying only. Casefolding is technically wrong on a case-sensitive

    filesystem, but the anchor fixture is a Windows job and the cost of a
    false collapse (two files differing only in case in one repo) is far
    below the cost of never collapsing a Windows path variant.
    """
    text = (p or "").replace("\\", "/")
    text = re.sub(r"/+", "/", text)
    if text.startswith("./"):
        text = text[2:]
    return text.casefold()


def dedup_key(d: Diagnostic) -> str:
    """Identity for exact-duplicate collapsing.

    Excludes `parser` on purpose -- the whole point of this section: all
    four parsers call `find_process_failure`, so a log that trips two of
    them emits the identical exit-code diagnostic twice, differing only in
    `parser`, and that pair MUST collide. Also excludes `evidence`/
    `source_range` (exactly what differs between two occurrences of one
    failure), `confidence` (a constant from confidence.py, not evidence),
    and any stack signature (would only make the key MORE selective --
    i.e. cause a truncated/full pair of the same failure to fail to
    collapse; frames are still used, as a set, by rule C3).
    """
    return "|".join(
        (
            d.type.value,
            d.severity.value,
            d.tool or "",
            str(d.metadata.get("code") or d.metadata.get("rule") or ""),
            norm_path(d.file),
            "" if d.line is None else str(d.line),
            "" if d.column is None else str(d.column),
            d.test_id or "",
            normalize_message(d.message),
        )
    )


# ---------------------------------------------------------------- ranking ladders

_TYPE_RANK = {
    DiagnosticType.TEST_FAILURE: 0,  # names a concrete broken behaviour
    DiagnosticType.EXCEPTION: 1,
    DiagnosticType.COMPILER_ERROR: 2,  # names a concrete broken symbol
    DiagnosticType.LINT_ERROR: 3,
    # Provisional: no parser emits DEPENDENCY_ERROR yet. "install failed =>
    # nothing downstream is meaningful" probably wants rank 0 once one does
    # -- revisit then, not now.
    DiagnosticType.DEPENDENCY_ERROR: 4,
    DiagnosticType.UNKNOWN: 5,
    DiagnosticType.PROCESS_FAILURE: 6,  # names nothing
}

_SEVERITY_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def _earliest(evidence: tuple[int, ...]) -> int:
    return min(evidence) if evidence else 0


def _member_rank_key(d: Diagnostic, emission_index: dict) -> tuple:
    """Which member of a cluster becomes its `primary` -- lower tuple wins.

    Rank 2 (diagnostic type) sits above rank 6 (line order): GitHub Actions
    halts a job at the first failing step unless `continue-on-error` is set,
    so a cross-step cascade is the exception, not the rule, and hoisting
    line order above type rank would make a lint error outrank a test
    failure in the common lint-step-then-test-step layout.
    ponytail: this is the one real judgement call in this module -- if
    Slice 6's labeled corpus shows earliest-failure should beat type rank,
    swap discriminators 2 and 6, here and in `_cluster_rank_key`.
    """
    return (
        _SEVERITY_RANK[d.severity],
        _TYPE_RANK[d.type],
        0 if d.file is not None else 1,
        -d.confidence,
        _earliest(d.evidence),
        emission_index[id(d)],
    )


def _cluster_rank_key(members: list, creation_index: int, emission_index: dict) -> tuple:
    """`clusters` order, and `clusters[0]` becomes `primary_cluster` -- lower

    tuple wins. Applied to pass-1 (non-PROCESS_FAILURE) clusters only.
    """
    primary = min(members, key=lambda d: _member_rank_key(d, emission_index))
    has_error = 0 if any(m.severity is Severity.ERROR for m in members) else 1
    evidences = [_earliest(m.evidence) for m in members if m.evidence]
    return (
        has_error,
        _TYPE_RANK[primary.type],
        0 if primary.file is not None else 1,
        -primary.confidence,
        -len(members),
        min(evidences) if evidences else 0,
        creation_index,
    )


# ---------------------------------------------------------------- correlation


def _frame_set(d: Diagnostic) -> set:
    if d.stack_trace is None:
        return set()
    return {
        (norm_path(f.file_path), f.line_number)
        for f in d.stack_trace.frames
        if f.in_project
    }


def _g1_blocks(anchor: Diagnostic, d: Diagnostic) -> bool:
    """Global guard: two diagnostics with different non-None test_ids never

    share a cluster -- the test is the unit of failure; two failing tests in
    one file, or two tests sharing an in-project helper frame, stay separate.
    """
    return anchor.test_id is not None and d.test_id is not None and anchor.test_id != d.test_id


def _is_marker_evidence(d: Diagnostic, lines_by_no: dict) -> bool:
    if not d.evidence:
        return False
    return all(
        lines_by_no.get(ln) is not None
        and lines_by_no[ln].marker in (WorkflowMarker.ERROR, WorkflowMarker.WARNING)
        for ln in d.evidence
    )


def _correlate(non_pf_reps: list, lines_by_no: dict) -> tuple[list, dict]:
    """Pass 1: cluster every non-PROCESS_FAILURE representative via

    S1 -> C1 -> C2a -> C2b -> C3, first match anywhere wins, else it opens a
    new cluster. Rule-major, scanning existing clusters in creation order --
    no fixpoint iteration, no union-find, so output order only ever depends
    on emission order, never on set/dict iteration order.

    ponytail: O(rules * n^2) scan, bounded by ParseLimits.max_diagnostics =
    500. Index clusters by test_id/(file, tool) in dicts if Section 6's perf
    work shows it matters.
    """
    builders: list[dict] = []
    rep_builder: dict[int, dict] = {}

    for d in non_pf_reps:
        target = None
        relation = None

        # S1 -- a marker-echoed diagnostic summarizing one already clustered
        # from unpolluted tool output.
        if _is_marker_evidence(d, lines_by_no):
            for b in builders:
                for t in b["members"]:
                    if (
                        normalize_message(d.message) == normalize_message(t.message)
                        and d.line == t.line
                        and d.column == t.column
                        and not _is_marker_evidence(t, lines_by_no)
                    ):
                        target, relation = b, "summary"
                        break
                if target:
                    break

        # C1 -- same test.
        if target is None and d.test_id is not None:
            for b in builders:
                if b["anchor"].test_id == d.test_id:
                    target, relation = b, "member"
                    break

        # C2a -- same file + line.
        if target is None:
            dp = norm_path(d.file)
            if dp:
                for b in builders:
                    if _g1_blocks(b["anchor"], d):
                        continue
                    a = b["anchor"]
                    if norm_path(a.file) == dp and d.line is not None and d.line == a.line:
                        target, relation = b, "member"
                        break

        # C2b -- same file, same tool, file-level tools only (no test_id on
        # either side -- a per-test rule is C1's job, not this one's).
        if target is None:
            dp = norm_path(d.file)
            if dp and d.tool is not None and d.test_id is None:
                for b in builders:
                    if _g1_blocks(b["anchor"], d):
                        continue
                    a = b["anchor"]
                    if a.test_id is None and a.tool == d.tool and norm_path(a.file) == dp:
                        target, relation = b, "member"
                        break

        # C3 -- shared in-project stack frame.
        if target is None and d.stack_trace is not None:
            d_frames = _frame_set(d)
            if d_frames:
                for b in builders:
                    if _g1_blocks(b["anchor"], d):
                        continue
                    a = b["anchor"]
                    if a.stack_trace is not None and d_frames & _frame_set(a):
                        target, relation = b, "member"
                        break

        if target is None:
            b = {"anchor": d, "members": [d], "summary": [], "consequence": [], "duplicate": []}
            builders.append(b)
            rep_builder[id(d)] = b
        elif relation == "summary":
            target["summary"].append(d)
            rep_builder[id(d)] = target
        else:
            target["members"].append(d)
            rep_builder[id(d)] = target

    return builders, rep_builder


def build_clusters(
    diagnostics: tuple[Diagnostic, ...], lines: list
) -> tuple[tuple[FailureCluster, ...], FailureCluster | None]:
    """[A]+[B]+[C]+[D]+[E] -- see the module docstring for the stage names.

    Non-lossy: nothing is dropped from `diagnostics`; dedup and correlation
    are expressed purely as cluster membership + `DiagnosticRole`.
    """
    if not diagnostics:
        return (), None

    lines_by_no = {line.raw_lineno: line for line in lines}
    emission_index = {id(d): i for i, d in enumerate(diagnostics)}

    # [A] + [B]: first occurrence per dedup_key is the representative that
    # goes on to correlation; every later occurrence is remembered so it can
    # attach next to wherever ITS representative ends up, with role
    # DUPLICATE -- dedup is expressed as cluster membership, never by
    # dropping evidence from `diagnostics`.
    representatives: list[Diagnostic] = []
    seen: dict[str, Diagnostic] = {}
    duplicates_of: dict[int, list[Diagnostic]] = defaultdict(list)
    for d in diagnostics:
        key = dedup_key(d)
        rep = seen.get(key)
        if rep is None:
            seen[key] = d
            representatives.append(d)
        else:
            duplicates_of[id(rep)].append(d)

    non_pf_reps = [d for d in representatives if d.type is not DiagnosticType.PROCESS_FAILURE]
    pf_reps = [d for d in representatives if d.type is DiagnosticType.PROCESS_FAILURE]

    builders, rep_builder = _correlate(non_pf_reps, lines_by_no)

    if builders:
        # [C4]/pass 3: every PROCESS_FAILURE representative is a consequence
        # of whichever pass-1 cluster ranks best -- "the job exited N
        # because of the primary failure" is never arbitrary the way
        # "nearest preceding cluster by line number" would be (rejected
        # alternative, see plan doc).
        creation_index = {id(b): i for i, b in enumerate(builders)}
        ordered_builders = sorted(
            builders,
            key=lambda b: _cluster_rank_key(b["members"], creation_index[id(b)], emission_index),
        )
        winner = ordered_builders[0]
        for pf in pf_reps:
            winner["consequence"].append(pf)
            rep_builder[id(pf)] = winner
    else:
        # No parseable tool output at all -- each distinct PROCESS_FAILURE is
        # its own job-level cluster: a red job whose only evidence is the
        # exit code itself. Not ranked -- there is nothing to compare.
        ordered_builders = []
        for pf in pf_reps:
            b = {"anchor": pf, "members": [pf], "summary": [], "consequence": [], "duplicate": []}
            ordered_builders.append(b)
            rep_builder[id(pf)] = b

    for rep in representatives:
        dupes = duplicates_of.get(id(rep))
        if dupes:
            rep_builder[id(rep)]["duplicate"].extend(dupes)

    clusters = []
    for b in ordered_builders:
        primary = min(b["members"], key=lambda d: _member_rank_key(d, emission_index))
        tagged = (
            [(m, DiagnosticRole.SECONDARY) for m in b["members"] if m is not primary]
            + [(m, DiagnosticRole.SUMMARY) for m in b["summary"]]
            + [(m, DiagnosticRole.CONSEQUENCE) for m in b["consequence"]]
            + [(m, DiagnosticRole.DUPLICATE) for m in b["duplicate"]]
        )
        # related is ordered by emission order, related_roles index-aligned
        # -- one less rule to specify/test than a role-sorted order.
        tagged.sort(key=lambda pair: emission_index[id(pair[0])])

        # Section of the primary's first evidence line -- verbatim from the
        # pre-Section-5 pipeline.py (now per-cluster instead of per-diagnostic).
        section_id = None
        if primary.evidence:
            evidence_line = lines_by_no.get(primary.evidence[0])
            if evidence_line is not None:
                section_id = evidence_line.section_id

        clusters.append(
            FailureCluster(
                primary=primary,
                related=tuple(m for m, _ in tagged),
                section_id=section_id,
                classification=primary.type,
                related_roles=tuple(r for _, r in tagged),
                key=dedup_key(primary),
            )
        )

    clusters = tuple(clusters)
    primary_cluster = clusters[0] if clusters else None
    return clusters, primary_cluster
