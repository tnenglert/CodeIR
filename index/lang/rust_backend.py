"""Rust language backend using tree-sitter for parsing and entity extraction."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import tree_sitter_rust
from tree_sitter import Language, Parser

RUST_LANGUAGE = Language(tree_sitter_rust.language())

# ---------------------------------------------------------------------------
# Rust-specific call stoplist
# ---------------------------------------------------------------------------

RUST_CALL_STOPLIST: Set[str] = {
    # Extremely common methods/functions
    "new", "from", "into", "clone", "default", "fmt", "drop",
    "deref", "deref_mut", "as_ref", "as_mut",
    "unwrap", "expect", "unwrap_or", "unwrap_or_else",
    "ok", "err", "is_ok", "is_err", "is_some", "is_none",
    "map", "and_then", "or_else", "map_err", "ok_or", "ok_or_else",
    "iter", "into_iter", "collect", "filter", "find", "any", "all",
    "len", "is_empty", "push", "pop", "insert", "remove", "contains",
    "get", "set", "entry", "or_insert", "or_insert_with",
    "to_string", "to_owned", "as_str", "as_bytes",
    "write", "read", "flush", "close",
    "lock", "try_lock",
    "spawn", "join",
    "eq", "ne", "cmp", "partial_cmp", "hash",
    "display", "debug",
    # Common macros treated as calls
    "println", "eprintln", "format", "vec", "todo", "unimplemented",
    "assert", "assert_eq", "assert_ne", "debug_assert",
    "write", "writeln",
}

# ---------------------------------------------------------------------------
# Domain classification signals
# ---------------------------------------------------------------------------

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
    "error": "unknown", "errors": "unknown",
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
    "log": "unknown", "tracing": "unknown", "env_logger": "unknown",
    "thiserror": "unknown", "anyhow": "unknown",
}

# ---------------------------------------------------------------------------
# Module classification
# ---------------------------------------------------------------------------

_FILENAME_RULES: List[tuple] = [
    (lambda p: p.name in ("mod.rs", "lib.rs"), "init"),
    (lambda p: p.name == "main.rs", "core_logic"),
    (lambda p: p.name == "build.rs", "config"),
    (lambda p: p.name in ("config.rs", "settings.rs", "configuration.rs"), "config"),
    (lambda p: p.name in ("error.rs", "errors.rs", "err.rs"), "exceptions"),
    (lambda p: p.name in ("constants.rs", "consts.rs"), "constants"),
    (lambda p: p.name in ("schema.rs", "models.rs", "types.rs"), "schema"),
]

_DIR_KEYWORDS: Dict[str, str] = {
    "tests": "tests",
    "test": "tests",
    "testing": "tests",
    "benches": "tests",
    "examples": "utils",
    "config": "config",
    "schemas": "schema",
    "models": "schema",
}


class RustBackend:
    language: str = "rust"
    extensions: List[str] = [".rs"]

    def __init__(self) -> None:
        self._parser = Parser(RUST_LANGUAGE)

    def parse_file(self, path: Path) -> Any:
        try:
            source = path.read_bytes()
            return self._parser.parse(source)
        except (OSError, UnicodeDecodeError):
            return None

    def extract_entities(self, path: Path, include_semantic: bool = True) -> List[dict]:
        tree = self.parse_file(path)
        if tree is None:
            return []
        source = path.read_bytes()
        return _extract_entities_from_tree(tree, source, include_semantic)

    def extract_imports(self, tree: Any, file_path: Optional[Path] = None) -> List[str]:
        if tree is None:
            return []
        return _extract_use_crates(tree)

    def classify_file(self, file_path: Path, tree: Any) -> str:
        return _classify_rust_file(file_path, tree)

    def classify_domain(self, file_path: Path, tree: Any) -> str:
        return _classify_rust_domain(file_path, tree)

    def discover_package_roots(self, repo_path: Path) -> Set[str]:
        return _discover_crate_roots(repo_path)

    def split_imports(self, all_imports: List[str], package_roots: Set[str]) -> Tuple[List[str], List[str]]:
        internal = sorted({n for n in all_imports if n in package_roots or n == "crate" or n == "super" or n == "self"})
        external = sorted({n for n in all_imports if n not in package_roots and n not in ("crate", "super", "self")})
        return internal, external

    def build_import_map(self, tree: Any, file_path: Path, repo_path: Path) -> Dict[str, str]:
        if tree is None:
            return {}
        return _build_rust_import_map(tree)

    def get_call_stoplist(self) -> Set[str]:
        return RUST_CALL_STOPLIST


# ---------------------------------------------------------------------------
# Tree-sitter helpers
# ---------------------------------------------------------------------------

def _node_text(node, source: bytes) -> str:
    """Extract text of a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_children(node, type_name: str):
    """Find all direct children of a given type."""
    return [c for c in node.children if c.type == type_name]


