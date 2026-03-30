"""Language frontend abstraction for CodeIR.

Each supported language implements LanguageFrontend. The indexer calls
get_frontend() to obtain the right implementation based on file extensions
found in the repository.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class LanguageFrontend(ABC):
    """Abstract interface for language-specific code analysis.

    A frontend handles everything that requires language-specific knowledge:
    parsing, entity extraction, classification, dependency resolution, and
    caller resolution. Everything downstream (ID generation, IR compression,
    storage, CLI) is language-agnostic.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Language name (e.g., 'python', 'rust')."""

    @property
    @abstractmethod
    def extensions(self) -> List[str]:
        """File extensions this frontend handles (e.g., ['.py'], ['.rs'])."""

    # ----- File discovery -----

    @abstractmethod
    def discover_package_roots(self, repo_path: Path) -> Set[str]:
        """Find top-level internal package/crate roots.

        Python: directories with __init__.py.
        Rust: crate root detected from Cargo.toml, src/ conventions.
        """

    # ----- Parsing and entity extraction -----

    @abstractmethod
    def parse_bare_entities(self, file_path: Path) -> List[Dict[str, Any]]:
        """Fast entity discovery — names, kinds, spans only. No semantic analysis.

        Used in Pass 1 for module classification and symbol collection.
        Each entity dict must contain: kind, name, qualified_name, start_line, end_line.
        """

    @abstractmethod
    def parse_entities(self, file_path: Path) -> List[Dict[str, Any]]:
        """Full entity extraction with semantic metadata.

        Used in Pass 3 for IR generation. Each entity dict must contain:
        kind, name, qualified_name, start_line, end_line, and a 'semantic' dict
        with: calls (list[str]), flags (str), assigns (int), bases (list[str]),
        and type_sig ({param_types: list[str], return_type: str|None}).
        """

    # ----- Classification -----

    @abstractmethod
    def classify_file(self, file_path: Path, source: Optional[str] = None) -> str:
        """Classify a file into a module category.

        Returns one of: core_logic, router, schema, config, compat, exceptions,
        constants, tests, init, docs, utils.
        """

    @abstractmethod
    def classify_domain(self, file_path: Path, source: Optional[str] = None) -> str:
        """Classify a file by functional domain.

        Returns one of: http, auth, crypto, db, fs, cli, async, parse, net, unknown.
        """

    # ----- Dependency extraction -----

    @abstractmethod
    def extract_imports(self, file_path: Path, source: Optional[str] = None) -> List[str]:
        """Extract top-level import/use module names from a file."""

    @abstractmethod
    def split_imports(
        self, all_imports: List[str], package_roots: Set[str],
    ) -> Tuple[List[str], List[str]]:
        """Partition imports into (internal, external)."""

    # ----- Caller resolution -----

    @abstractmethod
    def build_import_map(
        self, file_path: Path, repo_path: Path, source: Optional[str] = None,
    ) -> Dict[str, str]:
        """Build a map of locally bound names to their fully qualified origins.

        Used by caller resolution to determine which entity a call refers to.
        """


# ---------------------------------------------------------------------------
# Frontend registry
# ---------------------------------------------------------------------------

_FRONTENDS: Dict[str, type] = {}


def register_frontend(cls: type) -> type:
    """Register a LanguageFrontend subclass."""
    instance = cls()
    for ext in instance.extensions:
        _FRONTENDS[ext] = cls
    return cls


def get_frontend(extension: str) -> LanguageFrontend:
    """Return a LanguageFrontend instance for the given file extension."""
    cls = _FRONTENDS.get(extension)
    if cls is None:
        raise ValueError(f"No language frontend registered for extension: {extension}")
    return cls()


def detect_language(repo_path: Path) -> str:
    """Auto-detect the primary language of a repository.

    Counts files by extension and returns the language with the most files.
    Returns 'python' as default if no known extensions found.
    """
    counts: Dict[str, int] = {}
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        ext = path.suffix
        if ext in _FRONTENDS:
            lang = _FRONTENDS[ext]().name
            counts[lang] = counts.get(lang, 0) + 1

    if not counts:
        return "python"
    return max(counts, key=counts.get)


def get_extensions_for_language(language: str) -> List[str]:
    """Return file extensions for a language name."""
    result = []
    seen = set()
    for ext, cls in _FRONTENDS.items():
        inst = cls()
        if inst.name == language and ext not in seen:
            result.append(ext)
            seen.add(ext)
    return result or [".py"]
