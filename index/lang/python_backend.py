"""Python language backend — wraps existing locator/classifier/callers logic."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from index.locator import (
    discover_package_roots,
    extract_import_names,
    parse_ast,
    parse_bare_entities_from_file,
    parse_entities_from_file,
    split_imports,
)
from ir.classifier import classify_domain, classify_file
from index.callers import build_import_map, CALL_STOPLIST


class PythonBackend:
    language: str = "python"
    extensions: List[str] = [".py"]

    def parse_file(self, path: Path) -> Any:
        return parse_ast(path)

    def extract_entities(self, path: Path, include_semantic: bool = True) -> List[dict]:
        if include_semantic:
            return parse_entities_from_file(path)
        return parse_bare_entities_from_file(path)

    def extract_imports(self, tree: Any, file_path: Optional[Path] = None) -> List[str]:
        return extract_import_names(tree, file_path)

    def classify_file(self, file_path: Path, tree: Any) -> str:
        return classify_file(file_path, tree)

    def classify_domain(self, file_path: Path, tree: Any) -> str:
        return classify_domain(file_path, tree)

    def discover_package_roots(self, repo_path: Path) -> Set[str]:
        return discover_package_roots(repo_path)

    def split_imports(self, all_imports: List[str], package_roots: Set[str]) -> Tuple[List[str], List[str]]:
        return split_imports(all_imports, package_roots)

    def build_import_map(self, tree: Any, file_path: Path, repo_path: Path) -> Dict[str, str]:
        return build_import_map(tree, file_path, repo_path)

    def get_call_stoplist(self) -> Set[str]:
        return CALL_STOPLIST
