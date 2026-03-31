"""TypeScript frontend implementation for CodeIR."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from index.languages.base import LanguageFrontend, ParsedFile


TS_CATEGORIES = (
    "core_logic",
    "router",
    "schema",
    "config",
    "compat",
    "exceptions",
    "constants",
    "tests",
    "init",
    "docs",
    "utils",
)

TS_DOMAINS = (
    "http",
    "auth",
    "crypto",
    "db",
    "fs",
    "cli",
    "async",
    "parse",
    "net",
    "unknown",
)

TS_FILENAME_CATEGORY_MAP = {
    "index.ts": "init",
    "index.tsx": "init",
    "route.ts": "router",
    "route.tsx": "router",
    "config.ts": "config",
    "config.tsx": "config",
    "settings.ts": "config",
    "constants.ts": "constants",
    "consts.ts": "constants",
    "errors.ts": "exceptions",
    "exceptions.ts": "exceptions",
}

TS_DIR_CATEGORY_MAP = {
    "tests": "tests",
    "test": "tests",
    "__tests__": "tests",
    "schemas": "schema",
    "types": "schema",
    "models": "schema",
    "config": "config",
}

TS_DOMAIN_FILE_PATTERNS = {
    "http": "http",
    "api": "http",
    "client": "http",
    "auth": "auth",
    "login": "auth",
    "token": "auth",
    "crypto": "crypto",
    "hash": "crypto",
    "db": "db",
    "database": "db",
    "sql": "db",
    "fs": "fs",
    "files": "fs",
    "cli": "cli",
    "command": "cli",
    "commands": "cli",
    "parser": "parse",
    "json": "parse",
    "yaml": "parse",
}

TS_DOMAIN_IMPORTS = {
    "express": "http",
    "fastify": "http",
    "axios": "http",
    "node-fetch": "http",
    "jsonwebtoken": "auth",
    "bcrypt": "auth",
    "bcryptjs": "auth",
    "crypto": "crypto",
    "prisma": "db",
    "typeorm": "db",
    "mongoose": "db",
    "fs": "fs",
    "fs/promises": "fs",
    "path": "fs",
    "commander": "cli",
    "yargs": "cli",
    "zod": "parse",
    "yaml": "parse",
    "net": "net",
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _text(source: bytes, node: Optional[Node]) -> str:
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def _child(node: Node, field_name: str) -> Optional[Node]:
    try:
        return node.child_by_field_name(field_name)
    except Exception:
        return None


def _named_children(node: Optional[Node], node_type: Optional[str] = None) -> List[Node]:
    if node is None:
        return []
    out = []
    for child in node.named_children:
        if node_type is None or child.type == node_type:
            out.append(child)
    return out


def _identifier_text(source: bytes, node: Optional[Node]) -> str:
    if node is None:
        return ""
    if node.type in {"identifier", "property_identifier", "type_identifier", "nested_identifier"}:
        return _text(source, node)
    for child in node.named_children:
        value = _identifier_text(source, child)
        if value:
            return value
    return ""


def _type_text(source: bytes, node: Optional[Node]) -> str:
    if node is None:
        return ""
    if node.type == "type_annotation":
        raw = _text(source, node)
        return raw[1:].strip() if raw.startswith(":") else raw.strip()
    return _text(source, node).strip()


def _is_export_wrapped(node: Node) -> bool:
    return node.type == "export_statement"


def _unwrap_export(node: Node) -> Node:
    if node.type != "export_statement":
        return node
    for child in node.named_children:
        return child
    return node


def _iter_nodes(node: Node) -> Iterable[Node]:
    yield node
    for child in node.named_children:
        yield from _iter_nodes(child)


def _member_chain(source: bytes, node: Node) -> str:
    if node.type in {"identifier", "property_identifier", "type_identifier"}:
        return _text(source, node)
    if node.type in {"this", "super"}:
        return ""
    if node.type in {"member_expression", "subscript_expression"}:
        obj = _member_chain(source, _child(node, "object"))
        prop = _member_chain(source, _child(node, "property"))
        if obj and prop:
            parts = [p for p in [obj, prop] if p]
            return ".".join(parts[-2:])
        return prop or obj
    return ""


def _call_name(source: bytes, node: Node) -> str:
    if node.type in {"call_expression", "new_expression"}:
        fn = _child(node, "function") or node.named_children[0]
        return _call_name(source, fn)
    if node.type in {"identifier", "property_identifier"}:
        return _text(source, node)
    if node.type == "member_expression":
        return _member_chain(source, node)
    return ""


def _parameter_types(source: bytes, params: Optional[Node]) -> List[str]:
    values: List[str] = []
    for child in _named_children(params):
        if child.type in {"required_parameter", "optional_parameter", "rest_parameter"}:
            type_node = _child(child, "type")
            if type_node is None:
                type_node = next((n for n in child.named_children if n.type == "type_annotation"), None)
            values.append(_type_text(source, type_node) or "?")
    return values


def _return_type(source: bytes, node: Node) -> Optional[str]:
    type_node = _child(node, "return_type")
    if type_node is None:
        for child in node.named_children:
            if child.type == "type_annotation":
                type_node = child
                break
    value = _type_text(source, type_node)
    return value or None


def _semantic_summary(source: bytes, node: Node, bases: Optional[List[str]] = None) -> Dict[str, object]:
    calls: set[str] = set()
    flags: set[str] = set()
    assigns = 0

    for child in _iter_nodes(node):
        if child.type in {"call_expression", "new_expression"}:
            call = _call_name(source, child)
            if call:
                calls.add(call)
        elif child.type in {"if_statement", "switch_statement", "conditional_expression"}:
            flags.add("I")
        elif child.type in {"for_statement", "for_in_statement", "while_statement", "do_statement"}:
            flags.add("L")
        elif child.type == "try_statement":
            flags.add("T")
        elif child.type == "await_expression":
            flags.add("A")
        elif child.type == "return_statement":
            flags.add("R")
        elif child.type == "throw_statement":
            flags.add("E")
        elif child.type in {"assignment_expression", "variable_declarator", "update_expression"}:
            assigns += 1

    bases = sorted(set(bases or []))
    for base in bases:
        calls.add(base)

    return {
        "calls": sorted(calls),
        "flags": "".join(sorted(flags)),
        "assigns": assigns,
        "bases": bases,
    }


def _entity_entry(
    kind: str,
    name: str,
    node: Node,
    scope: List[str],
    source: bytes,
    semantic_node: Optional[Node] = None,
    bases: Optional[List[str]] = None,
    param_types: Optional[List[str]] = None,
    return_type: Optional[str] = None,
    include_semantic: bool = True,
) -> Dict[str, object]:
    start_line = node.start_point.row + 1
    end_line = node.end_point.row + 1
    qualified_name = ".".join([*scope, name]) if scope else name
    entry: Dict[str, object] = {
        "kind": kind,
        "name": name,
        "qualified_name": qualified_name,
        "start_line": start_line,
        "end_line": end_line,
    }
    if include_semantic:
        semantic = _semantic_summary(source, semantic_node or node, bases=bases)
        semantic["type_sig"] = {
            "param_types": list(param_types or []),
            "return_type": return_type,
        }
        entry["semantic"] = semantic
    return entry


@dataclass
class TSVisitor:
    source: bytes
    include_semantic: bool = True

    def __post_init__(self) -> None:
        self.entities: List[Dict[str, object]] = []
        self.scope: List[str] = []

    def visit_program(self, node: Node) -> None:
        for child in node.named_children:
            self.visit_statement(child)

    def visit_statement(self, node: Node) -> None:
        unwrapped = _unwrap_export(node)
        handler = getattr(self, f"visit_{unwrapped.type}", None)
        if handler:
            handler(unwrapped)

    def visit_expression_statement(self, node: Node) -> None:
        if node.named_children:
            inner = node.named_children[0]
            handler = getattr(self, f"visit_{inner.type}", None)
            if handler:
                handler(inner)

    def visit_function_declaration(self, node: Node) -> None:
        name = _identifier_text(self.source, _child(node, "name"))
        params = _parameter_types(self.source, _child(node, "parameters"))
        ret = _return_type(self.source, node)
        kind = "async_function" if _text(self.source, node).lstrip().startswith("async ") else "function"
        self.entities.append(
            _entity_entry(kind, name, node, self.scope, self.source, param_types=params, return_type=ret, include_semantic=self.include_semantic)
        )

    def visit_class_declaration(self, node: Node) -> None:
        name = _identifier_text(self.source, _child(node, "name"))
        heritage = next((child for child in node.named_children if child.type == "class_heritage"), None)
        bases: List[str] = []
        if heritage is not None:
            for child in _iter_nodes(heritage):
                value = _identifier_text(self.source, child)
                if value and value not in {name, "extends", "implements"}:
                    bases.append(value)

        self.entities.append(
            _entity_entry("class", name, node, self.scope, self.source, bases=bases, include_semantic=self.include_semantic)
        )
        self.scope.append(name)
        body = _child(node, "body")
        for child in _named_children(body):
            if child.type == "method_definition":
                self.visit_method_definition(child)
            elif child.type == "public_field_definition":
                self.visit_public_field_definition(child)
        self.scope.pop()

    def visit_method_definition(self, node: Node) -> None:
        name = _identifier_text(self.source, _child(node, "name") or node.named_children[0])
        params = _parameter_types(self.source, _child(node, "parameters"))
        ret = _return_type(self.source, node)
        kind = "async_method" if _text(self.source, node).lstrip().startswith("async ") else "method"
        body = _child(node, "body") or node
        self.entities.append(
            _entity_entry(kind, name, node, self.scope, self.source, semantic_node=body, param_types=params, return_type=ret, include_semantic=self.include_semantic)
        )

    def visit_public_field_definition(self, node: Node) -> None:
        name = _identifier_text(self.source, _child(node, "name") or node.named_children[0])
        value = _child(node, "value") or next((child for child in node.named_children if child.type == "arrow_function"), None)
        if value is None or value.type != "arrow_function":
            return
        params = _parameter_types(self.source, _child(value, "parameters"))
        ret = _return_type(self.source, value)
        kind = "async_method" if _text(self.source, value).lstrip().startswith("async ") else "method"
        self.entities.append(
            _entity_entry(kind, name, node, self.scope, self.source, semantic_node=value, param_types=params, return_type=ret, include_semantic=self.include_semantic)
        )

    def visit_interface_declaration(self, node: Node) -> None:
        name = _identifier_text(self.source, _child(node, "name"))
        bases: List[str] = []
        for child in _iter_nodes(node):
            if child.type == "extends_type_clause":
                for nested in child.named_children:
                    value = _identifier_text(self.source, nested)
                    if value:
                        bases.append(value)
        self.entities.append(
            _entity_entry("interface", name, node, self.scope, self.source, bases=bases, include_semantic=self.include_semantic)
        )

    def visit_type_alias_declaration(self, node: Node) -> None:
        name = _identifier_text(self.source, _child(node, "name"))
        self.entities.append(
            _entity_entry("type_alias", name, node, self.scope, self.source, include_semantic=self.include_semantic)
        )

    def visit_enum_declaration(self, node: Node) -> None:
        name = _identifier_text(self.source, _child(node, "name"))
        self.entities.append(
            _entity_entry("enum", name, node, self.scope, self.source, include_semantic=self.include_semantic)
        )

    def visit_internal_module(self, node: Node) -> None:
        name = _identifier_text(self.source, _child(node, "name"))
        self.entities.append(
            _entity_entry("namespace", name, node, self.scope, self.source, include_semantic=self.include_semantic)
        )
        self.scope.append(name)
        body = _child(node, "body")
        for child in _named_children(body):
            self.visit_statement(child)
        self.scope.pop()

    def visit_lexical_declaration(self, node: Node) -> None:
        text = _text(self.source, node).lstrip()
        declaration_kind = "const" if text.startswith("const") else "variable"
        for declarator in _named_children(node, "variable_declarator"):
            name = _identifier_text(self.source, _child(declarator, "name"))
            value = _child(declarator, "value")
            if value is not None and value.type in {"arrow_function", "function_expression"}:
                params = _parameter_types(self.source, _child(value, "parameters"))
                ret = _return_type(self.source, value)
                kind = "async_function" if _text(self.source, value).lstrip().startswith("async ") else "function"
                self.entities.append(
                    _entity_entry(kind, name, declarator, self.scope, self.source, semantic_node=value, param_types=params, return_type=ret, include_semantic=self.include_semantic)
                )
            elif declaration_kind == "const":
                self.entities.append(
                    _entity_entry("constant", name, declarator, self.scope, self.source, include_semantic=self.include_semantic)
                )


class TypeScriptFrontend(LanguageFrontend):
    language = "typescript"
    extensions = (".ts", ".tsx")

    def parse_file(self, file_path: Path) -> Optional[ParsedFile]:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        parser = get_parser("tsx" if file_path.suffix.lower() == ".tsx" else "typescript")
        tree = parser.parse(source.encode("utf-8"))
        return ParsedFile(language=self.language, file_path=file_path, source=source, tree=tree)

    def parse_bare_entities(self, parsed: ParsedFile) -> List[Dict[str, object]]:
        visitor = TSVisitor(parsed.source.encode("utf-8"), include_semantic=False)
        visitor.visit_program(parsed.tree.root_node)
        return visitor.entities

    def parse_entities(self, parsed: ParsedFile) -> List[Dict[str, object]]:
        visitor = TSVisitor(parsed.source.encode("utf-8"), include_semantic=True)
        visitor.visit_program(parsed.tree.root_node)
        return visitor.entities

    def classify_file(self, file_path: Path, parsed: Optional[ParsedFile]) -> str:
        lower_name = file_path.name.lower()
        if lower_name in TS_FILENAME_CATEGORY_MAP:
            return TS_FILENAME_CATEGORY_MAP[lower_name]
        if "route" in lower_name:
            return "router"
        if lower_name.endswith(".test.ts") or lower_name.endswith(".test.tsx") or lower_name.endswith(".spec.ts") or lower_name.endswith(".spec.tsx"):
            return "tests"
        for part in file_path.parts:
            if part.lower() in TS_DIR_CATEGORY_MAP:
                return TS_DIR_CATEGORY_MAP[part.lower()]
        if parsed is None:
            return "core_logic"

        root = parsed.tree.root_node
        import_names = self._import_names(parsed)
        interface_count = 0
        type_count = 0
        class_count = 0
        function_count = 0
        top_level_consts = 0
        route_signals = 0

        for child in root.named_children:
            node = _unwrap_export(child)
            if node.type in {"interface_declaration", "type_alias_declaration"}:
                if node.type == "interface_declaration":
                    interface_count += 1
                else:
                    type_count += 1
            elif node.type == "class_declaration":
                class_count += 1
            elif node.type in {"function_declaration", "lexical_declaration"}:
                function_count += 1
            if node.type == "lexical_declaration" and _text(parsed.source.encode("utf-8"), node).lstrip().startswith("const "):
                top_level_consts += len(_named_children(node, "variable_declarator"))

        if lower_name.startswith("use") and file_path.suffix == ".ts":
            return "utils"
        if lower_name in {"route.ts", "route.tsx", "router.ts", "routes.ts"}:
            return "router"
        if any(name in {"express", "fastify"} for name in import_names):
            route_signals += 1
        if any(method in parsed.source for method in [".get(", ".post(", ".put(", ".delete(", ".patch("]):
            route_signals += 1
        if route_signals >= 1:
            return "router"
        if interface_count + type_count >= 2 or "zod" in import_names:
            return "schema"
        if "process" in parsed.source and any(name in {"os", "path"} for name in import_names):
            return "compat"
        if top_level_consts >= 3 and class_count == 0 and function_count <= 1:
            return "constants"
        if class_count + function_count <= 2:
            return "utils"
        return "core_logic"

    def classify_domain(self, file_path: Path, parsed: Optional[ParsedFile]) -> str:
        stem = file_path.stem.lower()
        if stem in TS_DOMAIN_FILE_PATTERNS:
            return TS_DOMAIN_FILE_PATTERNS[stem]
        for part in file_path.parts:
            lower = part.lower()
            if lower in TS_DOMAIN_FILE_PATTERNS:
                return TS_DOMAIN_FILE_PATTERNS[lower]
        if parsed is None:
            return "unknown"
        scores: Dict[str, int] = {}
        for name in self._import_names(parsed):
            domain = TS_DOMAIN_IMPORTS.get(name)
            if domain:
                scores[domain] = scores.get(domain, 0) + 1
        if scores:
            return max(scores, key=scores.get)
        return "unknown"

    def extract_internal_dependencies(
        self,
        parsed: ParsedFile,
        repo_path: Path,
        all_source_paths: set[str],
    ) -> List[str]:
        deps: List[str] = []
        for spec in self._import_specs(parsed):
            target = self._resolve_module_specifier(parsed.file_path, spec, repo_path, all_source_paths)
            if target:
                deps.append(target)
        return sorted(set(deps))

    def build_import_map(
        self,
        parsed: ParsedFile,
        repo_path: Path,
        entities_by_file: Dict[str, List[Dict[str, object]]],
    ) -> Dict[str, str]:
        all_source_paths = set(entities_by_file.keys())
        import_map: Dict[str, str] = {}
        source_bytes = parsed.source.encode("utf-8")

        for stmt in _named_children(parsed.tree.root_node, "import_statement"):
            clause = next((child for child in stmt.named_children if child.type == "import_clause"), None)
            source_node = next((child for child in stmt.named_children if child.type == "string"), None)
            spec = _text(source_bytes, source_node).strip("\"'")
            target_file = self._resolve_module_specifier(parsed.file_path, spec, repo_path, all_source_paths)
            target_entities = entities_by_file.get(target_file or "", [])
            if clause is None:
                continue

            for child in clause.named_children:
                if child.type == "identifier":
                    default_name = _text(source_bytes, child)
                    default_target = next((entity for entity in target_entities if entity["name"] == "default"), None)
                    import_map[default_name] = str(default_target["qualified_name"]) if default_target else default_name
                elif child.type == "named_imports":
                    for specifier in _named_children(child, "import_specifier"):
                        imported_node = _child(specifier, "name") or (specifier.named_children[0] if specifier.named_children else None)
                        alias_node = _child(specifier, "alias") or imported_node
                        imported = _text(source_bytes, imported_node)
                        local = _text(source_bytes, alias_node)
                        target = next((entity for entity in target_entities if entity["name"] == imported), None)
                        import_map[local] = str(target["qualified_name"]) if target else imported
                elif child.type == "namespace_import":
                    alias = _identifier_text(source_bytes, child)
                    if target_file:
                        stem = Path(target_file).stem
                        import_map[alias] = stem
        return import_map

    def _import_names(self, parsed: ParsedFile) -> List[str]:
        names: List[str] = []
        for spec in self._import_specs(parsed):
            if spec.startswith("."):
                continue
            names.append(spec.split("/")[0] if not spec.startswith("@") else "/".join(spec.split("/")[:2]))
        return sorted(set(names))

    def _import_specs(self, parsed: ParsedFile) -> List[str]:
        source_bytes = parsed.source.encode("utf-8")
        specs: List[str] = []
        for stmt in parsed.tree.root_node.named_children:
            if stmt.type == "import_statement":
                source_node = next((child for child in stmt.named_children if child.type == "string"), None)
                spec = _text(source_bytes, source_node).strip("\"'")
                if spec:
                    specs.append(spec)
            elif stmt.type == "export_statement":
                source_node = next((child for child in stmt.named_children if child.type == "string"), None)
                spec = _text(source_bytes, source_node).strip("\"'")
                if spec:
                    specs.append(spec)
        return specs

    def _resolve_module_specifier(
        self,
        file_path: Path,
        spec: str,
        repo_path: Path,
        all_source_paths: set[str],
    ) -> Optional[str]:
        if not spec:
            return None
        candidates: List[Path] = []
        if spec.startswith("."):
            base = (file_path.parent / spec).resolve()
            candidates.append(base)
        elif not spec.startswith("@") and "/" in spec:
            candidates.append((repo_path / spec).resolve())
        else:
            return None

        suffixes = ["", ".ts", ".tsx", "/index.ts", "/index.tsx"]
        for base in candidates:
            for suffix in suffixes:
                candidate = Path(f"{base}{suffix}") if suffix and not suffix.startswith("/") else (base / suffix[1:] if suffix.startswith("/") else base)
                try:
                    rel = candidate.resolve().relative_to(repo_path.resolve()).as_posix()
                except ValueError:
                    continue
                if rel in all_source_paths:
                    return rel
        return None
