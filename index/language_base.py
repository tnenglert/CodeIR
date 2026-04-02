"""Language frontend protocol for CodeIR.

Defines the structural interface that all language frontends must satisfy.
Uses ``typing.Protocol`` for static checking without requiring inheritance.
Concrete frontends (PythonFrontend, RustFrontend, etc.) are validated by
mypy against this protocol — they do not need to subclass it at runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, runtime_checkable

from typing import Protocol


@runtime_checkable
class LanguageFrontend(Protocol):
    """Structural interface for language-specific indexing frontends.

    Every frontend must expose at minimum:
    - ``name``: language identifier (e.g. ``"python"``, ``"rust"``)
    - ``extensions``: file extensions this frontend handles
    - parsing, classification, import resolution, and entity extraction methods

    The ``@runtime_checkable`` decorator allows ``isinstance()`` checks at
    registration time if desired, but the primary enforcement path is mypy.
    """

    name: str
    extensions: Tuple[str, ...]
    stoplist: set[str]

    def matches_path(self, file_path: Path) -> bool:
        """Return True if this frontend handles the given file."""
        ...

    def parse_ast(self, file_path: Path) -> Any:
        """Parse a source file into a language-specific tree representation."""
        ...

    def parse_entities_from_file(
        self,
        file_path: Path,
        include_semantic: bool = True,
        tree: Any = None,
    ) -> List[Dict[str, object]]:
        """Extract entity metadata from a source file.

        When ``include_semantic`` is False, returns only names and spans
        (used in Pass 1 for fast entity discovery).

        If ``tree`` is provided (from a prior ``parse_ast`` call), it is
        reused instead of re-parsing the file.
        """
        ...

    def extract_import_names(
        self,
        tree: Any,
        file_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
    ) -> List[str]:
        """Extract top-level import/dependency names from a parsed tree."""
        ...

    def discover_internal_roots(self, repo_path: Path) -> set[str]:
        """Find internal package/crate roots for import resolution."""
        ...

    def split_imports(
        self,
        all_imports: Sequence[str],
        internal_roots: set[str],
        file_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
    ) -> Tuple[List[str], List[str]]:
        """Partition imports into (internal, external) based on known roots."""
        ...

    def classify_file(self, file_path: Path, tree: Any) -> str:
        """Assign a category (core_logic, tests, config, etc.) to a file."""
        ...

    def classify_domain(self, file_path: Path, tree: Any) -> str:
        """Assign a domain tag to a file based on content heuristics."""
        ...

    def build_import_map(
        self, tree: Any, file_path: Path, repo_path: Path,
    ) -> Dict[str, str]:
        """Map locally bound names to their fully qualified origin."""
        ...


# ---------------------------------------------------------------------------
# Shared utilities for frontend implementations
# ---------------------------------------------------------------------------

def default_split_imports(
    all_imports: Sequence[str],
    internal_roots: set[str],
) -> Tuple[List[str], List[str]]:
    """Partition imports into (internal, external) based on known roots.

    This is the standard implementation shared by all frontends.
    """
    internal = sorted({name for name in all_imports if name in internal_roots})
    external = sorted({name for name in all_imports if name not in internal_roots})
    return internal, external
