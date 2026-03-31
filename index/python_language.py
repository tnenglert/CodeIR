"""Python language frontend for CodeIR."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Optional

from ir.classifier import classify_domain as classify_python_domain
from ir.classifier import classify_file as classify_python_file


class _EntityVisitor(ast.NodeVisitor):
    """Collect class/function entities with qualified names."""

    def __init__(self, include_semantic: bool = True) -> None:
        self.entities: List[Dict[str, object]] = []
        self.scope: List[str] = []
        self._include_semantic = include_semantic

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name) and current.id not in ("self", "cls"):
                parts.append(current.id)
            parts.reverse()
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return parts[0] if parts else ""
        return ""

    def _symbol_name(self, node: ast.AST) -> str:
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
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return {"param_types": [], "return_type": None}

        param_types: List[str] = []
        for arg in node.args.args:
            if arg.arg in {"self", "cls"}:
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


class PythonFrontend:
    name = "python"
    extensions = (".py",)

    @property
    def stoplist(self) -> set[str]:
        return {
            "len", "range", "print", "str", "int", "float", "bool", "list", "dict",
            "set", "tuple", "type", "isinstance", "issubclass", "hasattr", "getattr",
            "setattr", "super", "property", "staticmethod", "classmethod", "enumerate",
            "zip", "map", "filter", "sorted", "reversed", "any", "all", "min", "max",
            "abs", "sum", "round", "id", "hash", "repr", "next", "iter", "callable",
            "vars", "dir", "hex", "oct", "bin", "ord", "chr",
            "get", "set", "put", "post", "delete", "update", "pop", "add", "remove",
            "append", "extend", "insert", "clear", "copy", "keys", "values", "items",
            "format", "join", "split", "strip", "replace", "find", "index", "count",
            "read", "write", "close", "open", "flush", "seek",
            "encode", "decode", "lower", "upper", "startswith", "endswith",
            "run", "start", "stop", "init", "setup", "teardown",
        }

    def parse_ast(self, file_path: Path) -> Optional[ast.Module]:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        try:
            return ast.parse(source)
        except SyntaxError:
            return None

    def parse_entities_from_file(self, file_path: Path, include_semantic: bool = True) -> List[Dict[str, object]]:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        visitor = _EntityVisitor(include_semantic=include_semantic)
        visitor.visit(tree)
        return visitor.entities

    def extract_import_names(
        self,
        tree: ast.Module,
        file_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
    ) -> List[str]:
        names: List[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module.split(".")[0])
                elif node.level and node.level > 0 and file_path is not None:
                    parts = file_path.parts
                    if len(parts) > node.level:
                        names.append(parts[-(node.level + 1)])
        return sorted(set(names))

    def discover_internal_roots(self, repo_path: Path) -> set[str]:
        roots: set[str] = set()
        for child in repo_path.iterdir():
            if child.is_dir() and (child / "__init__.py").exists():
                roots.add(child.name)
        return roots

    def split_imports(
        self,
        all_imports: List[str],
        internal_roots: set[str],
        file_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
    ) -> tuple[List[str], List[str]]:
        internal = sorted({n for n in all_imports if n in internal_roots})
        external = sorted({n for n in all_imports if n not in internal_roots})
        return internal, external

    def classify_file(self, file_path: Path, tree: ast.Module) -> str:
        return classify_python_file(file_path, tree)

    def classify_domain(self, file_path: Path, tree: ast.Module) -> str:
        return classify_python_domain(file_path, tree)

    def build_import_map(self, tree: ast.Module, file_path: Path, repo_path: Path) -> Dict[str, str]:
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