def _find_child(node, type_name: str):
    """Find first direct child of a given type."""
    for c in node.children:
        if c.type == type_name:
            return c
    return None


def _walk_descendants(node):
    """Yield all descendant nodes (DFS)."""
    stack = list(node.children)
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def _extract_entities_from_tree(tree, source: bytes, include_semantic: bool) -> List[dict]:
    """Walk the tree-sitter AST and extract entities."""
    entities: List[dict] = []
    root = tree.root_node

    for node in root.children:
        if node.type == "function_item":
            _extract_function(node, source, entities, scope=[], include_semantic=include_semantic)
        elif node.type == "struct_item":
            _extract_struct(node, source, entities, include_semantic=include_semantic)
        elif node.type == "enum_item":
            _extract_enum(node, source, entities, include_semantic=include_semantic)
        elif node.type == "trait_item":
            _extract_trait(node, source, entities, include_semantic=include_semantic)
        elif node.type == "impl_item":
            _extract_impl(node, source, entities, include_semantic=include_semantic)
        elif node.type == "const_item":
            _extract_const(node, source, entities, include_semantic=include_semantic)
        elif node.type == "static_item":
            _extract_const(node, source, entities, include_semantic=include_semantic)

    return entities


def _get_identifier(node, source: bytes) -> str:
    """Get the identifier name from a node."""
    ident = _find_child(node, "identifier") or _find_child(node, "type_identifier")
    if ident:
        return _node_text(ident, source)
    return ""


def _is_async(node) -> bool:
    """Check if a function_item is async."""
    mods = _find_child(node, "function_modifiers")
    if mods:
        for c in mods.children:
            if c.type == "async":
                return True
    return False


def _extract_function(node, source: bytes, entities: List[dict], scope: List[str],
                      include_semantic: bool, is_trait_def: bool = False) -> None:
    """Extract a function or method entity."""
    name = _get_identifier(node, source)
    if not name:
        return

    is_async_fn = _is_async(node)

    if is_trait_def:
        kind = "trait_method"
    elif scope:
        kind = "async_method" if is_async_fn else "method"
    else:
        kind = "async_function" if is_async_fn else "function"

    qualified_name = ".".join([*scope, name]) if scope else name
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1

    entry: dict = {
        "kind": kind,
        "name": name,
        "qualified_name": qualified_name,
        "start_line": start_line,
        "end_line": end_line,
    }

    if include_semantic:
        entry["semantic"] = _semantic_summary(node, source)

    entities.append(entry)


def _extract_struct(node, source: bytes, entities: List[dict], include_semantic: bool) -> None:
    name = _get_identifier(node, source)
    if not name:
        return

    entry: dict = {
        "kind": "struct",
        "name": name,
        "qualified_name": name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
    }

    if include_semantic:
        derives = _extract_derive_macros(node, source)
        entry["semantic"] = {
            "calls": sorted(derives),
            "flags": "",
            "assigns": _count_fields(node),
            "bases": sorted(derives),
            "type_sig": {"param_types": [], "return_type": None},
        }

    entities.append(entry)


def _extract_enum(node, source: bytes, entities: List[dict], include_semantic: bool) -> None:
    name = _get_identifier(node, source)
    if not name:
        return

    entry: dict = {
        "kind": "enum",
        "name": name,
        "qualified_name": name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
    }

    if include_semantic:
        derives = _extract_derive_macros(node, source)
        variant_count = _count_enum_variants(node)
        entry["semantic"] = {
            "calls": sorted(derives),
            "flags": "",
            "assigns": variant_count,
            "bases": sorted(derives),
            "type_sig": {"param_types": [], "return_type": None},
        }

    entities.append(entry)


