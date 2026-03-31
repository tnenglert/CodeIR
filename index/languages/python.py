"""Python frontend implementation for CodeIR."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Optional

from index.languages.base import LanguageFrontend, ParsedFile
from index.locator import extract_import_names, parse_bare_entities_from_file, parse_entities_from_file, split_imports
from ir.classifier import classify_domain as classify_python_domain
from ir.classifier import classify_file as classify_python_file


def _discover_package_roots(repo_path: Path, all_source_paths: set[str]) -> set[str]:
    roots: set[str] = set()
    for rel_path in all_source_paths:
        path = Path(rel_path)
        if path.name == "__init__.py" and path.parent != Path("."):
            roots.add(path.parent.parts[0])
    return roots


def _build_import_map(
    tree: ast.Module,
    file_path: Path,
    repo_path: Path,
) -> Dict[str, str]:
    import_map: Dict[str, str] = {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname if alias.asname else alias.name.split(".")[0]
                import_map[local_name] = alias.name

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""

            if node.level and node.level > 0:
                try:
                    rel_path = file_path.relative_to(repo_path)
                except ValueError:
                    rel_path = file_path
                parts = list(rel_path.parent.parts)
                if node.level <= len(parts):
                    base = ".".join(parts[: len(parts) - node.level + 1])
                else:
                    base = ""
                if module:
                    module = f"{base}.{module}" if base else module
                else:
                    module = base

            for alias in node.names:
                local_name = alias.asname if alias.asname else alias.name
                qualified = f"{module}.{alias.name}" if module else alias.name
                import_map[local_name] = qualified

    return import_map


class PythonFrontend(LanguageFrontend):
    language = "python"
    extensions = (".py",)

    def parse_file(self, file_path: Path) -> Optional[ParsedFile]:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        return ParsedFile(language=self.language, file_path=file_path, source=source, tree=tree)

    def parse_bare_entities(self, parsed: ParsedFile) -> List[Dict[str, object]]:
        return parse_bare_entities_from_file(parsed.file_path)

    def parse_entities(self, parsed: ParsedFile) -> List[Dict[str, object]]:
        return parse_entities_from_file(parsed.file_path)

    def classify_file(self, file_path: Path, parsed: Optional[ParsedFile]) -> str:
        if parsed is None:
            return "core_logic"
        return classify_python_file(file_path, parsed.tree)

    def classify_domain(self, file_path: Path, parsed: Optional[ParsedFile]) -> str:
        if parsed is None:
            return "unknown"
        return classify_python_domain(file_path, parsed.tree)

    def extract_internal_dependencies(
        self,
        parsed: ParsedFile,
        repo_path: Path,
        all_source_paths: set[str],
    ) -> List[str]:
        package_roots = _discover_package_roots(repo_path, all_source_paths)
        all_imports = extract_import_names(parsed.tree, parsed.file_path)
        internal, _ = split_imports(all_imports, package_roots)
        return internal

    def build_import_map(
        self,
        parsed: ParsedFile,
        repo_path: Path,
        entities_by_file: Dict[str, List[Dict[str, object]]],
    ) -> Dict[str, str]:
        return _build_import_map(parsed.tree, parsed.file_path, repo_path)
