"""Python language frontend — wraps existing AST-based extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from lang.base import LanguageFrontend, register_frontend


@register_frontend
class PythonFrontend(LanguageFrontend):

    @property
    def name(self) -> str:
        return "python"

    @property
    def extensions(self) -> List[str]:
        return [".py"]

    def discover_package_roots(self, repo_path: Path) -> Set[str]:
        from index.locator import discover_package_roots
        return discover_package_roots(repo_path)

    def parse_bare_entities(self, file_path: Path) -> List[Dict[str, Any]]:
        from index.locator import parse_bare_entities_from_file
        return parse_bare_entities_from_file(file_path)

    def parse_entities(self, file_path: Path) -> List[Dict[str, Any]]:
        from index.locator import parse_entities_from_file
        return parse_entities_from_file(file_path)

    def classify_file(self, file_path: Path, source: Optional[str] = None) -> str:
        from index.locator import parse_ast
        from ir.classifier import classify_file
        tree = parse_ast(file_path)
        if tree is None:
            return "core_logic"
        return classify_file(file_path, tree)

    def classify_domain(self, file_path: Path, source: Optional[str] = None) -> str:
        from index.locator import parse_ast
        from ir.classifier import classify_domain
        tree = parse_ast(file_path)
        if tree is None:
            return "unknown"
        return classify_domain(file_path, tree)

    def extract_imports(self, file_path: Path, source: Optional[str] = None) -> List[str]:
        from index.locator import parse_ast, extract_import_names
        tree = parse_ast(file_path)
        if tree is None:
            return []
        return extract_import_names(tree, file_path)

    def split_imports(
        self, all_imports: List[str], package_roots: Set[str],
    ) -> Tuple[List[str], List[str]]:
        from index.locator import split_imports
        return split_imports(all_imports, package_roots)

    def build_import_map(
        self, file_path: Path, repo_path: Path, source: Optional[str] = None,
    ) -> Dict[str, str]:
        from index.locator import parse_ast
        from index.callers import build_import_map
        tree = parse_ast(file_path)
        if tree is None:
            return {}
        return build_import_map(tree, file_path, repo_path)