def _extract_trait(node, source: bytes, entities: List[dict], include_semantic: bool) -> None:
    name = _get_identifier(node, source)
    if not name:
        return

    entry: dict = {
        "kind": "trait",
        "name": name,
        "qualified_name": name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
    }

    if include_semantic:
        method_names: List[str] = []
        decl = _find_child(node, "declaration_list")
        if decl:
            for child in decl.children:
                if child.type == "function_item":
                    fn_name = _get_identifier(child, source)
                    if fn_name:
                        method_names.append(fn_name)
        entry["semantic"] = {
            "calls": sorted(method_names),
            "flags": "",
            "assigns": len(method_names),
            "bases": [],
            "type_sig": {"param_types": [], "return_type": None},
        }

    entities.append(entry)

    # Also extract individual trait methods (both signatures and default implementations)
    decl = _find_child(node, "declaration_list")
    if decl:
        for child in decl.children:
            if child.type == "function_item":
                _extract_function(child, source, entities, scope=[name],
                                  include_semantic=include_semantic, is_trait_def=True)
            elif child.type == "function_signature_item":
                _extract_function_signature(child, source, entities, scope=[name],
                                            include_semantic=include_semantic)


def _extract_impl(node, source: bytes, entities: List[dict], include_semantic: bool) -> None:
    """Extract methods from an impl block.

    For `impl Foo { ... }`, methods are qualified as Foo.method_name.
    For `impl Trait for Foo { ... }`, methods are still Foo.method_name,
    but the trait name is recorded in semantic metadata.
    """
    # Determine the target type and optional trait
    type_node = None
    trait_name = ""

    # Check for trait impl: `impl Trait for Type { ... }`
    for_node = _find_child(node, "for")
    if for_node:
        # Pattern: impl <trait> for <type> { ... }
        # The trait is the type_identifier before 'for', type is after
        children_types = [(c.type, c) for c in node.children]
        saw_impl = False
        for ctype, cnode in children_types:
            if ctype == "impl":
                saw_impl = True
            elif saw_impl and ctype == "type_identifier" and not trait_name:
                trait_name = _node_text(cnode, source)
            elif ctype == "for":
                pass
            elif ctype == "type_identifier" and trait_name:
                type_node = cnode
                break
            elif ctype == "generic_type" and trait_name:
                type_node = _find_child(cnode, "type_identifier")
                break
    else:
        # Pattern: impl <type> { ... }
        for c in node.children:
            if c.type == "type_identifier":
                type_node = c
                break
            elif c.type == "generic_type":
                type_node = _find_child(c, "type_identifier")
                break

    if type_node is None:
        return

    type_name = _node_text(type_node, source)

    # Extract methods from declaration_list
    decl = _find_child(node, "declaration_list")
    if not decl:
        return

    for child in decl.children:
        if child.type == "function_item":
            _extract_function(child, source, entities, scope=[type_name],
                              include_semantic=include_semantic)
            # Tag the last entity with trait info if this is a trait impl
            if trait_name and include_semantic and entities:
                last = entities[-1]
                sem = last.get("semantic", {})
                if sem and trait_name not in sem.get("bases", []):
                    sem.setdefault("bases", []).append(trait_name)
                    sem["bases"] = sorted(sem["bases"])


def _extract_function_signature(node, source: bytes, entities: List[dict], scope: List[str],
                                include_semantic: bool) -> None:
    """Extract a trait method signature (no body)."""
    name = _get_identifier(node, source)
    if not name:
        return

    qualified_name = ".".join([*scope, name]) if scope else name
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1

    entry: dict = {
        "kind": "trait_method",
        "name": name,
        "qualified_name": qualified_name,
        "start_line": start_line,
        "end_line": end_line,
    }

    if include_semantic:
        entry["semantic"] = {
            "calls": [],
            "flags": "",
            "assigns": 0,
            "bases": [],
            "type_sig": _extract_type_sig(node, source),
        }

    entities.append(entry)


