"""TypeScript language frontend for CodeIR."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tree_sitter import Node
from tree_sitter_language_pack import get_parser


_TS_ROUTER_WORDS = {"router", "route", "routes"}
_TS_SCHEMA_DIRS = {"schemas", "schema", "models", "types", "interfaces"}
_TS_CONFIG_FILES = {"vite.config.ts", "vitest.config.ts", "jest.config.ts", "next.config.ts"}
_TS_CONSTANT_FILES = {"constants.ts", "const.ts", "consts.ts"}
_TS_EXCEPTION_FILES = {"errors.ts", "error.ts", "exceptions.ts"}
_TS_DOC_FILES = {"*.d.ts"}
_TS_TEST_SUFFIXES = (".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")
_TS_DOMAIN_PATTERNS = {
    "auth": "auth",
    "token": "auth",
    "jwt": "auth",
    "http": "http",
    "api": "http",
    "client": "http",
    "router": "http",
    "db": "db",
    "database": "db",
    "query": "db",
    "sql": "db",
    "fs": "fs",
    "file": "fs",
    "cli": "cli",
    "command": "cli",
    "parse": "parse",
    "parser": "parse",
    "json": "parse",
    "xml": "parse",
    "async": "async",
    "queue": "async",
    "socket": "net",
    "net": "net",
    "crypto": "crypto",
    "hash": "crypto",
}
_TS_DOMAIN_IMPORTS = {
    "express": "http",
    "fastify": "http",
    "next": "http",
    "axios": "http",
    "undici": "http",
    "jsonwebtoken": "auth",
    "bcrypt": "auth",
    "crypto": "crypto",
    "typeorm": "db",
    "prisma": "db",
    "mongoose": "db",
    "fs": "fs",
    "path": "fs",
    "commander": "cli",
    "yargs": "cli",
    "zod": "parse",
    "yaml": "parse",
    "ws": "net",
}


@dataclass
class _ExportedNode:
    node: Node
    start_line: int
    end_line: int


class TypeScriptFrontend:
    name = "typescript"
    extensions = (".ts", ".tsx")

    def __init__(self) -> None:
        self._ts_parser = get_parser("typescript")
        self._tsx_parser = get_parser("tsx")

    @property
    def stoplist(self) -> set[str]:
        return {
            "map", "filter", "reduce", "forEach", "find", "some", "every", "sort",
            "push", "pop", "shift", "unshift", "slice", "splice", "concat", "join",
            "split", "trim", "includes", "startsWith", "endsWith", "toString",
            "parseInt", "parseFloat", "Number", "String", "Boolean", "Array", "Object",
            "Promise", "setTimeout", "clearTimeout", "setInterval", "clearInterval",
            "log", "warn", "error",
        }

    def parse_ast(self, file_path: Path):
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        parser = self._tsx_parser if file_path.suffix.lower() == ".tsx" else self._ts_parser
        tree = parser.parse(source.encode("utf-8"))
        return {"tree": tree, "source": source}

    def parse_entities_from_file(self, file_path: Path, include_semantic: bool = True) -> List[Dict[str, object]]:
        parsed = self.parse_ast(file_path)
        source = parsed["source"]
        root = parsed["tree"].root_node
        extractor = _TypeScriptEntityExtractor(source=source, include_semantic=include_semantic)
        return extractor.extract(root)

    def extract_import_names(
        self,
        tree: Dict[str, object],
        file_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
    ) -> List[str]:
        names: set[str] = set()
        root = tree["tree"].root_node
        for node in root.named_children:
            if node.type == "import_statement":
                module_name = self._module_name(node)
                if module_name:
                    names.add(module_name)
            elif node.type == "export_statement":
                module_name = self._module_name(node)
                if module_name:
                    names.add(module_name)
        return sorted(names)

    def discover_internal_roots(self, repo_path: Path) -> set[str]:
        roots: set[str] = set()
        for child in repo_path.iterdir():
            if not child.is_dir():
                continue
            if any(path.suffix.lower() in self.extensions for path in child.rglob("*") if path.is_file()):
                roots.add(child.name)
        return roots

    def split_imports(
        self,
        all_imports: Sequence[str],
        internal_roots: set[str],
        file_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
    ) -> tuple[List[str], List[str]]:
        internal: set[str] = set()
        external: set[str] = set()
        for spec in all_imports:
            resolved = self._resolve_dependency(spec, file_path=file_path, repo_path=repo_path, internal_roots=internal_roots)
            if resolved is None:
                external.add(spec)
            else:
                internal.add(resolved)
        return sorted(internal), sorted(external)

    def classify_file(self, file_path: Path, tree: Dict[str, object]) -> str:
        lower_name = file_path.name.lower()
        lower_parts = [part.lower() for part in file_path.parts]

        if any(lower_name.endswith(suffix) for suffix in _TS_TEST_SUFFIXES):
            return "tests"
        if lower_name == "index.ts" or lower_name == "index.tsx":
            if any(part in {"config", "configs"} for part in lower_parts):
                return "config"
            return "init"
        if lower_name in _TS_CONFIG_FILES:
            return "config"
        if lower_name in _TS_CONSTANT_FILES:
            return "constants"
        if lower_name in _TS_EXCEPTION_FILES:
            return "exceptions"
        if file_path.suffix.lower() == ".d.ts":
            return "docs"
        if any(part in {"tests", "test", "__tests__"} for part in lower_parts):
            return "tests"
        if any(part in {"config", "configs"} for part in lower_parts):
            return "config"
        if any(part in _TS_SCHEMA_DIRS for part in lower_parts):
            return "schema"
        if any(part in _TS_ROUTER_WORDS for part in lower_parts):
            return "router"
        if any(part in {"utils", "helpers", "shared"} for part in lower_parts):
            return "utils"

        root = tree["tree"].root_node
        stats = _TypeScriptClassificationStats.from_root(root)
        if stats.router_signals >= 2:
            return "router"
        if stats.schema_signals >= 2:
            return "schema"
        if stats.exception_signals > 0 and stats.class_count == stats.exception_signals and stats.function_count == 0:
            return "exceptions"
        if stats.constant_count >= 3 and stats.def_count == 0:
            return "constants"
        if stats.def_count == 0:
            return "constants" if stats.constant_count > 0 else "docs"
        if stats.def_count <= 3 and stats.constant_count == 0:
            return "utils"
        return "core_logic"

    def classify_domain(self, file_path: Path, tree: Dict[str, object]) -> str:
        parts = [file_path.stem.lower(), *[part.lower() for part in file_path.parts]]
        for part in parts:
            if part in _TS_DOMAIN_PATTERNS:
                return _TS_DOMAIN_PATTERNS[part]

        scores: Dict[str, int] = {}
        for spec in self.extract_import_names(tree):
            root = spec.split("/", 1)[0]
            if root in _TS_DOMAIN_IMPORTS:
                domain = _TS_DOMAIN_IMPORTS[root]
                scores[domain] = scores.get(domain, 0) + 1
        if scores:
            return max(scores, key=scores.get)
        return "unknown"

    def build_import_map(self, tree: Dict[str, object], file_path: Path, repo_path: Path) -> Dict[str, str]:
        import_map: Dict[str, str] = {}
        internal_roots = self.discover_internal_roots(repo_path)
        root = tree["tree"].root_node

        for node in root.named_children:
            if node.type != "import_statement":
                continue
            module_spec = self._module_name(node)
            if not module_spec:
                continue
            resolved_module = self._resolve_dependency(
                module_spec,
                file_path=file_path,
                repo_path=repo_path,
                internal_roots=internal_roots,
            ) or module_spec

            clause = next((child for child in node.named_children if child.type == "import_clause"), None)
            if clause is None:
                continue
            named_children = clause.named_children
            if not named_children:
                continue

            first = named_children[0]
            if first.type == "identifier":
                import_map[first.text.decode("utf-8")] = resolved_module

            for child in named_children:
                if child.type == "namespace_import":
                    ident = child.child_by_field_name("name") or next((c for c in child.named_children if c.type == "identifier"), None)
                    if ident is not None:
                        import_map[ident.text.decode("utf-8")] = resolved_module
                elif child.type == "named_imports":
                    for spec in child.named_children:
                        if spec.type != "import_specifier":
                            continue
                        name_node = spec.child_by_field_name("name")
                        alias_node = spec.child_by_field_name("alias")
                        imported_name = name_node.text.decode("utf-8") if name_node is not None else spec.text.decode("utf-8")
                        local_name = alias_node.text.decode("utf-8") if alias_node is not None else imported_name
                        import_map[local_name] = f"{resolved_module}.{imported_name}"

        return import_map

    @staticmethod
    def _module_name(node: Node) -> Optional[str]:
        string_node = next((child for child in node.named_children if child.type == "string"), None)
        if string_node is None:
            return None
        text = string_node.text.decode("utf-8")
        return text[1:-1] if len(text) >= 2 and text[0] in {"'", '"'} else text

    def _resolve_dependency(
        self,
        spec: str,
        file_path: Optional[Path],
        repo_path: Optional[Path],
        internal_roots: set[str],
    ) -> Optional[str]:
        if repo_path is None:
            return spec if spec.split("/", 1)[0] in internal_roots else None

        def candidate_rel(target: Path) -> Optional[str]:
            if not target.exists():
                return None
            try:
                rel = target.relative_to(repo_path).as_posix()
            except ValueError:
                return None
            if rel.endswith("/index.ts"):
                rel = rel[: -len("/index.ts")]
            elif rel.endswith("/index.tsx"):
                rel = rel[: -len("/index.tsx")]
            elif rel.endswith(".ts"):
                rel = rel[:-3]
            elif rel.endswith(".tsx"):
                rel = rel[:-4]
            return rel

        if spec.startswith("."):
            if file_path is None:
                return spec
            base = (file_path.parent / spec).resolve()
            for candidate in (
                base.with_suffix(".ts"),
                base.with_suffix(".tsx"),
                base / "index.ts",
                base / "index.tsx",
            ):
                rel = candidate_rel(candidate)
                if rel:
                    return rel
            return spec

        root = spec.split("/", 1)[0]
        if root not in internal_roots:
            return None

        if repo_path is not None:
            base = (repo_path / spec).resolve()
            for candidate in (
                base.with_suffix(".ts"),
                base.with_suffix(".tsx"),
                base / "index.ts",
                base / "index.tsx",
            ):
                rel = candidate_rel(candidate)
                if rel:
                    return rel

        return spec


class _TypeScriptClassificationStats:
    def __init__(self) -> None:
        self.function_count = 0
        self.class_count = 0
        self.interface_count = 0
        self.type_count = 0
        self.enum_count = 0
        self.namespace_count = 0
        self.constant_count = 0
        self.router_signals = 0
        self.schema_signals = 0
        self.exception_signals = 0

    @property
    def def_count(self) -> int:
        return (
            self.function_count + self.class_count + self.interface_count
            + self.type_count + self.enum_count + self.namespace_count
        )

    @classmethod
    def from_root(cls, root: Node) -> "_TypeScriptClassificationStats":
        stats = cls()
        for node in root.named_children:
            target = _unwrap_export(node)
            if target is None:
                continue
            if target.type == "function_declaration":
                stats.function_count += 1
            elif target.type == "lexical_declaration":
                for declarator in target.named_children:
                    if declarator.type != "variable_declarator":
                        continue
                    value = declarator.child_by_field_name("value")
                    if value is not None and value.type == "arrow_function":
                        stats.function_count += 1
                    else:
                        stats.constant_count += 1
            elif target.type == "class_declaration":
                stats.class_count += 1
                name = _node_text(target.child_by_field_name("name")).lower()
                if name.endswith("error") or name.endswith("exception"):
                    stats.exception_signals += 1
            elif target.type == "interface_declaration":
                stats.interface_count += 1
                stats.schema_signals += 1
            elif target.type == "type_alias_declaration":
                stats.type_count += 1
                stats.schema_signals += 1
            elif target.type == "enum_declaration":
                stats.enum_count += 1
            elif target.type == "internal_module":
                stats.namespace_count += 1

            text = target.text.decode("utf-8").lower()
            if any(word in text for word in ("router", "route", "get(", "post(", "put(", "delete(")):
                stats.router_signals += 1

        return stats


class _TypeScriptEntityExtractor:
    def __init__(self, source: str, include_semantic: bool) -> None:
        self.source = source
        self.include_semantic = include_semantic
        self.entities: List[Dict[str, object]] = []
        self.scope: List[str] = []

    def extract(self, root: Node) -> List[Dict[str, object]]:
        for node in root.named_children:
            self._visit_top_level(node)
        return self.entities

    def _visit_top_level(self, node: Node) -> None:
        target = _unwrap_export(node)
        if target is None:
            return
        span = _ExportedNode(
            node=target,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
        )
        self._emit_entity(target, span)

    def _emit_entity(self, node: Node, span: _ExportedNode) -> None:
        if node.type == "function_declaration":
            name = _node_text(node.child_by_field_name("name"))
            kind = "async_function" if _has_child_type(node, "async") else "function"
            self._append(node, span, kind, name)
            return
        if node.type == "class_declaration":
            name = _node_text(node.child_by_field_name("name"))
            self._append(node, span, "class", name)
            self.scope.append(name)
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.named_children:
                    self._emit_class_member(child)
            self.scope.pop()
            return
        if node.type == "interface_declaration":
            self._append(node, span, "interface", _node_text(node.child_by_field_name("name")))
            return
        if node.type == "type_alias_declaration":
            self._append(node, span, "type_alias", _node_text(node.child_by_field_name("name")))
            return
        if node.type == "enum_declaration":
            self._append(node, span, "enum", _node_text(node.child_by_field_name("name")))
            return
        if node.type == "internal_module":
            name = _node_text(node.child_by_field_name("name"))
            self._append(node, span, "namespace", name)
            self.scope.append(name)
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.named_children:
                    nested_span = _ExportedNode(
                        node=child,
                        start_line=child.start_point.row + 1,
                        end_line=child.end_point.row + 1,
                    )
                    self._emit_entity(_unwrap_export(child) or child, nested_span)
            self.scope.pop()
            return
        if node.type == "lexical_declaration":
            for declarator in node.named_children:
                if declarator.type != "variable_declarator":
                    continue
                self._emit_variable_declarator(declarator, span)

    def _emit_class_member(self, node: Node) -> None:
        if node.type == "method_definition":
            name = _node_text(node.child_by_field_name("name"))
            kind = "async_method" if _has_child_type(node, "async") else "method"
            span = _ExportedNode(node=node, start_line=node.start_point.row + 1, end_line=node.end_point.row + 1)
            self._append(node, span, kind, name)
        elif node.type == "public_field_definition":
            value = node.child_by_field_name("value")
            if value is not None and value.type == "arrow_function":
                name = _node_text(node.child_by_field_name("name"))
                kind = "async_method" if _has_child_type(value, "async") else "method"
                span = _ExportedNode(node=node, start_line=node.start_point.row + 1, end_line=node.end_point.row + 1)
                self._append(value, span, kind, name)

    def _emit_variable_declarator(self, declarator: Node, span: _ExportedNode) -> None:
        name_node = declarator.child_by_field_name("name")
        value_node = declarator.child_by_field_name("value")
        if name_node is None:
            return
        name = _node_text(name_node)
        if value_node is not None and value_node.type == "arrow_function":
            kind = "async_function" if _has_child_type(value_node, "async") else "function"
            self._append(value_node, span, kind, name)
        else:
            self._append(declarator, span, "constant", name)

    def _append(self, node: Node, span: _ExportedNode, kind: str, name: str) -> None:
        if not name:
            return
        qualified_name = ".".join([*self.scope, name]) if self.scope else name
        entry: Dict[str, object] = {
            "kind": kind,
            "name": name,
            "qualified_name": qualified_name,
            "start_line": span.start_line,
            "end_line": span.end_line,
        }
        if self.include_semantic:
            entry["semantic"] = self._semantic_summary(node, kind)
        self.entities.append(entry)

    def _semantic_summary(self, node: Node, kind: str) -> Dict[str, object]:
        calls: set[str] = set()
        bases: set[str] = set()
        flags: set[str] = set()
        assign_count = 0

        for child in _walk(node):
            if child.type in {"call_expression", "new_expression"}:
                target = child.child_by_field_name("function") or child.child_by_field_name("constructor")
                call_name = _call_name(target)
                if call_name:
                    calls.add(call_name)
            elif child.type in {"if_statement", "switch_statement", "ternary_expression"}:
                flags.add("I")
            elif child.type in {"for_statement", "for_in_statement", "for_of_statement", "while_statement", "do_statement"}:
                flags.add("L")
            elif child.type == "try_statement":
                flags.add("T")
            elif child.type == "with_statement":
                flags.add("W")
            elif child.type == "await_expression":
                flags.add("A")
            elif child.type == "return_statement":
                flags.add("R")
            elif child.type == "throw_statement":
                flags.add("E")
            elif child.type in {"assignment_expression", "augmented_assignment_expression", "update_expression", "variable_declarator"}:
                assign_count += 1

        if kind == "class":
            heritage = node.child_by_field_name("body")
            for child in node.named_children:
                if child.type == "class_heritage":
                    for sub in _walk(child):
                        if sub.type in {"identifier", "type_identifier", "member_expression"}:
                            base_name = _call_name(sub)
                            if base_name:
                                bases.add(base_name)
                    break
        elif kind == "interface":
            for child in node.named_children:
                if child.type == "extends_type_clause":
                    for sub in _walk(child):
                        if sub.type in {"identifier", "type_identifier"}:
                            bases.add(_node_text(sub))

        type_sig = self._extract_type_signature(node)
        return {
            "calls": sorted(calls),
            "flags": "".join(sorted(flags)),
            "assigns": assign_count,
            "bases": sorted(bases),
            "type_sig": type_sig,
        }

    def _extract_type_signature(self, node: Node) -> Dict[str, object]:
        params_node = node.child_by_field_name("parameters")
        param_types: List[str] = []
        if params_node is not None:
            for child in params_node.named_children:
                pattern = child.child_by_field_name("pattern")
                if pattern is None:
                    continue
                name = _node_text(pattern).lstrip("...")
                if name in {"this", "self", "cls"}:
                    continue
                type_node = child.child_by_field_name("type")
                param_types.append(_node_text(type_node)[1:].strip() if type_node is not None else "?")

        return_node = node.child_by_field_name("return_type")
        if return_node is None:
            return_node = next(
                (child for child in node.named_children if child.type == "type_annotation"),
                None,
            )
        return_type = None
        if return_node is not None:
            return_type = _node_text(return_node)[1:].strip() or None

        return {"param_types": param_types, "return_type": return_type}


def _unwrap_export(node: Node) -> Optional[Node]:
    if node.type != "export_statement":
        return node
    for child in node.named_children:
        if child.type != "export_clause" and child.type != "string":
            return child
    return None


def _walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _node_text(node: Optional[Node]) -> str:
    if node is None:
        return ""
    return node.text.decode("utf-8")


def _has_child_type(node: Node, child_type: str) -> bool:
    return any(child.type == child_type for child in node.children)


def _call_name(node: Optional[Node]) -> str:
    if node is None:
        return ""
    if node.type in {"identifier", "property_identifier", "type_identifier"}:
        return _node_text(node)
    if node.type in {"member_expression", "subscript_expression"}:
        parts: List[str] = []
        current = node
        while current is not None and current.type == "member_expression":
            prop = current.child_by_field_name("property")
            if prop is not None:
                parts.append(_node_text(prop))
            current = current.child_by_field_name("object")
        if current is not None and current.type in {"identifier", "this", "super"}:
            base = _node_text(current)
            if base not in {"this", "super"}:
                parts.append(base)
        parts.reverse()
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return parts[0] if parts else ""
    return ""
