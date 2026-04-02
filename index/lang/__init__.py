"""Language-pluggable backend abstraction for CodeIR.

Each supported language implements the LanguageBackend protocol, providing
parsing, entity extraction, classification, and caller resolution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple


class LanguageBackend(Protocol):
    """Interface that each language plugin must implement."""

    language: str
    extensions: List[str]

    def parse_file(self, path: Path) -> Any:
        """Parse a source file into a language-specific tree.

        Returns the parsed tree (e.g., ast.Module for Python, tree-sitter Tree for Rust),
        or None if the file cannot be parsed.
        """
        ...

    def extract_entities(self, path: Path, include_semantic: bool = True) -> List[dict]:
        """Extract entity dicts from a source file.

        Each dict has: kind, name, qualified_name, start_line, end_line.
        When include_semantic is True, also includes a 'semantic' dict with:
        calls, flags, assigns, bases, type_sig.
        """
        ...

    def extract_imports(self, tree: Any, file_path: Optional[Path] = None) -> List[str]:
        """Extract top-level import/use module names from a parsed tree."""
        ...

    def classify_file(self, file_path: Path, tree: Any) -> str:
        """Classify a file into a category (core_logic, tests, schema, etc.)."""
        ...

    def classify_domain(self, file_path: Path, tree: Any) -> str:
        """Classify a file's domain (http, db, auth, etc.)."""
        ...

    def discover_package_roots(self, repo_path: Path) -> Set[str]:
        """Find internal package/crate boundaries."""
        ...

    def split_imports(self, all_imports: List[str], package_roots: Set[str]) -> Tuple[List[str], List[str]]:
        """Split imports into (internal, external) based on known package roots."""
        ...

    def build_import_map(self, tree: Any, file_path: Path, repo_path: Path) -> Dict[str, str]:
        """Map locally bound names to their fully qualified origins.

        Used by caller resolution to resolve import-based calls.
        """
        ...

    def get_call_stoplist(self) -> Set[str]:
        """Return language-specific names too common for useful caller links."""
        ...


def detect_language(repo_path: Path) -> str:
    """Detect the primary language of a repository.

    Checks for language-specific marker files:
    - Cargo.toml -> "rust"
    - setup.py / pyproject.toml / __init__.py -> "python"
    Falls back to file extension ratio.
    """
    if (repo_path / "Cargo.toml").exists():
        return "rust"

    python_markers = ["setup.py", "pyproject.toml", "setup.cfg"]
    for marker in python_markers:
        if (repo_path / marker).exists():
            return "python"

    # Check for __init__.py in any subdirectory
    for child in repo_path.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            return "python"

    # Fallback: count file extensions
    py_count = len(list(repo_path.rglob("*.py")))
    rs_count = len(list(repo_path.rglob("*.rs")))

    if rs_count > py_count:
        return "rust"
    return "python"


def get_backend(language: str) -> LanguageBackend:
    """Return the appropriate LanguageBackend for the given language."""
    if language == "python":
        from index.lang.python_backend import PythonBackend
        return PythonBackend()
    elif language == "rust":
        from index.lang.rust_backend import RustBackend
        return RustBackend()
    else:
        raise ValueError(f"Unsupported language: {language}")
