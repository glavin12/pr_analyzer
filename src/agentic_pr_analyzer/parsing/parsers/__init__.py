from .base import Parser
from .generic_parser import GenericParser
from .js_test_parser import JsTestParser
from .pytest_parser import PytestParser

# Fixed priority order. Specialized parsers (pytest, jest/vitest, ...) run
# first; the registry contract (pipeline.py) only falls back to
# GenericParser when no specialized parser produced a diagnostic.
PARSER_REGISTRY: tuple[Parser, ...] = (PytestParser(), JsTestParser(), GenericParser())

__all__ = ["Parser", "GenericParser", "PytestParser", "JsTestParser", "PARSER_REGISTRY"]
