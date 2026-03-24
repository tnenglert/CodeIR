"""File discovery, AST-driven entity extraction, and content hashing."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def discover_source_files(repo_path: Path, extensions: Iterable[str], hidden_dirs: Iterable[str]) -> List[Path]:
    """Return source files for indexing with simple directory exclusions."""
    ext_set = set(extensions)
    hidden = set(hidden_dirs)

    files: List[Path] = []
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in ext_set:
            continue
        if any(part in hidden for part in path.parts):
            continue
        files.append(path)
    return files


def compute_file_content_hash(file_path: Path) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    content = file_path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def parse_ast(file_path: Path) -> Optional[ast.Module]:
    """Parse a Python file into an AST. Returns None on syntax errors."""
    source = file_path.read_text(encoding="utf-8", errors="ignore")
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

class _EntityVisitor(ast.NodeVisitor):
    """Collect class/function entities with qualified names."""

    def __init__(self, include_semantic: bool = True) -> None:
        self.entities: List[Dict[str, object]] = []
        self.scope: List[str] = []
        self._include_semantic = include_semantic

    def _call_name(self, node: ast.AST) -> str:
        """Extract call name, capturing attribute chains for qualified calls.

        Simple calls: foo() → "foo"
        Attribute calls: self.helper.hash() → "helper.hash"
        Strips 'self' and 'cls' prefixes. Returns last 2 segments max.
        """
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            # Build attribute chain by walking up
            parts = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value

            # Add base name if it's not self/cls
            if isinstance(current, ast.Name) and current.id not in ("self", "cls"):
                parts.append(current.id)

            parts.reverse()

            # Return last 2 segments for qualified calls
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return parts[0] if parts else ""
        return ""

    def _symbol_name(self, node: ast.AST) -> str:
        """Best-effort symbol extraction for inheritance/annotation references."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript):
            return self._symbol_name(node.value)
        if isinstance(node, ast.Call):
            return self._symbol_name(node.func)
        return ""

    @staticmethod
    def _looks_like_exception(name: str) -> bool:
        lower = name.lower()
        return (
            lower in {"exception", "baseexception"}
            or lower.endswith("error")
            or lower.endswith("exception")
            or lower.endswith("timeout")
        )

    def _semantic_summary(self, node: ast.AST) -> Dict[str, object]:
        calls: set[str] = set()
        bases: set[str] = set()
        flags: set[str] = set()
        assign_count = 0

        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call = self._call_name(child.func)
                if call:
                    calls.add(call)
            elif isinstance(child, ast.If):
                flags.add("I")
            elif isinstance(child, (ast.For, ast.While, ast.AsyncFor)):
                flags.add("L")
            elif isinstance(child, ast.Try):
                flags.add("T")
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                flags.add("W")
            elif isinstance(child, ast.Await):
                flags.add("A")
            elif isinstance(child, ast.Return):
                flags.add("R")
            elif isinstance(child, ast.Raise):
                flags.add("E")
            elif isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                assign_count += 1

        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = self._symbol_name(base)
                if base_name:
                    bases.add(base_name)
                    # Treat base classes as semantic references for class role inference.
                    calls.add(base_name)
            if (
                self._looks_like_exception(node.name)
                or any(self._looks_like_exception(base) for base in bases)
            ):
                flags.add("X")

        return {
            "calls": sorted(calls),
            "flags": "".join(sorted(flags)),
            "assigns": assign_count,
            "bases": sorted(bases),
        }

    def _extract_type_signature(self, node: ast.AST) -> Dict[str, object]:
        """Extract parameter type annotations and return type from a function node."""
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return {"param_types": [], "return_type": None}

        param_types: List[str] = []
        for arg in node.args.args:
            if arg.arg == "self" or arg.arg == "cls":
                continue
            if arg.annotation:
                try:
                    param_types.append(ast.unparse(arg.annotation))
                except Exception:
                    param_types.append("?")
            else:
                param_types.append("?")

        return_type = None
        if node.returns:
            try:
                return_type = ast.unparse(node.returns)
            except Exception:
                return_type = "?"

        return {"param_types": param_types, "return_type": return_type}

    def _append(self, node: ast.AST, kind: str, name: str) -> None:
        start_line = int(getattr(node, "lineno", 0) or 0)
        end_line = int(getattr(node, "end_lineno", start_line) or start_line)
        qualified_name = ".".join([*self.scope, name]) if self.scope else name
        entry: Dict[str, object] = {
            "kind": kind,
            "name": name,
            "qualified_name": qualified_name,
            "start_line": start_line,
            "end_line": end_line,
        }
        if self._include_semantic:
            entry["semantic"] = self._semantic_summary(node)
            entry["semantic"]["type_sig"] = self._extract_type_signature(node)
        self.entities.append(entry)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._append(node, "class", node.name)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        kind = "method" if self.scope else "function"
        self._append(node, kind, node.name)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        kind = "async_method" if self.scope else "async_function"
        self._append(node, kind, node.name)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def parse_entities_from_file(file_path: Path) -> List[Dict[str, object]]:
    """Extract entity metadata with full semantic analysis from a Python file."""
    source = file_path.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    visitor = _EntityVisitor(include_semantic=True)
    visitor.visit(tree)
    return visitor.entities


def parse_bare_entities_from_file(file_path: Path) -> List[Dict[str, object]]:
    """Extract entity metadata (names/spans only, no semantic analysis) from a Python file.

    Used in Pass 1 of the multi-pass pipeline for fast entity discovery.
    """
    source = file_path.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    visitor = _EntityVisitor(include_semantic=False)
    visitor.visit(tree)
    return visitor.entities


def extract_import_names(tree: ast.Module, file_path: Optional[Path] = None) -> List[str]:
    """Extract top-level import module names from an AST.

    Returns the root module name for each import (e.g., 'os' from 'from os.path import join').
    Handles relative imports when file_path is provided.
    """
    names: List[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module.split(".")[0])
            elif node.level and node.level > 0 and file_path is not None:
                # Relative import (from . import X): derive package root from file path
                parts = file_path.parts
                if len(parts) > node.level:
                    names.append(parts[-(node.level + 1)])
    return sorted(set(names))


def discover_package_roots(repo_path: Path) -> set:
    """Find top-level directories containing __init__.py — these are internal packages."""
    roots: set = set()
    for child in repo_path.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            roots.add(child.name)
    return roots


def split_imports(
    all_imports: List[str], package_roots: set,
) -> tuple:
    """Partition imports into (internal, external) based on known package roots.

    Internal: root module name appears in package_roots.
    External: everything else (stdlib + third-party).
    """
    internal = sorted({n for n in all_imports if n in package_roots})
    external = sorted({n for n in all_imports if n not in package_roots})
    return internal, external


def extract_code_slice(repo_path: Path, file_path: str, start_line: int, end_line: int) -> str:
    """Return an exact inclusive line slice from a repository file."""
    abs_path = (repo_path / file_path).resolve()
    lines = abs_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)

    if start_line <= 0:
        start_line = 1
    if end_line < start_line:
        end_line = start_line

    return "".join(lines[start_line - 1 : end_line])