def _extract_const(node, source: bytes, entities: List[dict], include_semantic: bool) -> None:
    name = _get_identifier(node, source)
    if not name:
        return

    entry: dict = {
        "kind": "constant",
        "name": name,
        "qualified_name": name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
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


# ---------------------------------------------------------------------------
# Semantic analysis
# ---------------------------------------------------------------------------

def _semantic_summary(node, source: bytes) -> dict:
    """Extract semantic information from a function/method body."""
    calls: Set[str] = set()
    flags: Set[str] = set()
    assign_count = 0

    body = _find_child(node, "block")
    if body is None:
        # Trait method signature without body
        return {
            "calls": [],
            "flags": "",
            "assigns": 0,
            "bases": [],
            "type_sig": _extract_type_sig(node, source),
        }

    for descendant in _walk_descendants(body):
        dt = descendant.type

        if dt == "call_expression":
            call_name = _extract_call_name(descendant, source)
            if call_name:
                calls.add(call_name)

        elif dt == "macro_invocation":
            macro_name = _get_identifier(descendant, source)
            if macro_name:
                calls.add(macro_name)
            if macro_name in ("panic", "todo", "unimplemented"):
                flags.add("E")

        elif dt == "if_expression":
            flags.add("I")
        elif dt == "match_expression":
            flags.add("I")

        elif dt in ("for_expression", "while_expression", "loop_expression"):
            flags.add("L")

        elif dt == "return_expression":
            flags.add("R")

        elif dt == "try_expression":
            flags.add("E")

        elif dt == "unsafe_block":
            flags.add("U")

        elif dt == "await_expression":
            flags.add("A")

        elif dt == "closure_expression":
            flags.add("W")

        elif dt == "let_declaration":
            assign_count += 1

    # Check for .unwrap() / .expect() calls as error flags
    for call in list(calls):
        if call in ("unwrap", "expect"):
            flags.add("E")

    return {
        "calls": sorted(calls),
        "flags": "".join(sorted(flags)),
        "assigns": assign_count,
        "bases": [],
        "type_sig": _extract_type_sig(node, source),
    }


def _extract_call_name(node, source: bytes) -> str:
    """Extract the function/method name from a call_expression node.

    Handles:
    - foo(args) -> "foo"
    - Foo::new(args) -> "Foo.new"
    - self.method(args) -> "method"
    - obj.method(args) -> "obj.method"
    - module::func(args) -> "func"
    """
    func_node = node.children[0] if node.children else None
    if func_node is None:
        return ""

    if func_node.type == "identifier":
        return _node_text(func_node, source)

    if func_node.type == "scoped_identifier":
        # a::b::c -> extract last two segments
        parts = []
        for c in func_node.children:
            if c.type in ("identifier", "type_identifier"):
                parts.append(_node_text(c, source))
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}"
        return parts[-1] if parts else ""

    if func_node.type == "field_expression":
        # obj.method or self.helper.method
        parts = []
        current = func_node
        while current and current.type == "field_expression":
            field = _find_child(current, "field_identifier")
            if field:
                parts.append(_node_text(field, source))
            # Move to the left-hand side
            current = current.children[0] if current.children else None

        parts.reverse()

        # Strip 'self' prefix
        if current and current.type == "self":
            pass  # already stripped by not including it
        elif current and current.type == "identifier":
            parts.insert(0, _node_text(current, source))

        # Return last 2 segments max
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}"
        return parts[0] if parts else ""

    if func_node.type == "generic_function":
        # func::<T>(args) — extract inner function name
        inner = func_node.children[0] if func_node.children else None
        if inner:
            if inner.type == "identifier":
                return _node_text(inner, source)
            if inner.type == "scoped_identifier":
                parts = [_node_text(c, source) for c in inner.children
                         if c.type in ("identifier", "type_identifier")]
                if len(parts) >= 2:
                    return f"{parts[-2]}.{parts[-1]}"
                return parts[-1] if parts else ""
            if inner.type == "field_expression":
                return _extract_call_name_from_field(inner, source)

    return ""


def _extract_call_name_from_field(node, source: bytes) -> str:
    """Extract call name from a field_expression (for generic_function case)."""
    field = _find_child(node, "field_identifier")
    if field:
        return _node_text(field, source)
    return ""


def _extract_type_sig(node, source: bytes) -> dict:
    """Extract parameter types and return type from a function signature."""
    param_types: List[str] = []
    return_type: Optional[str] = None

    params = _find_child(node, "parameters")
    if params:
        for c in params.children:
            if c.type == "parameter":
                # Find the type annotation
                type_node = None
                for sub in c.children:
                    if sub.type not in ("identifier", ":", ",", "mut", "mutable_specifier",
                                        "pattern", "reference_pattern", "tuple_pattern"):
                        type_node = sub
                if type_node:
                    param_types.append(_node_text(type_node, source))
            # Skip self_parameter

    # Find return type (the node after "->")
    saw_arrow = False
    for c in node.children:
        if c.type == "->":
            saw_arrow = True
        elif saw_arrow and c.type != "block":
            return_type = _node_text(c, source)
            break

    return {"param_types": param_types, "return_type": return_type}


