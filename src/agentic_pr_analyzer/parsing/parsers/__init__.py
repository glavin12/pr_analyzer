from .base import Parser
from .compiler_parser import CompilerParser
from .generic_parser import GenericParser
from .js_test_parser import JsTestParser
from .pytest_parser import PytestParser

# Fixed priority order. Specialized parsers (pytest, jest/vitest, tsc/eslint,
# ...) run first; the registry contract (pipeline.py) only falls back to
# GenericParser when no specialized parser produced a diagnostic.
PARSER_REGISTRY: tuple[Parser, ...] = (
    PytestParser(),
    JsTestParser(),
    CompilerParser(),
    GenericParser(),
)

__all__ = [
    "Parser",
    "GenericParser",
    "PytestParser",
    "JsTestParser",
    "CompilerParser",
    "PARSER_REGISTRY",
]
