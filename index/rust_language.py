"""Rust language frontend for CodeIR."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from tree_sitter import Language, Node, Parser, Tree
    import tree_sitter_rust
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    Language = None  # type: ignore[assignment]
    Node = object  # type: ignore[assignment,misc]
    Parser = None  # type: ignore[assignment]
    Tree = object  # type: ignore[assignment,misc]
    tree_sitter_rust = None  # type: ignore[assignment]


RUST_CALL_STOPLIST: Set[str] = {
    "new", "default", "from", "into", "clone", "to_string", "to_owned",
    "as_ref", "as_mut", "as_slice", "as_str", "as_bytes",
    "unwrap", "expect", "ok", "err", "map", "and_then", "or_else",
    "unwrap_or", "unwrap_or_else", "unwrap_or_default", "map_err",
    "is_some", "is_none", "is_ok", "is_err",
    "iter", "into_iter", "iter_mut", "collect", "filter", "fold",
    "flat_map", "for_each", "any", "all", "find", "position",
    "enumerate", "zip", "chain", "take", "skip", "count",
    "push", "pop", "len", "is_empty", "insert", "remove", "contains",
    "get", "set", "clear", "extend", "drain", "retain", "sort",
    "sort_by", "sort_by_key", "reverse", "first", "last",
    "format", "trim", "split", "starts_with", "ends_with", "replace",
    "println", "eprintln", "print", "eprint", "write", "writeln",
    "read", "read_to_string", "flush",
    "drop", "forget", "size_of", "align_of", "transmute",
    "fmt", "debug", "display",
    "eq", "ne", "cmp", "partial_cmp", "hash",
    "Some", "None", "Ok", "Err",
    "parse", "try_from", "try_into",
}

_FILENAME_CATEGORIES: Tuple[tuple[Tuple[str, ...], str], ...] = (
    (("mod.rs", "lib.rs"), "init"),
    (("main.rs",), "core_logic"),
    (("build.rs", "config.rs", "settings.rs", "configuration.rs"), "config"),
    (("errors.rs", "error.rs", "err.rs"), "exceptions"),
    (("constants.rs", "consts.rs"), "constants"),
    (("schema.rs", "models.rs", "types.rs"), "schema"),
)

_DIRECTORY_CATEGORIES: Dict[str, str] = {
    "tests": "tests",
    "test": "tests",
    "testing": "tests",
    "benches": "tests",
    "examples": "utils",
    "config": "config",
    "configuration": "config",
    "schemas": "schema",
    "models": "schema",
}

_DOMAIN_FILE_PATTERNS: Dict[str, str] = {
    "http": "http", "api": "http", "client": "http", "server": "http",
    "transport": "http", "request": "http", "response": "http",
    "handler": "http", "router": "http", "middleware": "http",
    "auth": "auth", "authentication": "auth", "login": "auth",
    "oauth": "auth", "token": "auth", "credentials": "auth",
    "crypto": "crypto", "encryption": "crypto", "hash": "crypto",
    "signing": "crypto", "certs": "crypto",
    "database": "db", "db": "db", "orm": "db", "query": "db",
    "migration": "db", "schema": "db", "connection": "db",
    "cli": "cli", "commands": "cli", "args": "cli",
    "parser": "parse", "serializer": "parse", "codec": "parse",
    "config": "config", "settings": "config",
}

_DOMAIN_CRATES_STRONG: Dict[str, str] = {
    "reqwest": "http", "hyper": "http", "actix": "http", "actix_web": "http",
    "axum": "http", "warp": "http", "rocket": "http", "tonic": "http",
    "sqlx": "db", "diesel": "db", "sea_orm": "db", "rusqlite": "db",
    "redis": "db", "mongodb": "db",
    "clap": "cli", "structopt": "cli",
    "ring": "crypto", "rustls": "crypto", "openssl": "crypto",
    "jsonwebtoken": "auth",
}

_DOMAIN_CRATES_WEAK: Dict[str, str] = {
    "tokio": "async", "async_std": "async", "futures": "async",
    "serde": "parse", "serde_json": "parse", "toml": "parse",
    "serde_yaml": "parse", "csv": "parse",
}


@dataclass(frozen=True)
class ParsedRustFile:
    tree: Tree
    source: bytes


def _require_rust_parser() -> Parser:
    if Parser is None or Language is None or tree_sitter_rust is None:
        raise RuntimeError(
            "Rust support requires optional dependencies 'tree-sitter' and "
            "'tree-sitter-rust'. Install the rust extra to index Rust repositories."
        )
    language = Language(tree_sitter_rust.language())
    return Parser(language)


class RustFrontend:
    name = "rust"
    extensions = (".rs",)

    def __init__(self) -> None:
        self._parser: Optional[Parser] = None

    @property
    def stoplist(self) -> set[str]:
        return RUST_CALL_STOPLIST

    def matches_path(self, file_path: Path) -> bool:
        return file_path.name.lower().endswith(".rs")

    def _parser_instance(self) -> Parser:
        if self._parser is None:
            self._parser = _require_rust_parser()
        return self._parser

    def parse_ast(self, file_path: Path) -> Optional[ParsedRustFile]:
        try:
            source = file_path.read_bytes()
        except OSError:
            return None
        tree = self._parser_instance().parse(source)
        return ParsedRustFile(tree=tree, source=source)

    def parse_entities_from_file(
        self,
        file_path: Path,
        include_semantic: bool = True,
        tree: object = None,
    ) -> List[Dict[str, object]]:
        parsed = tree if tree is not None else self.parse_ast(file_path)
        if parsed is None:
            return []
        return _extract_entities_from_tree(
            parsed=parsed,
            file_path=file_path,
            include_semantic=include_semantic,
        )

    def extract_import_names(
        self,
        tree: ParsedRustFile,
        file_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
    ) -> List[str]:
        imports = []
        for use_path, _alias in _iter_use_entries(tree.tree.root_node, tree.source):
            if use_path:
                imports.append(use_path[0])
        return sorted(set(imports))

    def discover_internal_roots(self, repo_path: Path) -> set[str]:
        roots = {"crate", "self", "super"}
        cargo_toml = repo_path / "Cargo.toml"
        if cargo_toml.exists():
            package_name = _cargo_package_name(cargo_toml)
            if package_name:
                roots.add(package_name)

        src_dir = repo_path / "src"
        if src_dir.exists():
            for child in src_dir.iterdir():
                if child.is_dir():
                    roots.add(child.name)
                elif child.is_file() and child.suffix == ".rs" and child.stem not in {"lib", "main", "mod"}:
                    roots.add(child.stem)

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

    def classify_file(self, file_path: Path, tree: ParsedRustFile) -> str:
        return _classify_rust_file(file_path, tree.source)

    def classify_domain(self, file_path: Path, tree: ParsedRustFile) -> str:
        return _classify_rust_domain(file_path, tree.source)

    def build_import_map(self, tree: ParsedRustFile, file_path: Path, repo_path: Path) -> Dict[str, str]:
        internal_roots = self.discover_internal_roots(repo_path)
        module_scope = _module_scope_from_path(file_path)
        current_module = list(module_scope)
        current_file_module = list(module_scope)

        if file_path.name == "mod.rs" and current_file_module:
            current_file_module = current_file_module[:-1]

        import_map: Dict[str, str] = {}
        for use_path, alias in _iter_use_entries(tree.tree.root_node, tree.source):
            if not use_path or use_path[-1] == "*":
                continue

            qualified = _resolve_use_path(
                use_path=use_path,
                current_module=current_file_module,
                internal_roots=internal_roots,
            )
            if not qualified:
                continue

            local_name = alias or use_path[-1]
            import_map[local_name] = qualified

        return import_map


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child(node: Node, type_name: str):
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _walk_descendants(node: Node):
    stack = list(node.children)
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _identifier_text(node: Optional[Node], source: bytes) -> str:
    if node is None:
        return ""
    if node.type in {"identifier", "type_identifier", "field_identifier"}:
        return _node_text(node, source)
    for child in node.children:
        value = _identifier_text(child, source)
        if value:
            return value
    return ""


def _module_scope_from_path(file_path: Path) -> List[str]:
    parts = list(file_path.parts)
    if "src" in parts:
        idx = parts.index("src")
        rel = parts[idx + 1 :]
        prefix: List[str] = []
    elif "tests" in parts:
        idx = parts.index("tests")
        rel = parts[idx + 1 :]
        prefix = ["tests"]
    elif "examples" in parts:
        idx = parts.index("examples")
        rel = parts[idx + 1 :]
        prefix = ["examples"]
    elif "benches" in parts:
        idx = parts.index("benches")
        rel = parts[idx + 1 :]
        prefix = ["benches"]
    else:
        rel = [file_path.name]
        prefix = []

    if not rel:
        return prefix

    stem_parts = list(rel[:-1])
    filename = rel[-1]
    stem = Path(filename).stem
    if stem not in {"lib", "main", "mod"}:
        stem_parts.append(stem)

    return prefix + [part for part in stem_parts if part]


def _extract_entities_from_tree(
    parsed: ParsedRustFile,
    file_path: Path,
    include_semantic: bool,
) -> List[Dict[str, object]]:
    entities: List[Dict[str, object]] = []
    module_scope = _module_scope_from_path(file_path)
    _visit_container(
        node=parsed.tree.root_node,
        source=parsed.source,
        entities=entities,
        scope=module_scope,
        include_semantic=include_semantic,
    )
    return entities


def _visit_container(
    node: Node,
    source: bytes,
    entities: List[Dict[str, object]],
    scope: List[str],
    include_semantic: bool,
) -> None:
    for child in node.children:
        if child.type == "function_item":
            _extract_function(child, source, entities, scope, include_semantic)
        elif child.type == "struct_item":
            _extract_type_item(child, source, entities, scope, "struct", include_semantic)
        elif child.type == "enum_item":
            _extract_type_item(child, source, entities, scope, "enum", include_semantic)
        elif child.type == "trait_item":
            _extract_trait(child, source, entities, scope, include_semantic)
        elif child.type == "impl_item":
            _extract_impl(child, source, entities, scope, include_semantic)
        elif child.type in {"const_item", "static_item"}:
            _extract_constant(child, source, entities, scope, include_semantic)
        elif child.type == "mod_item":
            _extract_inline_module(child, source, entities, scope, include_semantic)


def _qualified_name(scope: Sequence[str], name: str) -> str:
    return ".".join([*scope, name]) if scope else name


def _start_end(node: Node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _is_async_function(node: Node) -> bool:
    modifiers = _find_child(node, "function_modifiers")
    if modifiers is None:
        return False
    return any(child.type == "async" for child in modifiers.children)


def _extract_function(
    node: Node,
    source: bytes,
    entities: List[Dict[str, object]],
    scope: List[str],
    include_semantic: bool,
    *,
    kind_override: Optional[str] = None,
    base_traits: Optional[List[str]] = None,
) -> None:
    name = _identifier_text(_find_child(node, "identifier"), source)
    if not name:
        return

    if kind_override:
        kind = kind_override
    elif scope and scope[-1] and scope[-1][0].isupper():
        kind = "async_method" if _is_async_function(node) else "method"
    else:
        kind = "async_function" if _is_async_function(node) else "function"

    start_line, end_line = _start_end(node)
    entry: Dict[str, object] = {
        "kind": kind,
        "name": name,
        "qualified_name": _qualified_name(scope, name),
        "start_line": start_line,
        "end_line": end_line,
    }

    if include_semantic:
        semantic = _extract_semantic_summary(node, source)
        semantic["type_sig"] = _extract_type_signature(node, source)
        if _is_async_function(node):
            semantic["flags"] = "".join(sorted(set(semantic["flags"]) | {"A"}))
        if base_traits:
            semantic["bases"] = sorted(set(semantic.get("bases", [])) | set(base_traits))
        entry["semantic"] = semantic

    entities.append(entry)


def _extract_type_item(
    node: Node,
    source: bytes,
    entities: List[Dict[str, object]],
    scope: List[str],
    kind: str,
    include_semantic: bool,
) -> None:
    name = _identifier_text(_find_child(node, "type_identifier"), source)
    if not name:
        return

    start_line, end_line = _start_end(node)
    entry: Dict[str, object] = {
        "kind": kind,
        "name": name,
        "qualified_name": _qualified_name(scope, name),
        "start_line": start_line,
        "end_line": end_line,
    }

    if include_semantic:
        assigns = 0
        if kind == "struct":
            assigns = _count_nodes_by_type(node, "field_declaration")
        elif kind == "enum":
            assigns = _count_nodes_by_type(node, "enum_variant")

        semantic = {
            "calls": [],
            "flags": "",
            "assigns": assigns,
            "bases": _extract_derive_names(node, source),
            "type_sig": {"param_types": [], "return_type": None},
        }
        if kind == "enum" and _looks_like_error_enum(name, semantic["bases"]):
            semantic["flags"] = "X"
        entry["semantic"] = semantic

    entities.append(entry)


def _extract_trait(
    node: Node,
    source: bytes,
    entities: List[Dict[str, object]],
    scope: List[str],
    include_semantic: bool,
) -> None:
    name = _identifier_text(_find_child(node, "type_identifier"), source)
    if not name:
        return

    start_line, end_line = _start_end(node)
    trait_scope = [*scope, name]
    entry: Dict[str, object] = {
        "kind": "trait",
        "name": name,
        "qualified_name": _qualified_name(scope, name),
        "start_line": start_line,
        "end_line": end_line,
    }

    if include_semantic:
        entry["semantic"] = {
            "calls": [],
            "flags": "",
            "assigns": 0,
            "bases": _extract_trait_bounds(node, source),
            "type_sig": {"param_types": [], "return_type": None},
        }

    entities.append(entry)

    declaration_list = _find_child(node, "declaration_list")
    if declaration_list is None:
        return

    trait_methods = 0
    for child in declaration_list.children:
        if child.type == "function_item":
            _extract_function(
                child,
                source,
                entities,
                trait_scope,
                include_semantic,
                kind_override="trait_method",
            )
            trait_methods += 1
        elif child.type == "function_signature_item":
            _extract_trait_signature(child, source, entities, trait_scope, include_semantic)
            trait_methods += 1

    if include_semantic:
        entry["semantic"]["assigns"] = trait_methods


def _extract_trait_signature(
    node: Node,
    source: bytes,
    entities: List[Dict[str, object]],
    scope: List[str],
    include_semantic: bool,
) -> None:
    name = _identifier_text(_find_child(node, "identifier"), source)
    if not name:
        return

    start_line, end_line = _start_end(node)
    entry: Dict[str, object] = {
        "kind": "trait_method",
        "name": name,
        "qualified_name": _qualified_name(scope, name),
        "start_line": start_line,
        "end_line": end_line,
    }

    if include_semantic:
        entry["semantic"] = {
            "calls": [],
            "flags": "",
            "assigns": 0,
            "bases": [],
            "type_sig": _extract_type_signature(node, source),
        }

    entities.append(entry)


def _extract_impl(
    node: Node,
    source: bytes,
    entities: List[Dict[str, object]],
    scope: List[str],
    include_semantic: bool,
) -> None:
    type_name = _extract_impl_type_name(node, source)
    if not type_name:
        return

    trait_name = _extract_impl_trait_name(node, source)
    declaration_list = _find_child(node, "declaration_list")
    if declaration_list is None:
        return

    impl_scope = [*scope, type_name]
    bases = [trait_name] if trait_name else []
    for child in declaration_list.children:
        if child.type == "function_item":
            _extract_function(
                child,
                source,
                entities,
                impl_scope,
                include_semantic,
                base_traits=bases,
            )
        elif child.type in {"const_item", "static_item"}:
            _extract_constant(child, source, entities, impl_scope, include_semantic)


def _extract_constant(
    node: Node,
    source: bytes,
    entities: List[Dict[str, object]],
    scope: List[str],
    include_semantic: bool,
) -> None:
    name = _identifier_text(_find_child(node, "identifier"), source)
    if not name:
        return

    start_line, end_line = _start_end(node)
    entry: Dict[str, object] = {
        "kind": "constant",
        "name": name,
        "qualified_name": _qualified_name(scope, name),
        "start_line": start_line,
        "end_line": end_line,
    }

    if include_semantic:
        entry["semantic"] = {
            "calls": [],
            "flags": "",
            "assigns": 1,
            "bases": [],
            "type_sig": {"param_types": [], "return_type": None},
        }

    entities.append(entry)


def _extract_inline_module(
    node: Node,
    source: bytes,
    entities: List[Dict[str, object]],
    scope: List[str],
    include_semantic: bool,
) -> None:
    name = _identifier_text(_find_child(node, "identifier"), source)
    declaration_list = _find_child(node, "declaration_list")
    if not name or declaration_list is None:
        return
    _visit_container(
        node=declaration_list,
        source=source,
        entities=entities,
        scope=[*scope, name],
        include_semantic=include_semantic,
    )


def _extract_semantic_summary(node: Node, source: bytes) -> Dict[str, object]:
    body = _find_child(node, "block")
    if body is None:
        return {
            "calls": [],
            "flags": "",
            "assigns": 0,
            "bases": [],
        }

    calls: Set[str] = set()
    flags: Set[str] = set()
    assigns = 0

    for descendant in _walk_descendants(body):
        ntype = descendant.type

        if ntype == "call_expression":
            call_name = _extract_call_name(descendant, source)
            if call_name:
                calls.add(call_name)
        elif ntype == "macro_invocation":
            macro_name = _identifier_text(_find_child(descendant, "identifier"), source)
            if macro_name:
                calls.add(macro_name)
            if macro_name in {"panic", "todo", "unimplemented"}:
                flags.add("E")
        elif ntype in {"if_expression", "match_expression"}:
            flags.add("I")
        elif ntype in {"for_expression", "while_expression", "loop_expression"}:
            flags.add("L")
        elif ntype == "return_expression":
            flags.add("R")
        elif ntype == "await_expression":
            flags.add("A")
        elif ntype == "unsafe_block":
            flags.add("U")
        elif ntype == "try_expression":
            flags.add("E")
        elif ntype in {"let_declaration", "assignment_expression", "compound_assignment_expr"}:
            assigns += 1

    if {"unwrap", "expect"} & calls:
        flags.add("E")

    return {
        "calls": sorted(calls),
        "flags": "".join(sorted(flags)),
        "assigns": assigns,
        "bases": [],
    }


def _extract_call_name(node: Node, source: bytes) -> str:
    func_node = node.children[0] if node.children else None
    if func_node is None:
        return ""

    if func_node.type == "identifier":
        return _node_text(func_node, source)

    if func_node.type == "scoped_identifier":
        parts = _collect_scoped_parts(func_node, source)
        while parts and parts[0] in {"crate", "self", "super", "Self"}:
            parts = parts[1:]
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return parts[0] if parts else ""

    if func_node.type == "field_expression":
        parts: List[str] = []
        current = func_node
        while current is not None and current.type == "field_expression":
            field = _find_child(current, "field_identifier")
            if field is not None:
                parts.append(_node_text(field, source))
            current = current.children[0] if current.children else None

        parts.reverse()
        if current is not None and current.type == "identifier":
            parts.insert(0, _node_text(current, source))
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return parts[0] if parts else ""

    if func_node.type == "generic_function" and func_node.children:
        inner = func_node.children[0]
        if inner.type == "call_expression":
            return _extract_call_name(inner, source)
        if inner.type == "field_expression":
            proxy = type("Proxy", (), {"children": [inner]})()
            return _extract_call_name(proxy, source)
        if inner.type == "identifier":
            return _node_text(inner, source)
        if inner.type == "scoped_identifier":
            parts = _collect_scoped_parts(inner, source)
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return parts[-1] if parts else ""

    return ""


def _collect_scoped_parts(node: Node, source: bytes) -> List[str]:
    parts: List[str] = []

    def _walk(current: Node) -> None:
        for child in current.children:
            if child.type == "scoped_identifier":
                _walk(child)
            elif child.type in {"identifier", "type_identifier", "self", "super", "crate", "Self"}:
                parts.append(_node_text(child, source))

    _walk(node)
    return parts


def _extract_type_signature(node: Node, source: bytes) -> Dict[str, object]:
    param_types: List[str] = []
    return_type: Optional[str] = None

    params_node = _find_child(node, "parameters")
    if params_node is not None:
        for param in params_node.children:
            if param.type == "self_parameter":
                continue
            if param.type != "parameter":
                continue
            found_colon = False
            param_type = "?"
            for child in param.children:
                if child.type == ":":
                    found_colon = True
                    continue
                if not found_colon:
                    continue
                if child.type != ",":
                    param_type = _node_text(child, source)
                    break
            param_types.append(param_type)

    found_arrow = False
    for child in node.children:
        if child.type == "->":
            found_arrow = True
            continue
        if not found_arrow:
            continue
        if child.type not in {"block", "where_clause"}:
            return_type = _node_text(child, source)
            break

    return {"param_types": param_types, "return_type": return_type}


def _count_nodes_by_type(node: Node, type_name: str) -> int:
    return sum(1 for descendant in _walk_descendants(node) if descendant.type == type_name)


def _looks_like_error_enum(name: str, derives: Sequence[str]) -> bool:
    lowered = name.lower()
    return (
        lowered.endswith("error")
        or lowered.endswith("exception")
        or any(base.endswith("Error") or base == "thiserror::Error" for base in derives)
    )


def _extract_derive_names(node: Node, source: bytes) -> List[str]:
    derives: List[str] = []

    for child in node.children:
        if child.type == "attribute_item":
            derives.extend(_derive_names_from_attribute(child, source))

    if node.parent is not None:
        index = None
        for idx, sibling in enumerate(node.parent.children):
            if sibling.id == node.id:
                index = idx
                break
        if index is not None:
            scan = index - 1
            while scan >= 0:
                sibling = node.parent.children[scan]
                if sibling.type != "attribute_item":
                    break
                derives.extend(_derive_names_from_attribute(sibling, source))
                scan -= 1

    return sorted(set(derives))


def _derive_names_from_attribute(node: Node, source: bytes) -> List[str]:
    text = _node_text(node, source)
    match = re.search(r"derive\(([^)]+)\)", text)
    if not match:
        return []
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def _extract_trait_bounds(node: Node, source: bytes) -> List[str]:
    header_end = node.start_byte
    declaration_list = _find_child(node, "declaration_list")
    if declaration_list is not None:
        header_end = declaration_list.start_byte
    header = source[node.start_byte:header_end].decode("utf-8", errors="replace")
    match = re.search(r"trait\s+\w+\s*:\s*([^{]+)", header)
    if not match:
        return []
    bounds = [part.strip() for part in match.group(1).split("+")]
    return [bound for bound in bounds if bound]


def _extract_impl_type_name(node: Node, source: bytes) -> str:
    children = node.children
    seen_for = False
    seen_impl = False

    for child in children:
        if child.type == "impl":
            seen_impl = True
            continue
        if child.type == "for":
            seen_for = True
            continue
        if child.type == "type_parameters":
            continue
        if child.type not in {"type_identifier", "generic_type", "scoped_type_identifier"}:
            continue

        if seen_for:
            return _type_name_from_node(child, source)
        if seen_impl:
            if any(grandchild.type == "for" for grandchild in children):
                continue
            return _type_name_from_node(child, source)

    return ""


def _extract_impl_trait_name(node: Node, source: bytes) -> str:
    children = node.children
    seen_impl = False
    for child in children:
        if child.type == "impl":
            seen_impl = True
            continue
        if not seen_impl:
            continue
        if child.type == "type_parameters":
            continue
        if child.type == "for":
            return ""
        if child.type in {"type_identifier", "generic_type", "scoped_type_identifier"}:
            return _type_name_from_node(child, source)
    return ""


def _type_name_from_node(node: Node, source: bytes) -> str:
    if node.type == "generic_type":
        inner = _find_child(node, "type_identifier")
        return _node_text(inner, source) if inner is not None else _node_text(node, source)
    if node.type == "scoped_type_identifier":
        parts = _collect_scoped_parts(node, source)
        return parts[-1] if parts else _node_text(node, source)
    return _node_text(node, source)


def _cargo_package_name(cargo_toml: Path) -> str:
    try:
        text = cargo_toml.read_text(encoding="utf-8")
    except OSError:
        return ""

    in_package = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_package = stripped == "[package]"
            continue
        if not in_package:
            continue
        match = re.match(r'name\s*=\s*"([^"]+)"', stripped)
        if match:
            return match.group(1).replace("-", "_")
    return ""


def _classify_rust_file(file_path: Path, source: bytes) -> str:
    name = file_path.name.lower()
    for filenames, category in _FILENAME_CATEGORIES:
        if name in filenames:
            return category

    for part in file_path.parts:
        lowered = part.lower()
        if lowered in _DIRECTORY_CATEGORIES:
            return _DIRECTORY_CATEGORIES[lowered]

    text = source.decode("utf-8", errors="ignore")
    if "#[cfg(test)]" in text or "mod tests" in text:
        return "tests"
    if "trait " in text and "struct " not in text and "enum " not in text:
        return "schema"
    if text.count("struct ") + text.count("enum ") >= 2 and text.count("fn ") <= 2:
        return "schema"
    if text.count("const ") >= 3 and text.count("fn ") == 0:
        return "constants"
    if file_path.stem == "mod":
        return "init"
    if text.strip().startswith("//") and "fn " not in text and "struct " not in text:
        return "docs"
    return "core_logic"


def _classify_rust_domain(file_path: Path, source: bytes) -> str:
    for part in (file_path.stem, *file_path.parts):
        lowered = str(part).lower()
        if lowered in _DOMAIN_FILE_PATTERNS:
            return _DOMAIN_FILE_PATTERNS[lowered]

    imports = _crates_from_source(source)
    for crate_name in imports:
        if crate_name in _DOMAIN_CRATES_STRONG:
            return _DOMAIN_CRATES_STRONG[crate_name]

    scores: Dict[str, int] = {}
    for crate_name in imports:
        domain = _DOMAIN_CRATES_WEAK.get(crate_name)
        if domain:
            scores[domain] = scores.get(domain, 0) + 1

    if scores:
        return max(scores, key=scores.get)
    return "unknown"


def _crates_from_source(source: bytes) -> List[str]:
    text = source.decode("utf-8", errors="ignore")
    crates: Set[str] = set()
    for match in re.finditer(r"^\s*use\s+([A-Za-z_][A-Za-z0-9_]*)", text, flags=re.MULTILINE):
        crates.add(match.group(1))
    return sorted(crates)


def _iter_use_entries(root: Node, source: bytes) -> Iterable[tuple[List[str], Optional[str]]]:
    for child in root.children:
        if child.type == "use_declaration":
            target = next(
                (
                    grandchild
                    for grandchild in child.children
                    if grandchild.type not in {"use", ";", "visibility_modifier"}
                ),
                None,
            )
            if target is None:
                continue
            yield from _expand_use_node(target, source)


def _expand_use_node(node: Node, source: bytes) -> Iterable[tuple[List[str], Optional[str]]]:
    if node.type in {"identifier", "type_identifier", "self", "super", "crate"}:
        yield ([_node_text(node, source)], None)
        return

    if node.type == "scoped_identifier":
        yield (_collect_scoped_parts(node, source), None)
        return

    if node.type == "use_as_clause":
        path_node = next(
            (
                child
                for child in node.children
                if child.type in {
                    "identifier", "type_identifier", "self", "super", "crate",
                    "scoped_identifier", "scoped_use_list",
                }
            ),
            None,
        )
        alias_node = node.children[-1] if node.children else None
        alias = _identifier_text(alias_node, source)
        if path_node is None:
            return
        for path, _ in _expand_use_node(path_node, source):
            yield (path, alias or None)
        return

    if node.type == "use_wildcard":
        prefix = next(
            (
                child
                for child in node.children
                if child.type in {"identifier", "type_identifier", "self", "super", "crate", "scoped_identifier"}
            ),
            None,
        )
        if prefix is None:
            return
        prefix_parts = _collect_use_prefix(prefix, source)
        yield (prefix_parts + ["*"], None)
        return

    if node.type == "use_list":
        for child in node.children:
            if child.type in {"{", "}", ","}:
                continue
            yield from _expand_use_node(child, source)
        return

    if node.type == "scoped_use_list":
        prefix_node = None
        list_node = None
        for child in node.children:
            if child.type == "use_list":
                list_node = child
                break
            if child.type not in {"::"}:
                prefix_node = child
        if prefix_node is None or list_node is None:
            return
        prefix_parts = _collect_use_prefix(prefix_node, source)
        for child in list_node.children:
            if child.type in {"{", "}", ","}:
                continue
            for suffix, alias in _expand_use_node(child, source):
                yield (prefix_parts + suffix, alias)
        return


def _collect_use_prefix(node: Node, source: bytes) -> List[str]:
    if node.type == "scoped_use_list":
        items = list(_expand_use_node(node, source))
        return items[0][0][:-1] if items else []
    if node.type == "scoped_identifier":
        return _collect_scoped_parts(node, source)
    if node.type in {"identifier", "type_identifier", "self", "super", "crate"}:
        return [_node_text(node, source)]
    return []


def _resolve_use_path(
    use_path: Sequence[str],
    current_module: Sequence[str],
    internal_roots: Set[str],
) -> str:
    if not use_path:
        return ""

    parts = list(use_path)
    head = parts[0]
    if head == "crate":
        resolved = parts[1:]
    elif head == "self":
        resolved = [*current_module, *parts[1:]]
    elif head == "super":
        resolved = [*current_module[:-1], *parts[1:]]
    elif head in internal_roots:
        resolved = parts
    else:
        return ""

    if resolved and resolved[-1] == "*":
        return ""
    return ".".join(part for part in resolved if part)