def _extract_derive_macros(node, source: bytes) -> List[str]:
    """Extract derive macro names from attributes on a struct/enum."""
    derives: List[str] = []
    # Look for attribute_item siblings before the struct/enum
    # In tree-sitter, attributes are children of the parent
    # Actually, they are direct children of the node itself for structs
    for c in node.children:
        if c.type == "attribute_item":
            text = _node_text(c, source)
            # Parse #[derive(Foo, Bar)]
            m = re.search(r'derive\(([^)]+)\)', text)
            if m:
                for name in m.group(1).split(","):
                    name = name.strip()
                    if name:
                        derives.append(name)
    return derives


def _count_fields(node) -> int:
    """Count struct fields."""
    field_list = _find_child(node, "field_declaration_list")
    if field_list:
        return len(_find_children(field_list, "field_declaration"))
    return 0


def _count_enum_variants(node) -> int:
    """Count enum variants."""
    variant_list = _find_child(node, "enum_variant_list")
    if variant_list:
        return len(_find_children(variant_list, "enum_variant"))
    return 0


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------

def _extract_use_crates(tree) -> List[str]:
    """Extract root crate names from use declarations."""
    crates: Set[str] = set()
    root = tree.root_node

    for node in root.children:
        if node.type == "use_declaration":
            _extract_crate_from_use(node, crates)

    return sorted(crates)


def _extract_crate_from_use(node, crates: Set[str]) -> None:
    """Extract the root crate name from a single use declaration."""
    for child in node.children:
        if child.type == "scoped_identifier":
            # use foo::bar::baz -> "foo"
            first_ident = None
            for c in child.children:
                if c.type in ("identifier", "type_identifier", "crate", "super", "self"):
                    text = c.text.decode("utf-8") if c.text else ""
                    first_ident = text
                    break
            if first_ident:
                crates.add(first_ident)
            return
        elif child.type == "scoped_use_list":
            # use foo::{bar, baz} -> "foo"
            first = None
            for c in child.children:
                if c.type in ("identifier", "scoped_identifier"):
                    if c.type == "identifier":
                        first = c.text.decode("utf-8") if c.text else ""
                    else:
                        for sub in c.children:
                            if sub.type in ("identifier", "type_identifier", "crate", "super"):
                                first = sub.text.decode("utf-8") if sub.text else ""
                                break
                    break
            if first:
                crates.add(first)
            return
        elif child.type == "identifier":
            crates.add(child.text.decode("utf-8") if child.text else "")
            return
        elif child.type == "use_as_clause":
            # use foo as bar -> extract "foo"
            for c in child.children:
                if c.type == "identifier":
                    crates.add(c.text.decode("utf-8") if c.text else "")
                    return
                elif c.type == "scoped_identifier":
                    for sub in c.children:
                        if sub.type in ("identifier", "type_identifier", "crate", "super"):
                            crates.add(sub.text.decode("utf-8") if sub.text else "")
                            return


def _build_rust_import_map(tree) -> Dict[str, str]:
    """Build import map from use declarations.

    Maps the locally bound name to its fully qualified path.
    e.g., `use crate::config::AppConfig;` -> {"AppConfig": "crate.config.AppConfig"}
    """
    import_map: Dict[str, str] = {}
    root = tree.root_node

    for node in root.children:
        if node.type == "use_declaration":
            _process_use_for_import_map(node, import_map)

    return import_map


def _process_use_for_import_map(node, import_map: Dict[str, str]) -> None:
    """Process a single use declaration into the import map."""
    for child in node.children:
        if child.type == "scoped_identifier":
            # use foo::bar::Baz -> Baz maps to foo.bar.Baz
            parts = _scoped_id_parts(child)
            if parts:
                local_name = parts[-1]
                qualified = ".".join(parts)
                import_map[local_name] = qualified

        elif child.type == "scoped_use_list":
            # use foo::bar::{Baz, Qux}
            _process_scoped_use_list(child, import_map)

        elif child.type == "use_as_clause":
            # use foo::Bar as Baz
            _process_use_as(child, import_map)

        elif child.type == "identifier":
            name = child.text.decode("utf-8") if child.text else ""
            if name:
                import_map[name] = name


