"""Language-pluggable support for CodeIR.

Each supported language implements the LanguageSupport interface.
The registry maps language names to their implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class LanguageSupport(ABC):
    """Interface that each language implementation must provide.

    Separates language-specific concerns (parsing, classification, import
    resolution) from language-agnostic concerns (ID generation, IR
    compression, storage, query, CLI).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Language name (e.g., 'python', 'rust')."""
        ...

    @property
    @abstractmethod
    def extensions(self) -> List[str]:
        """File extensions including dot (e.g., ['.py'], ['.rs'])."""
        ...

    @property
    @abstractmethod
    def call_stoplist(self) -> Set[str]:
        """Names too common to produce useful caller relationships."""
        ...

    # -- Parsing --

    @abstractmethod
    def parse_entities(self, file_path: Path, include_semantic: bool = True) -> List[dict]:
        """Extract entities from a source file.

        Each entity dict must contain at minimum:
            kind: str            — entity kind (function, method, class, struct, etc.)
            name: str            — bare name
            qualified_name: str  — dot-separated qualified name
            start_line: int
            end_line: int

        When include_semantic is True, also include:
            semantic: dict with keys:
                calls: List[str]   — names/qualified names of called entities
                flags: str         — behavioral flags (R, E, I, L, T, W, A, U, M, etc.)
                assigns: int       — assignment count
                bases: List[str]   — base classes/traits
                type_sig: dict     — {param_types: List[str], return_type: Optional[str]}
        """
        ...

    @abstractmethod
    def parse_ast(self, file_path: Path):
        """Parse a source file into a language-specific AST/tree.

        Returns None on parse errors. The returned object is opaque to
        callers outside the language module — it's passed back into
        classify_file, classify_domain, extract_import_names, and
        build_import_map.
        """
        ...

    # -- Classification --

    @abstractmethod
    def classify_file(self, file_path: Path, tree) -> str:
        """Classify a source file into a module category.

        Returns one of the standard categories:
        core_logic, router, schema, config, compat, exceptions, constants,
        tests, init, docs, utils
        """
        ...

    @abstractmethod
    def classify_domain(self, file_path: Path, tree) -> str:
        """Classify a source file by functional domain.

        Returns one of: http, auth, crypto, db, fs, cli, async, parse, net, unknown
        """
        ...

    # -- Dependency / Import Resolution --

    @abstractmethod
    def extract_import_names(self, tree, file_path: Optional[Path] = None) -> List[str]:
        """Extract top-level import/use module names from a parsed tree."""
        ...

    @abstractmethod
    def discover_package_roots(self, repo_path: Path) -> Set[str]:
        """Find top-level internal package/crate roots."""
        ...

    @abstractmethod
    def split_imports(
        self, all_imports: List[str], package_roots: Set[str],
    ) -> Tuple[List[str], List[str]]:
        """Partition imports into (internal, external)."""
        ...

    @abstractmethod
    def build_import_map(
        self, tree, file_path: Path, repo_path: Path,
    ) -> Dict[str, str]:
        """Map locally bound names to their fully qualified origin.

        Used by caller resolution to resolve call names to entity IDs.
        """
        ...


# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, LanguageSupport] = {}


def register_language(lang: LanguageSupport) -> None:
    """Register a language implementation."""
    _REGISTRY[lang.name] = lang


def get_language(name: str) -> Optional[LanguageSupport]:
    """Get a registered language implementation by name."""
    return _REGISTRY.get(name)


def detect_language(repo_path: Path) -> str:
    """Detect the primary language of a repository.

    Heuristic: check for language-specific project files first,
    then count source files by extension.
    """
    # Check for language-specific project files
    if (repo_path / "Cargo.toml").exists():
        return "rust"
    if (repo_path / "setup.py").exists() or (repo_path / "pyproject.toml").exists():
        return "python"

    # Count source files
    counts: Dict[str, int] = {}
    for lang in _REGISTRY.values():
        count = 0
        for ext in lang.extensions:
            count += sum(1 for _ in repo_path.rglob(f"*{ext}"))
        if count > 0:
            counts[lang.name] = count

    if counts:
        return max(counts, key=counts.get)

    return "python"  # default fallback


def get_language_for_repo(repo_path: Path) -> LanguageSupport:
    """Detect language and return the appropriate LanguageSupport.

    Raises ValueError if the detected language has no registered implementation.
    """
    lang_name = detect_language(repo_path)
    lang = get_language(lang_name)
    if lang is None:
        available = ", ".join(sorted(_REGISTRY.keys())) or "none"
        raise ValueError(
            f"No language support registered for '{lang_name}'. "
            f"Available: {available}"
        )
    return lang


def available_languages() -> List[str]:
    """Return names of all registered languages."""
    return sorted(_REGISTRY.keys())
