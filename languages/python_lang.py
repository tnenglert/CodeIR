"""Python language support for CodeIR.

Wraps existing Python-specific modules (locator, classifier, callers)
behind the LanguageSupport interface.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from languages import LanguageSupport, register_language
from index.locator import (
    discover_package_roots,
    extract_import_names,
    parse_bare_entities_from_file,
    parse_entities_from_file,
    split_imports,
)
from index.locator import parse_ast as _parse_ast
from ir.classifier import classify_file as _classify_file
from ir.classifier import classify_domain as _classify_domain
from index.callers import build_import_map as _build_import_map
from index.callers import CALL_STOPLIST


class PythonLanguage(LanguageSupport):

    @property
    def name(self) -> str:
        return "python"

    @property
    def extensions(self) -> List[str]:
        return [".py"]

    @property
    def call_stoplist(self) -> Set[str]:
        return CALL_STOPLIST

    def parse_entities(self, file_path: Path, include_semantic: bool = True) -> List[dict]:
        if include_semantic:
            return parse_entities_from_file(file_path)
        return parse_bare_entities_from_file(file_path)

    def parse_ast(self, file_path: Path):
        return _parse_ast(file_path)

    def classify_file(self, file_path: Path, tree) -> str:
        return _classify_file(file_path, tree)

    def classify_domain(self, file_path: Path, tree) -> str:
        return _classify_domain(file_path, tree)

    def extract_import_names(self, tree, file_path: Optional[Path] = None) -> List[str]:
        return extract_import_names(tree, file_path)

    def discover_package_roots(self, repo_path: Path) -> Set[str]:
        return discover_package_roots(repo_path)

    def split_imports(
        self, all_imports: List[str], package_roots: Set[str],
    ) -> Tuple[List[str], List[str]]:
        return split_imports(all_imports, package_roots)

    def build_import_map(
        self, tree, file_path: Path, repo_path: Path,
    ) -> Dict[str, str]:
        return _build_import_map(tree, file_path, repo_path)


# Auto-register on import
_instance = PythonLanguage()
register_language(_instance)