def _scoped_id_parts(node) -> List[str]:
    """Extract path segments from a scoped_identifier."""
    parts: List[str] = []
    for c in node.children:
        if c.type in ("identifier", "type_identifier", "crate", "super", "self"):
            parts.append(c.text.decode("utf-8") if c.text else "")
        elif c.type == "scoped_identifier":
            parts.extend(_scoped_id_parts(c))
    return parts


def _process_scoped_use_list(node, import_map: Dict[str, str]) -> None:
    """Process `use prefix::{A, B, C}`."""
    # Find the prefix (scoped_identifier or identifier before the use_list)
    prefix_parts: List[str] = []
    for c in node.children:
        if c.type == "scoped_identifier":
            prefix_parts = _scoped_id_parts(c)
        elif c.type == "identifier":
            prefix_parts = [c.text.decode("utf-8") if c.text else ""]
        elif c.type == "use_list":
            for item in c.children:
                if item.type == "identifier":
                    name = item.text.decode("utf-8") if item.text else ""
                    if name:
                        qualified = ".".join(prefix_parts + [name])
                        import_map[name] = qualified
                elif item.type == "type_identifier":
                    name = item.text.decode("utf-8") if item.text else ""
                    if name:
                        qualified = ".".join(prefix_parts + [name])
                        import_map[name] = qualified
                elif item.type == "scoped_identifier":
                    parts = _scoped_id_parts(item)
                    if parts:
                        local_name = parts[-1]
                        qualified = ".".join(prefix_parts + parts)
                        import_map[local_name] = qualified
                elif item.type == "use_as_clause":
                    _process_use_as(item, import_map, prefix=prefix_parts)
            break


def _process_use_as(node, import_map: Dict[str, str], prefix: Optional[List[str]] = None) -> None:
    """Process `use foo::Bar as Baz`."""
    prefix = prefix or []
    original_parts: List[str] = []
    alias = ""

    for c in node.children:
        if c.type in ("identifier", "type_identifier"):
            if not original_parts:
                original_parts.append(c.text.decode("utf-8") if c.text else "")
            elif c.type == "identifier" and not alias:
                alias = c.text.decode("utf-8") if c.text else ""
        elif c.type == "scoped_identifier":
            original_parts = _scoped_id_parts(c)
        elif c.type == "as":
            pass

    # In `use_as_clause`, the pattern is: path `as` alias
    # We need to find the identifier after 'as'
    saw_as = False
    for c in node.children:
        if c.type == "as":
            saw_as = True
        elif saw_as and c.type == "identifier":
            alias = c.text.decode("utf-8") if c.text else ""
            break

    if alias and original_parts:
        qualified = ".".join(prefix + original_parts)
        import_map[alias] = qualified
    elif original_parts:
        local_name = original_parts[-1]
        qualified = ".".join(prefix + original_parts)
        import_map[local_name] = qualified


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify_rust_file(file_path: Path, tree) -> str:
    """Classify a Rust source file into a module category."""
    # 1. Filename rules
    for rule_fn, category in _FILENAME_RULES:
        if rule_fn(file_path):
            return category

    # 2. Directory patterns
    for part in file_path.parts:
        lower = part.lower()
        if lower in _DIR_KEYWORDS:
            return _DIR_KEYWORDS[lower]

    # 3. AST-based classification
    if tree is not None:
        return _classify_by_rust_ast(tree)

    return "core_logic"


def _classify_by_rust_ast(tree) -> str:
    """Classify using tree-sitter AST structural signals."""
    root = tree.root_node
    struct_count = 0
    enum_count = 0
    fn_count = 0
    impl_count = 0
    test_fn_count = 0
    has_cfg_test = False
    derive_serde_count = 0
    route_attr_count = 0

    for node in root.children:
        if node.type == "struct_item":
            struct_count += 1
            if _has_serde_derive(node):
                derive_serde_count += 1
        elif node.type == "enum_item":
            enum_count += 1
            if _has_serde_derive(node):
                derive_serde_count += 1
        elif node.type == "function_item":
            fn_count += 1
            if _has_test_attribute(node):
                test_fn_count += 1
            if _has_route_attribute(node):
                route_attr_count += 1
        elif node.type == "impl_item":
            impl_count += 1
        elif node.type == "attribute_item":
            text = node.text.decode("utf-8", errors="replace") if node.text else ""
            if "cfg(test)" in text:
                has_cfg_test = True

    # Test module
    if has_cfg_test or test_fn_count >= 2:
        return "tests"

    # Schema-heavy (serde derives on structs/enums)
    total_types = struct_count + enum_count
    if derive_serde_count >= 2 and derive_serde_count >= total_types * 0.5:
        return "schema"

    # Router (route attributes)
    if route_attr_count >= 2:
        return "router"

    # Constants-only
    const_count = sum(1 for n in root.children if n.type in ("const_item", "static_item"))
    if const_count >= 3 and fn_count == 0 and struct_count == 0:
        return "constants"

    return "core_logic"


