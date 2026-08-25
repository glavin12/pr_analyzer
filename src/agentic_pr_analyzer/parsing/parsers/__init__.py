from .base import Parser
from .generic_parser import GenericParser

# Fixed priority order. Section 2 adds the pytest parser ahead of
# GenericParser here; the registry contract (pipeline.py) runs specialized
# parsers first and only falls back to GenericParser when none produced a
# diagnostic.
PARSER_REGISTRY: tuple[Parser, ...] = (GenericParser(),)

__all__ = ["Parser", "GenericParser", "PARSER_REGISTRY"]
