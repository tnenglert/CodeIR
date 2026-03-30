"""Language-pluggable frontend for CodeIR entity extraction and analysis."""

from lang.base import LanguageFrontend, get_frontend, detect_language, get_extensions_for_language

# Register built-in frontends
import lang.python  # noqa: F401
try:
    import lang.rust  # noqa: F401
except ImportError:
    pass  # tree-sitter not available

__all__ = ["LanguageFrontend", "get_frontend", "detect_language", "get_extensions_for_language"]
