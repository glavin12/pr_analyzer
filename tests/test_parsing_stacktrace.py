from agentic_pr_analyzer.parsing.limits import ParseLimits
from agentic_pr_analyzer.parsing.normalizer import normalize
from agentic_pr_analyzer.parsing.providers.generic import GenericProvider
from agentic_pr_analyzer.parsing.stacktrace import parse_js_stack, parse_python_traceback, primary_frame


def _lines(text: str):
    lines, _ = normalize(text, GenericProvider(), ParseLimits())
    return lines


def test_parse_python_traceback_tb_short_single_frame():
    text = (
        "tests/test_x.py:10: in test_foo\n"
        "    assert 1 == 2\n"
        "E   AssertionError: assert 1 == 2\n"
    )
    trace = parse_python_traceback(_lines(text))
    assert trace is not None
    assert trace.exception_type == "AssertionError"
    assert trace.message == "assert 1 == 2"
    assert len(trace.frames) == 1
    frame = trace.frames[0]
    assert frame.file_path == "tests/test_x.py"
    assert frame.line_number == 10
    assert frame.function == "test_foo"
    assert frame.in_project is True
    assert frame.raw_lineno == 1


def test_parse_python_traceback_default_tb_file_line_in_func():
    text = (
        'File "tests/test_x.py", line 10, in test_foo\n'
        "    assert 1 == 2\n"
        "E   AssertionError: assert 1 == 2\n"
    )
    trace = parse_python_traceback(_lines(text))
    assert trace is not None
    assert trace.frames[0].file_path == "tests/test_x.py"
    assert trace.frames[0].line_number == 10


def test_parse_python_traceback_chained_exception_keeps_final_e_line_and_all_frames():
    text = (
        ".tox/py3.14/site-packages/click/types.py:986: in convert\n"
        "    lf = _LazyFile(\n"
        "E   FileNotFoundError: no such file\n"
        "\n"
        "During handling of the above exception, another exception occurred:\n"
        "tests/test_x.py:288: in test_foo\n"
        "    with pytest.raises(...):\n"
        "E   AssertionError: Regex pattern did not match.\n"
    )
    trace = parse_python_traceback(_lines(text))
    assert trace is not None
    assert trace.exception_type == "AssertionError"
    assert trace.message == "Regex pattern did not match."
    assert len(trace.frames) == 2
    assert trace.frames[0].in_project is False
    assert trace.frames[1].in_project is True
    assert trace.frames[1].file_path == "tests/test_x.py"
    assert trace.frames[1].line_number == 288


def test_parse_python_traceback_in_project_heuristic_excludes_tooling_dirs():
    text = ".venv/lib/site-packages/pkg/mod.py:5: in run\n    pass\nE   ValueError: x\n"
    trace = parse_python_traceback(_lines(text))
    assert trace.frames[0].in_project is False


def test_parse_python_traceback_garbage_input_returns_none():
    assert parse_python_traceback(_lines("just some noise\nmore noise\n")) is None


def test_parse_python_traceback_empty_input_returns_none():
    assert parse_python_traceback([]) is None


def test_parse_js_stack_frame_with_function():
    text = "  at Object.<anonymous> (src/sum.test.js:7:25)\n"
    trace = parse_js_stack(_lines(text))
    assert trace is not None
    frame = trace.frames[0]
    assert frame.function == "Object.<anonymous>"
    assert frame.file_path == "src/sum.test.js"
    assert frame.line_number == 7
    assert frame.column == 25
    assert frame.in_project is True


def test_parse_js_stack_frame_without_function():
    text = "  at src/sum.test.ts:7:25\n"
    trace = parse_js_stack(_lines(text))
    assert trace is not None
    assert trace.frames[0].function is None
    assert trace.frames[0].file_path == "src/sum.test.ts"


def test_parse_js_stack_in_project_heuristic_excludes_node_modules():
    text = "  at run (node_modules/pkg/index.js:1:1)\n"
    trace = parse_js_stack(_lines(text))
    assert trace.frames[0].in_project is False


def test_parse_js_stack_garbage_input_returns_none():
    assert parse_js_stack(_lines("nothing here\n")) is None


def test_primary_frame_prefers_last_in_project_frame():
    text = (
        ".tox/py3.14/site-packages/click/types.py:986: in convert\n"
        "    lf = _LazyFile(\n"
        "E   FileNotFoundError: no such file\n"
        "tests/test_x.py:289: in test_foo\n"
        "    do_thing()\n"
        "E   AssertionError: boom\n"
    )
    trace = parse_python_traceback(_lines(text))
    frame = primary_frame(trace)
    assert frame.file_path == "tests/test_x.py"
    assert frame.line_number == 289


def test_primary_frame_falls_back_to_last_frame_when_none_in_project():
    text = ".venv/site-packages/pkg/mod.py:5: in run\n    pass\nE   ValueError: x\n"
    trace = parse_python_traceback(_lines(text))
    frame = primary_frame(trace)
    assert frame.file_path == ".venv/site-packages/pkg/mod.py"


def test_primary_frame_none_for_none_trace_or_no_frames():
    assert primary_frame(None) is None
