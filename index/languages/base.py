"""Language frontend abstractions for CodeIR."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class ParsedFile:
    """Language-specific parsed source plus raw text for downstream passes."""

    language: str
    file_path: Path
    source: str
    tree: Any
    extras: Dict[str, Any] = field(default_factory=dict)


class LanguageFrontend(Protocol):
    """Interface implemented by each supported language frontend."""

    language: str
    extensions: tuple[str, ...]

    def parse_file(self, file_path: Path) -> Optional[ParsedFile]:
        """Parse a source file, returning None for syntax errors."""

    def parse_bare_entities(self, parsed: ParsedFile) -> List[Dict[str, object]]:
        """Extract names and spans only for fast pass-1 discovery."""

    def parse_entities(self, parsed: ParsedFile) -> List[Dict[str, object]]:
        """Extract full semantic entities for pass-3 indexing."""

    def classify_file(self, file_path: Path, parsed: Optional[ParsedFile]) -> str:
        """Classify a source file into a CodeIR module category."""

    def classify_domain(self, file_path: Path, parsed: Optional[ParsedFile]) -> str:
        """Classify the file domain for bearings and IR tagging."""

    def extract_internal_dependencies(
        self,
        parsed: ParsedFile,
        repo_path: Path,
        all_source_paths: set[str],
    ) -> List[str]:
        """Return repo-internal module dependencies for a source file."""

    def build_import_map(
        self,
        parsed: ParsedFile,
        repo_path: Path,
        entities_by_file: Dict[str, List[Dict[str, object]]],
    ) -> Dict[str, str]:
        """Map locally bound names to likely qualified origins for caller resolution."""
