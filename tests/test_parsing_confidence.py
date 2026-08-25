from agentic_pr_analyzer.parsing import confidence


def test_confidence_table_ordering():
    assert confidence.EXACT_TOOL_FORMAT == 0.9
    assert confidence.KNOWN_SUMMARY == 0.85
    assert confidence.GENERIC_FILE_LINE_ERROR == 0.6
    assert confidence.BARE_ERROR_MARKER == 0.4
    assert (
        confidence.EXACT_TOOL_FORMAT
        > confidence.KNOWN_SUMMARY
        > confidence.GENERIC_FILE_LINE_ERROR
        > confidence.BARE_ERROR_MARKER
    )
