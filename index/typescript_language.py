"""TypeScript language frontend for CodeIR."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

try:
    from tree_sitter import Language, Node, Parser, Tree
    import tree_sitter_typescript
except ImportError:  # pragma: no cover - exercised in packaging tests
    Language = None  # type: ignore[assignment]
    Node = object  # type: ignore[assignment,misc]
    Parser = None  # type: ignore[assignment]
    Tree = object  # type: ignore[assignment,misc]
    tree_sitter_typescript = None  # type: ignore[assignment]


TYPESCRIPT_CALL_STOPLIST: Set[str] = {
    "console", "log", "warn", "error", "info", "debug",
    "parseInt", "parseFloat", "isNaN", "isFinite",
    "Promise", "resolve", "reject", "then", "catch", "finally",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "Array", "Object", "String", "Number", "Boolean", "Map", "Set",
    "JSON", "parse", "stringify",
    "toString", "valueOf", "hasOwnProperty",
    "map", "filter", "reduce", "forEach", "find", "findIndex",
    "some", "every", "includes", "indexOf", "flat", "flatMap",
    "slice", "splice", "push", "pop", "shift", "unshift",
    "keys", "values", "entries", "assign", "freeze", "create",
    "join", "split", "trim", "replace", "match", "test",
    "get", "set", "add", "delete", "has", "clear",
    "bind", "call", "apply",
}

_TS_FILENAME_CATEGORIES: Tuple[tuple[Tuple[str, ...], str], ...] = (
    (("index.ts", "index.tsx", "index.d.ts"), "init"),
    (("jest.config.ts", "vitest.config.ts", "vite.config.ts"), "config"),
    (("config.ts", "configuration.ts", "settings.ts", "env.ts"), "config"),
    (("types.ts", "types.d.ts", "interfaces.ts", "models.ts", "schemas.ts", "dtos.ts"), "schema"),
    (("errors.ts", "exceptions.ts", "error.ts"), "exceptions"),
    (("constants.ts", "consts.ts", "const.ts", "enums.ts"), "constants"),
)

_TS_DIRECTORY_CATEGORIES: Dict[str, str] = {
    "__tests__": "tests",
    "tests": "tests",
    "test": "tests",
    "spec": "tests",
    "config": "config",
    "configuration": "config",
    "schemas": "schema",
    "models": "schema",
    "types": "schema",
    "interfaces": "schema",
    "dtos": "schema",
    "controllers": "router",
    "routes": "router",
    "routers": "router",
}

_TS_DOMAIN_FILE_PATTERNS: Dict[str, str] = {
    "http": "http",
    "api": "http",
    "client": "http",
    "server": "http",
    "request": "http",
    "response": "http",
    "middleware": "http",
    "router": "http",
    "controller": "http",
    "auth": "auth",
    "authentication": "auth",
    "login": "auth",
    "oauth": "auth",
    "jwt": "auth",
    "token": "auth",
    "password": "auth",
    "crypto": "crypto",
    "hash": "crypto",
    "database": "db",
    "db": "db",
    "orm": "db",
    "query": "db",
    "repository": "db",
    "schema": "db",
    "cli": "cli",
    "commands": "cli",
    "parser": "parse",
    "serializer": "parse",
    "codec": "parse",
}

_TS_DOMAIN_IMPORTS_STRONG: Dict[str, str] = {
    "express": "http",
    "fastify": "http",
    "axios": "http",
    "node-fetch": "http",
    "koa": "http",
    "passport": "auth",
    "jsonwebtoken": "auth",
    "bcrypt": "auth",
    "typeorm": "db",
    "prisma": "db",
    "@prisma/client": "db",
    "mongoose": "db",
    "sequelize": "db",
    "knex": "db",
    "commander": "cli",
    "yargs": "cli",
    "inquirer": "cli",
    "ws": "net",
    "socket.io": "net",
}

_TS_DOMAIN_IMPORTS_WEAK: Dict[str, str] = {
    "http": "http",
    "https": "http",
    "url": "http",
    "crypto": "crypto",
    "fs": "fs",
    "path": "fs",
    "child_process": "async",
    "worker_threads": "async",
}

_SOURCE_ROOT_MARKERS = {"src", "app", "lib"}
_TEST_ROOT_MARKERS = {"tests", "test", "__tests__", "spec"}
_UTILITY_ROOT_MARKERS = {"examples", "example"}


@dataclass(frozen=True)
class ParsedTypeScriptFile:
    tree: Tree
    source: bytes
    is_tsx: bool


def _require_typescript_language(is_tsx: bool) -> Language:
    if Language is None or Parser is None or tree_sitter_typescript is None:
        raise RuntimeError(
            "TypeScript support requires optional dependencies 'tree-sitter' and "
            "'tree-sitter-typescript'. Install the typescript extra to index "
            "TypeScript repositories."
        )
    handle = (
        tree_sitter_typescript.language_tsx()
        if is_tsx
        else tree_sitter_typescript.language_typescript()
    )
    return Language(handle)


def _read_source(file_path: Path) -> Optional[bytes]:
    try:
        return file_path.read_bytes()
    except OSError:
        return None


def _node_text(node: Optional[Node], source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _iter_nodes(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _iter_nodes(child)


def _find_child(node: Node, type_name: str) -> Optional[Node]:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_children(node: Node, type_name: str) -> List[Node]:
    return [child for child in node.children if child.type == type_name]


def _identifier_text(node: Optional[Node], source: bytes) -> str:
    if node is None:
        return ""
    if node.type in {"identifier", "type_identifier", "property_identifier"}:
        return _node_text(node, source)
    for child in node.children:
        value = _identifier_text(child, source)
        if value:
            return value
    return ""


def _strip_typescript_suffix(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".d.ts"):
        return name[:-5]
    return Path(name).stem


def _module_scope_from_path(file_path: Path, repo_path: Optional[Path] = None) -> List[str]:
    if repo_path is not None:
        try:
            relative = file_path.resolve().relative_to(repo_path.resolve())
            parts = list(relative.parts)
        except ValueError:
            parts = list(file_path.parts)
    else:
        parts = list(file_path.parts)

    prefix: List[str] = []
    rel = parts

    for marker_set, marker_value in (
        (_TEST_ROOT_MARKERS, "tests"),
        (_UTILITY_ROOT_MARKERS, "examples"),
        (_SOURCE_ROOT_MARKERS, ""),
    ):
        indices = [idx for idx, part in enumerate(parts[:-1]) if part.lower() in marker_set]
        if not indices:
            continue
        idx = indices[0]
        prefix = list(parts[:idx]) if repo_path is not None else []
        if marker_value:
            prefix.append(marker_value)
        rel = parts[idx + 1 :]
        break

    if rel is parts:
        if repo_path is not None:
            rel = parts
        elif file_path.is_absolute():
            rel = list(file_path.parts[-2:]) if len(file_path.parts) >= 2 else [file_path.name]

    if not rel:
        return prefix

    directories = list(rel[:-1])
    stem = _strip_typescript_suffix(rel[-1])
    scope = prefix + directories
    if stem and stem != "index":
        scope.append(stem)
    return [part for part in scope if part]


def _qualified_name(scope: Sequence[str], name: str) -> str:
    return ".".join([*scope, name]) if scope else name


def _start_end(node: Node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _is_async(node: Node) -> bool:
    for child in node.children:
        if child.type == "async":
            return True
        if child.type in {"identifier", "type_identifier", "property_identifier", "formal_parameters"}:
            break
    return False


def _extract_type_text(node: Optional[Node], source: bytes) -> Optional[str]:
    if node is None:
        return None
    if node.type == "type_annotation":
        for child in node.children:
            if child.type != ":":
                return _node_text(child, source)
    return _node_text(node, source)


def _extract_type_signature(node: Node, source: bytes) -> Dict[str, object]:
    param_types: List[str] = []
    return_type: Optional[str] = None

    params = node.child_by_field_name("parameters") or _find_child(node, "formal_parameters")
    if params is not None:
        for param in params.children:
            if param.type in {"required_parameter", "optional_parameter", "rest_parameter"}:
                type_node = param.child_by_field_name("type") or _find_child(param, "type_annotation")
                param_types.append(_extract_type_text(type_node, source) or "?")

    type_params = node.child_by_field_name("type_parameters")
    generic_params: List[str] = []
    if type_params is not None:
        for child in type_params.children:
            if child.type == "type_parameter":
                generic_name = _identifier_text(child.child_by_field_name("name"), source)
                if generic_name:
                    generic_params.append(generic_name)

    return_node = node.child_by_field_name("return_type") or _find_child(node, "type_annotation")
    return_type = _extract_type_text(return_node, source)

    return {
        "param_types": param_types,
        "return_type": return_type,
        "generic_params": generic_params,
    }


def _call_name(node: Node, source: bytes) -> str:
    function_node = node.child_by_field_name("function")
    if function_node is None:
        for child in node.children:
            if child.type == "arguments":
                break
            function_node = child
            break
    if function_node is None:
        return ""

    if function_node.type in {"identifier", "property_identifier"}:
        return _node_text(function_node, source)

    if function_node.type == "member_expression":
        parts: List[str] = []
        current: Optional[Node] = function_node
        while current is not None and current.type == "member_expression":
            prop = current.child_by_field_name("property")
            prop_name = _identifier_text(prop, source)
            if prop_name:
                parts.append(prop_name)
            current = current.child_by_field_name("object")
        if current is not None:
            head = _identifier_text(current, source)
            if head and head != "this":
                parts.append(head)
        parts.reverse()
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return parts[0] if parts else ""

    if function_node.type == "new_expression":
        constructor = function_node.child_by_field_name("constructor")
        return _identifier_text(constructor, source)

    return ""


def _semantic_summary(node: Node, source: bytes, kind: str) -> Dict[str, object]:
    calls: Set[str] = set()
    flags: Set[str] = set()
    bases: Set[str] = set()
    assign_count = 0

    for child in _iter_nodes(node):
        node_type = child.type
        if node_type == "call_expression":
            call = _call_name(child, source)
            if call:
                calls.add(call)
        elif node_type == "new_expression":
            ctor = _identifier_text(child.child_by_field_name("constructor"), source)
            if ctor:
                calls.add(ctor)
        elif node_type in {"if_statement", "switch_statement", "ternary_expression"}:
            flags.add("I")
        elif node_type in {"for_statement", "for_in_statement", "while_statement", "do_statement"}:
            flags.add("L")
        elif node_type == "try_statement":
            flags.add("T")
        elif node_type == "await_expression":
            flags.add("A")
        elif node_type == "return_statement":
            flags.add("R")
        elif node_type == "throw_statement":
            flags.add("E")
        elif node_type in {"variable_declarator", "assignment_expression"}:
            assign_count += 1

    if kind == "class":
        heritage = _find_child(node, "class_heritage")
        if heritage is not None:
            for clause in heritage.children:
                if clause.type not in {"extends_clause", "implements_clause"}:
                    continue
                for child in clause.children:
                    if child.type in {"type_identifier", "identifier"}:
                        base = _node_text(child, source)
                        if base:
                            bases.add(base)
                            calls.add(base)
    elif kind == "interface":
        for clause in node.children:
            if clause.type != "extends_type_clause":
                continue
            for child in clause.children:
                if child.type in {"type_identifier", "identifier"}:
                    base = _node_text(child, source)
                    if base:
                        bases.add(base)

    return {
        "calls": sorted(calls),
        "flags": "".join(sorted(flags)),
        "assigns": assign_count,
        "bases": sorted(bases),
    }


def _exported_declaration(node: Node) -> Optional[Node]:
    if node.type != "export_statement":
        return node
    for child in node.children:
        if child.type in {
            "function_declaration",
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
            "lexical_declaration",
            "internal_module",
        }:
            return child
    return None


class _TypeScriptExtractor:
    def __init__(self, module_scope: Sequence[str], include_semantic: bool = True) -> None:
        self.entities: List[Dict[str, object]] = []
        self.scope = list(module_scope)
        self.include_semantic = include_semantic

    def _append(
        self,
        node: Node,
        source: bytes,
        kind: str,
        name: str,
        *,
        semantic_node: Optional[Node] = None,
    ) -> None:
        start_line, end_line = _start_end(node)
        entry: Dict[str, object] = {
            "kind": kind,
            "name": name,
            "qualified_name": _qualified_name(self.scope, name),
            "start_line": start_line,
            "end_line": end_line,
        }
        if self.include_semantic:
            sem_node = semantic_node or node
            semantic = _semantic_summary(sem_node, source, kind)
            semantic["type_sig"] = _extract_type_signature(sem_node, source)
            if kind in {"async_function", "async_method"}:
                semantic["flags"] = "".join(sorted(set(semantic["flags"]) | {"A"}))
            entry["semantic"] = semantic
        self.entities.append(entry)

    def _visit_function(self, node: Node, source: bytes, *, method: bool = False) -> None:
        name = _identifier_text(node.child_by_field_name("name"), source)
        if not name:
            return
        is_async = _is_async(node)
        kind = "async_method" if (method and is_async) else "method" if method else "async_function" if is_async else "function"
        self._append(node, source, kind, name)

    def _visit_class(self, node: Node, source: bytes) -> None:
        name = _identifier_text(node.child_by_field_name("name"), source)
        if not name:
            return
        self._append(node, source, "class", name)
        self.scope.append(name)
        body = node.child_by_field_name("body") or _find_child(node, "class_body")
        if body is not None:
            for child in body.children:
                if child.type == "method_definition":
                    self._visit_function(child, source, method=True)
        self.scope.pop()

    def _visit_interface(self, node: Node, source: bytes) -> None:
        name = _identifier_text(node.child_by_field_name("name"), source)
        if name:
            self._append(node, source, "interface", name)

    def _visit_type_alias(self, node: Node, source: bytes) -> None:
        name = _identifier_text(node.child_by_field_name("name"), source)
        if name:
            self._append(node, source, "type_alias", name)

    def _visit_enum(self, node: Node, source: bytes) -> None:
        name = _identifier_text(node.child_by_field_name("name"), source)
        if name:
            self._append(node, source, "enum", name)

    def _visit_namespace(self, node: Node, source: bytes) -> None:
        name = _identifier_text(node.child_by_field_name("name"), source) or _identifier_text(_find_child(node, "identifier"), source)
        if not name:
            return
        self._append(node, source, "namespace", name)
        self.scope.append(name)
        block = node.child_by_field_name("body") or _find_child(node, "statement_block")
        if block is not None:
            self._visit_container(block, source)
        self.scope.pop()

    def _visit_lexical(self, node: Node, source: bytes) -> None:
        for declarator in _find_children(node, "variable_declarator"):
            name = _identifier_text(declarator.child_by_field_name("name"), source)
            if not name:
                continue
            value = declarator.child_by_field_name("value")
            if value is not None and value.type == "arrow_function":
                is_async = _is_async(value)
                kind = "async_function" if is_async else "function"
                self._append(declarator, source, kind, name, semantic_node=value)
            else:
                self._append(declarator, source, "constant", name, semantic_node=declarator)

    def _visit_container(self, node: Node, source: bytes) -> None:
        for child in node.children:
            actual = _exported_declaration(child)
            if actual is None:
                continue
            if actual.type == "function_declaration":
                self._visit_function(actual, source)
            elif actual.type == "class_declaration":
                self._visit_class(actual, source)
            elif actual.type == "interface_declaration":
                self._visit_interface(actual, source)
            elif actual.type == "type_alias_declaration":
                self._visit_type_alias(actual, source)
            elif actual.type == "enum_declaration":
                self._visit_enum(actual, source)
            elif actual.type == "internal_module":
                self._visit_namespace(actual, source)
            elif actual.type == "lexical_declaration":
                self._visit_lexical(actual, source)

    def extract(self, parsed: ParsedTypeScriptFile) -> List[Dict[str, object]]:
        self._visit_container(parsed.tree.root_node, parsed.source)
        return self.entities


def _extract_import_source(node: Node, source: bytes) -> str:
    string_node = _find_child(node, "string")
    if string_node is None:
        return ""
    fragment = _find_child(string_node, "string_fragment")
    if fragment is not None:
        return _node_text(fragment, source)
    text = _node_text(string_node, source)
    return text.strip("'\"")


def _package_root(source: str) -> str:
    if source.startswith("@"):
        parts = source.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else source
    return source.split("/")[0]


def _resolve_module_specifier(module_spec: str, file_path: Path, repo_path: Path) -> str:
    if not module_spec.startswith("."):
        return module_spec

    try:
        rel_dir = file_path.resolve().relative_to(repo_path.resolve()).parent
    except ValueError:
        rel_dir = file_path.parent

    current = rel_dir
    for part in module_spec.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            current = current.parent
        else:
            current = current / part

    return ".".join(_module_scope_from_path(current, repo_path))


def _extract_import_names(parsed: ParsedTypeScriptFile, file_path: Path, repo_path: Path) -> List[str]:
    imports: Set[str] = set()
    for child in parsed.tree.root_node.children:
        if child.type not in {"import_statement", "export_statement"}:
            continue
        source = _extract_import_source(child, parsed.source)
        if not source:
            continue
        if source.startswith("."):
            resolved = _resolve_module_specifier(source, file_path, repo_path)
            if resolved:
                imports.add(resolved.split(".")[0])
        else:
            imports.add(_package_root(source))
    return sorted(imports)


def _build_import_map(parsed: ParsedTypeScriptFile, file_path: Path, repo_path: Path) -> Dict[str, str]:
    import_map: Dict[str, str] = {}
    for node in parsed.tree.root_node.children:
        if node.type != "import_statement":
            continue
        source = _extract_import_source(node, parsed.source)
        if not source:
            continue
        module_name = _resolve_module_specifier(source, file_path, repo_path)
        clause = _find_child(node, "import_clause")
        if clause is None:
            continue
        for child in clause.children:
            if child.type == "identifier":
                local_name = _node_text(child, parsed.source)
                import_map[local_name] = f"{module_name}.{local_name}" if module_name else local_name
            elif child.type == "named_imports":
                for spec in _find_children(child, "import_specifier"):
                    name_node = spec.child_by_field_name("name")
                    alias_node = spec.child_by_field_name("alias")
                    original = _identifier_text(name_node, parsed.source)
                    local_name = _identifier_text(alias_node, parsed.source) or original
                    if original and local_name:
                        import_map[local_name] = f"{module_name}.{original}" if module_name else original
            elif child.type == "namespace_import":
                ident = _find_child(child, "identifier")
                local_name = _identifier_text(ident, parsed.source)
                if local_name:
                    import_map[local_name] = module_name
    return import_map


def _classify_file(file_path: Path, parsed: ParsedTypeScriptFile) -> str:
    lower_name = file_path.name.lower()
    for names, category in _TS_FILENAME_CATEGORIES:
        if lower_name in names:
            return category
    if any(
        lower_name.endswith(suffix)
        for suffix in (".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx", ".test.d.ts", ".spec.d.ts")
    ):
        return "tests"

    for part in file_path.parts:
        category = _TS_DIRECTORY_CATEGORIES.get(part.lower())
        if category:
            return category

    interface_count = 0
    type_alias_count = 0
    class_count = 0
    function_count = 0
    enum_count = 0
    const_count = 0
    route_signal_count = 0
    exception_class_count = 0

    for child in parsed.tree.root_node.children:
        actual = _exported_declaration(child)
        if actual is None:
            continue
        if actual.type == "interface_declaration":
            interface_count += 1
        elif actual.type == "type_alias_declaration":
            type_alias_count += 1
        elif actual.type == "class_declaration":
            class_count += 1
            heritage = _find_child(actual, "class_heritage")
            if heritage is not None:
                for clause in heritage.children:
                    if clause.type != "extends_clause":
                        continue
                    for item in clause.children:
                        name = _identifier_text(item, parsed.source).lower()
                        if name == "error" or name.endswith("error") or name.endswith("exception"):
                            exception_class_count += 1
        elif actual.type == "function_declaration":
            function_count += 1
        elif actual.type == "enum_declaration":
            enum_count += 1
        elif actual.type == "lexical_declaration":
            has_arrow = False
            for declarator in _find_children(actual, "variable_declarator"):
                value = declarator.child_by_field_name("value")
                if value is not None and value.type == "arrow_function":
                    function_count += 1
                    has_arrow = True
                else:
                    const_count += 1
            if not has_arrow and const_count == 0:
                const_count += 1

        text = _node_text(actual, parsed.source).lower()
        if any(token in text for token in ("router.", "app.", ".get(", ".post(", ".put(", ".delete(", ".patch(")):
            route_signal_count += 1

    if exception_class_count > 0 and exception_class_count == class_count and function_count == 0:
        return "exceptions"
    if route_signal_count >= 2:
        return "router"

    type_total = interface_count + type_alias_count
    if type_total >= 2 and class_count == 0 and function_count <= 1:
        return "schema"
    if type_total >= 1 and class_count == 0 and function_count == 0:
        return "schema"
    if (const_count >= 2 or enum_count >= 1) and function_count == 0 and class_count == 0 and type_total == 0:
        return "constants"
    if function_count == 0 and class_count == 0 and type_total == 0 and const_count == 0:
        return "docs"
    if function_count <= 2 and class_count == 0 and type_total == 0:
        return "utils"
    return "core_logic"


def _classify_domain(file_path: Path, parsed: ParsedTypeScriptFile) -> str:
    stem = _strip_typescript_suffix(file_path.name).lower()
    for suffix in (".test", ".spec"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    if stem in _TS_DOMAIN_FILE_PATTERNS:
        return _TS_DOMAIN_FILE_PATTERNS[stem]

    for part in file_path.parts:
        domain = _TS_DOMAIN_FILE_PATTERNS.get(part.lower())
        if domain:
            return domain

    scores: Dict[str, int] = {}
    for child in parsed.tree.root_node.children:
        if child.type != "import_statement":
            continue
        source = _extract_import_source(child, parsed.source)
        if not source:
            continue
        root = _package_root(source)
        if root in _TS_DOMAIN_IMPORTS_STRONG:
            scores[_TS_DOMAIN_IMPORTS_STRONG[root]] = scores.get(_TS_DOMAIN_IMPORTS_STRONG[root], 0) + 2
        elif root in _TS_DOMAIN_IMPORTS_WEAK:
            scores[_TS_DOMAIN_IMPORTS_WEAK[root]] = scores.get(_TS_DOMAIN_IMPORTS_WEAK[root], 0) + 1

    qualifying = {domain: score for domain, score in scores.items() if score >= 2}
    if qualifying:
        return max(qualifying, key=qualifying.get)
    return "unknown"


class TypeScriptFrontend:
    name = "typescript"
    extensions = (".ts", ".tsx", ".d.ts")

    def __init__(self) -> None:
        self._ts_parser: Optional[Parser] = None
        self._tsx_parser: Optional[Parser] = None

    @property
    def stoplist(self) -> set[str]:
        return TYPESCRIPT_CALL_STOPLIST

    def matches_path(self, file_path: Path) -> bool:
        lower = file_path.name.lower()
        return any(lower.endswith(ext) for ext in self.extensions)

    def _parser_for(self, file_path: Path) -> Parser:
        is_tsx = file_path.name.lower().endswith(".tsx")
        if is_tsx:
            if self._tsx_parser is None:
                self._tsx_parser = Parser(_require_typescript_language(True))
            return self._tsx_parser
        if self._ts_parser is None:
            self._ts_parser = Parser(_require_typescript_language(False))
        return self._ts_parser

    def parse_ast(self, file_path: Path) -> Optional[ParsedTypeScriptFile]:
        source = _read_source(file_path)
        if source is None:
            return None
        tree = self._parser_for(file_path).parse(source)
        return ParsedTypeScriptFile(
            tree=tree,
            source=source,
            is_tsx=file_path.name.lower().endswith(".tsx"),
        )

    def parse_entities_from_file(
        self,
        file_path: Path,
        include_semantic: bool = True,
        tree: object = None,
    ) -> List[Dict[str, object]]:
        parsed = tree if tree is not None else self.parse_ast(file_path)
        if parsed is None:
            return []
        extractor = _TypeScriptExtractor(
            module_scope=_module_scope_from_path(file_path),
            include_semantic=include_semantic,
        )
        return extractor.extract(parsed)

    def extract_import_names(
        self,
        tree: ParsedTypeScriptFile,
        file_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
    ) -> List[str]:
        if file_path is None or repo_path is None:
            return []
        return _extract_import_names(tree, file_path, repo_path)

    def discover_internal_roots(self, repo_path: Path) -> set[str]:
        roots: Set[str] = set()
        package_json = repo_path / "package.json"
        if package_json.exists():
            try:
                package_data = json.loads(package_json.read_text(encoding="utf-8"))
                package_name = str(package_data.get("name", "")).strip()
                if package_name:
                    roots.add(_package_root(package_name))
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass

        for source_file in repo_path.rglob("*"):
            if not source_file.is_file():
                continue
            if not self.matches_path(source_file):
                continue
            try:
                rel = source_file.relative_to(repo_path)
            except ValueError:
                rel = source_file
            parts = rel.parts
            if not parts:
                continue
            first = parts[0]
            if first.lower() in _SOURCE_ROOT_MARKERS | _TEST_ROOT_MARKERS | _UTILITY_ROOT_MARKERS:
                if len(parts) > 1:
                    roots.add(parts[1] if parts[1].lower() not in _SOURCE_ROOT_MARKERS else _strip_typescript_suffix(parts[-1]))
                else:
                    roots.add(_strip_typescript_suffix(parts[0]))
            else:
                roots.add(first)

        roots.discard("")
        return roots

    def split_imports(
        self,
        all_imports: Sequence[str],
        internal_roots: set[str],
        file_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
    ) -> tuple[List[str], List[str]]:
        from index.language_base import default_split_imports

        return default_split_imports(all_imports, internal_roots)

    def classify_file(self, file_path: Path, tree: ParsedTypeScriptFile) -> str:
        return _classify_file(file_path, tree)

    def classify_domain(self, file_path: Path, tree: ParsedTypeScriptFile) -> str:
        return _classify_domain(file_path, tree)

    def build_import_map(self, tree: ParsedTypeScriptFile, file_path: Path, repo_path: Path) -> Dict[str, str]:
        return _build_import_map(tree, file_path, repo_path)