def _has_serde_derive(node) -> bool:
    """Check if a struct/enum has #[derive(Serialize)] or #[derive(Deserialize)]."""
    for c in node.children:
        if c.type == "attribute_item":
            text = c.text.decode("utf-8", errors="replace") if c.text else ""
            if "Serialize" in text or "Deserialize" in text:
                return True
    return False


def _has_test_attribute(node) -> bool:
    """Check if a function has #[test] or #[tokio::test]."""
    for c in node.children:
        if c.type == "attribute_item":
            text = c.text.decode("utf-8", errors="replace") if c.text else ""
            if "test" in text.lower():
                return True
    return False


def _has_route_attribute(node) -> bool:
    """Check for route attributes (#[get], #[post], etc.)."""
    route_names = {"get", "post", "put", "patch", "delete", "head", "options"}
    for c in node.children:
        if c.type == "attribute_item":
            text = c.text.decode("utf-8", errors="replace") if c.text else ""
            text_lower = text.lower()
            for route in route_names:
                if f"#{route}" in text_lower or f"#[{route}" in text_lower:
                    return True
    return False


def _classify_rust_domain(file_path: Path, tree) -> str:
    """Classify a Rust file by domain."""
    # 1. Filename
    stem = file_path.stem.lower()
    if stem in _DOMAIN_FILE_PATTERNS:
        return _DOMAIN_FILE_PATTERNS[stem]

    # 2. Directory parts
    for part in file_path.parts:
        lower = part.lower()
        if lower in _DOMAIN_FILE_PATTERNS:
            return _DOMAIN_FILE_PATTERNS[lower]

    # 3. Crate imports
    if tree is not None:
        crates = _extract_use_crates(tree)
        scores: Dict[str, int] = {}
        for crate_name in crates:
            if crate_name in _DOMAIN_CRATES_STRONG:
                domain = _DOMAIN_CRATES_STRONG[crate_name]
                scores[domain] = scores.get(domain, 0) + 2
            elif crate_name in _DOMAIN_CRATES_WEAK:
                domain = _DOMAIN_CRATES_WEAK[crate_name]
                scores[domain] = scores.get(domain, 0) + 1

        qualifying = {d: s for d, s in scores.items() if s >= 2 and d != "unknown"}
        if qualifying:
            return max(qualifying, key=qualifying.get)

    return "unknown"


# ---------------------------------------------------------------------------
# Package root discovery
# ---------------------------------------------------------------------------

def _discover_crate_roots(repo_path: Path) -> Set[str]:
    """Find internal crate names from Cargo.toml files."""
    roots: Set[str] = set()

    cargo_path = repo_path / "Cargo.toml"
    if cargo_path.exists():
        try:
            content = cargo_path.read_text(encoding="utf-8")
            # Simple regex to extract package name
            m = re.search(r'^\s*name\s*=\s*"([^"]+)"', content, re.MULTILINE)
            if m:
                crate_name = m.group(1).replace("-", "_")
                roots.add(crate_name)
        except OSError:
            pass

    # Also check for workspace members
    if cargo_path.exists():
        try:
            content = cargo_path.read_text(encoding="utf-8")
            # Find workspace members
            for m in re.finditer(r'members\s*=\s*\[([^\]]+)\]', content, re.DOTALL):
                for member_match in re.finditer(r'"([^"]+)"', m.group(1)):
                    member_path = repo_path / member_match.group(1) / "Cargo.toml"
                    if member_path.exists():
                        member_content = member_path.read_text(encoding="utf-8")
                        name_match = re.search(r'^\s*name\s*=\s*"([^"]+)"', member_content, re.MULTILINE)
                        if name_match:
                            roots.add(name_match.group(1).replace("-", "_"))
        except OSError:
            pass

    # Always include "crate" and "super" as internal
    roots.add("crate")
    roots.add("super")

    return roots
