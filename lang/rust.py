"""Rust language frontend — tree-sitter based extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from lang.base import LanguageFrontend, register_frontend


def _get_parser():
    from tree_sitter_language_pack import get_parser
    return get_parser("rust")


def _parse_file(file_path: Path) -> Any:
    """Parse a Rust file and return the tree-sitter tree."""
    source = file_path.read_bytes()
    parser = _get_parser()
    return parser.parse(source), source


def _node_text(node, source: bytes) -> str:
    """Extract text of a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_children_by_type(node, type_name: str) -> list:
    """Find all direct children of a specific type."""
    return [c for c in node.children if c.type == type_name]


def _find_child_by_type(node, type_name: str):
    """Find first direct child of a specific type."""
    for c in node.children:
        if c.type == type_name:
            return c
    return None


def _extract_identifier(node, source: bytes) -> str:
    """Extract the identifier name from a node."""
    ident = _find_child_by_type(node, "identifier")
    if ident is None:
        ident = _find_child_by_type(node, "type_identifier")
    if ident is None:
        return ""
    return _node_text(ident, source)


def _is_async(node) -> bool:
    """Check if a function node has async modifier."""
    mods = _find_child_by_type(node, "function_modifiers")
    if mods:
        for c in mods.children:
            if c.type == "async":
                return True
    return False


def _is_public(node) -> bool:
    """Check if a node has pub visibility."""
    return _find_child_by_type(node, "visibility_modifier") is not None


def _extract_parameters(node, source: bytes) -> List[Dict[str, str]]:
    """Extract parameter names and types from a function's parameters node."""
    params_node = _find_child_by_type(node, "parameters")
    if params_node is None:
        return []
    params = []
    for child in params_node.children:
        if child.type == "parameter":
            ident = _find_child_by_type(child, "identifier")
            name = _node_text(ident, source) if ident else "?"
            # Type is everything after the ':'
            type_str = "?"
            colon_seen = False
            for c in child.children:
                if c.type == ":":
                    colon_seen = True
                elif colon_seen:
                    type_str = _node_text(c, source)
                    break
            params.append({"name": name, "type": type_str})
        elif child.type == "self_parameter":
            # Skip self/&self/&mut self
            pass
    return params


def _extract_return_type(node, source: bytes) -> Optional[str]:
    """Extract return type from a function node."""
    # Return type follows '->' token
    arrow_seen = False
    for child in node.children:
        if child.type == "->":
            arrow_seen = True
        elif arrow_seen:
            if child.type == "block":
                break
            return _node_text(child, source)
    return None


def _has_self_param(node) -> bool:
    """Check if a function has a self parameter (making it a method)."""
    params = _find_child_by_type(node, "parameters")
    if params is None:
        return False
    for child in params.children:
        if child.type == "self_parameter":
            return True
    return False


# ---------------------------------------------------------------------------
# Semantic analysis helpers
# ---------------------------------------------------------------------------

def _collect_calls(node, source: bytes) -> Set[str]:
    """Recursively collect function/method call names from a subtree."""
    calls: Set[str] = set()
    _collect_calls_recursive(node, source, calls)
    return calls


def _collect_calls_recursive(node, source: bytes, calls: Set[str]) -> None:
    """Walk tree collecting call expressions."""
    if node.type == "call_expression":
        func = node.children[0] if node.children else None
        if func:
            call_name = _extract_call_name(func, source)
            if call_name:
                calls.add(call_name)
    elif node.type == "macro_invocation":
        ident = _find_child_by_type(node, "identifier")
        if ident:
            calls.add(_node_text(ident, source))

    for child in node.children:
        _collect_calls_recursive(child, source, calls)


def _extract_call_name(node, source: bytes) -> str:
    """Extract a readable call name from a call expression's function node."""
    if node.type == "identifier":
        return _node_text(node, source)
    if node.type == "scoped_identifier":
        # e.g., HashMap::new -> HashMap.new
        parts = []
        for c in node.children:
            if c.type in ("identifier", "type_identifier"):
                parts.append(_node_text(c, source))
        return ".".join(parts[-2:]) if len(parts) >= 2 else ".".join(parts)
    if node.type == "field_expression":
        # e.g., self.name.method() -> name.method or method
        parts = []
        current = node
        while current.type == "field_expression":
            ident = _find_child_by_type(current, "field_identifier")
            if ident:
                parts.append(_node_text(ident, source))
            current = current.children[0] if current.children else current
            if current.type == "field_expression":
                continue
            elif current.type == "identifier":
                name = _node_text(current, source)
                if name != "self":
                    parts.append(name)
            break
        parts.reverse()
        return ".".join(parts[-2:]) if len(parts) >= 2 else ".".join(parts) if parts else ""
    return ""


def _collect_flags(node, source: bytes) -> str:
    """Collect behavioral flags from a function body."""
    flags: Set[str] = set()
    _collect_flags_recursive(node, source, flags)
    return "".join(sorted(flags))


def _collect_flags_recursive(node, source: bytes, flags: Set[str]) -> None:
    """Walk tree collecting behavioral signal flags."""
    ntype = node.type

    if ntype == "if_expression":
        flags.add("I")
    elif ntype in ("for_expression", "while_expression", "loop_expression"):
        flags.add("L")
    elif ntype == "return_expression":
        flags.add("R")
    elif ntype == "match_expression":
        flags.add("I")  # match is conditional branching
    elif ntype in ("try_expression",):
        flags.add("T")
    elif ntype == "await_expression":
        flags.add("A")
    elif ntype == "macro_invocation":
        ident = _find_child_by_type(node, "identifier")
        if ident:
            name = _node_text(ident, source)
            if name in ("panic", "unreachable", "unimplemented", "todo"):
                flags.add("E")  # error/panic
    elif ntype == "call_expression":
        # Check for ? operator (error propagation) on the result
        pass
    elif ntype == "try_expression":
        flags.add("T")

    # Check for ? operator (error propagation)
    if ntype == "try_expression":
        flags.add("T")

    for child in node.children:
        _collect_flags_recursive(child, source, flags)


def _count_assignments(node) -> int:
    """Count let bindings and assignments in a subtree."""
    count = 0
    if node.type in ("let_declaration", "assignment_expression", "compound_assignment_expr"):
        count += 1
    for child in node.children:
        count += _count_assignments(child)
    return count


def _semantic_summary(node, source: bytes) -> Dict[str, Any]:
    """Compute semantic summary for an entity node."""
    calls = sorted(_collect_calls(node, source))
    flags = _collect_flags(node, source)
    assigns = _count_assignments(node)
    bases: List[str] = []

    # For impl blocks, extract trait if it's a trait impl
    # This is handled at the entity level, not here

    return {
        "calls": calls,
        "flags": flags,
        "assigns": assigns,
        "bases": bases,
        "type_sig": {"param_types": [], "return_type": None},
    }


def _extract_type_sig(node, source: bytes) -> Dict[str, Any]:
    """Extract type signature from a function node."""
    params = _extract_parameters(node, source)
    param_types = [p["type"] for p in params]
    return_type = _extract_return_type(node, source)
    return {"param_types": param_types, "return_type": return_type}


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def _extract_entities(
    tree, source: bytes, include_semantic: bool = True,
) -> List[Dict[str, Any]]:
    """Walk tree-sitter tree and extract Rust entities."""
    entities: List[Dict[str, Any]] = []
    root = tree.root_node

    for child in root.children:
        _visit_top_level(child, source, entities, [], include_semantic)

    return entities


def _visit_top_level(
    node, source: bytes, entities: List[Dict[str, Any]],
    scope: List[str], include_semantic: bool,
) -> None:
    """Visit a top-level node and extract entities."""
    ntype = node.type

    if ntype == "function_item":
        _extract_function(node, source, entities, scope, include_semantic)

    elif ntype == "struct_item":
        _extract_struct(node, source, entities, scope, include_semantic)

    elif ntype == "enum_item":
        _extract_enum(node, source, entities, scope, include_semantic)

    elif ntype == "trait_item":
        _extract_trait(node, source, entities, scope, include_semantic)

    elif ntype == "impl_item":
        _extract_impl(node, source, entities, scope, include_semantic)

    elif ntype == "const_item":
        _extract_const(node, source, entities, scope, include_semantic, kind="constant")

    elif ntype == "static_item":
        _extract_const(node, source, entities, scope, include_semantic, kind="static")

    elif ntype == "mod_item":
        _extract_mod(node, source, entities, scope, include_semantic)


def _extract_function(
    node, source: bytes, entities: List[Dict[str, Any]],
    scope: List[str], include_semantic: bool,
) -> None:
    """Extract a function_item entity."""
    name = _extract_identifier(node, source)
    if not name:
        return

    is_method = _has_self_param(node)
    is_async_fn = _is_async(node)

    if is_method:
        kind = "async_method" if is_async_fn else "method"
    else:
        kind = "async_function" if is_async_fn else "function"

    qualified_name = ".".join([*scope, name]) if scope else name

    entry: Dict[str, Any] = {
        "kind": kind,
        "name": name,
        "qualified_name": qualified_name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
    }

    if include_semantic:
        sem = _semantic_summary(node, source)
        sem["type_sig"] = _extract_type_sig(node, source)
        entry["semantic"] = sem

    entities.append(entry)


def _extract_struct(
    node, source: bytes, entities: List[Dict[str, Any]],
    scope: List[str], include_semantic: bool,
) -> None:
    """Extract a struct_item entity."""
    name = _extract_identifier(node, source)
    if not name:
        return

    qualified_name = ".".join([*scope, name]) if scope else name

    entry: Dict[str, Any] = {
        "kind": "struct",
        "name": name,
        "qualified_name": qualified_name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
    }

    if include_semantic:
        sem = _semantic_summary(node, source)
        # Extract derive attributes
        derives = _extract_derives(node, source)
        if derives:
            sem["bases"] = derives
        entry["semantic"] = sem

    entities.append(entry)


def _extract_enum(
    node, source: bytes, entities: List[Dict[str, Any]],
    scope: List[str], include_semantic: bool,
) -> None:
    """Extract an enum_item entity."""
    name = _extract_identifier(node, source)
    if not name:
        return

    qualified_name = ".".join([*scope, name]) if scope else name

    entry: Dict[str, Any] = {
        "kind": "enum",
        "name": name,
        "qualified_name": qualified_name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
    }

    if include_semantic:
        sem = _semantic_summary(node, source)
        derives = _extract_derives(node, source)
        if derives:
            sem["bases"] = derives
        entry["semantic"] = sem

    entities.append(entry)


def _extract_trait(
    node, source: bytes, entities: List[Dict[str, Any]],
    scope: List[str], include_semantic: bool,
) -> None:
    """Extract a trait_item entity and its methods."""
    name = _extract_identifier(node, source)
    if not name:
        return

    qualified_name = ".".join([*scope, name]) if scope else name

    entry: Dict[str, Any] = {
        "kind": "trait",
        "name": name,
        "qualified_name": qualified_name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
    }

    if include_semantic:
        sem = _semantic_summary(node, source)
        # Check for supertraits
        supertraits = _extract_supertraits(node, source)
        if supertraits:
            sem["bases"] = supertraits
        entry["semantic"] = sem

    entities.append(entry)

    # Extract trait methods
    decl_list = _find_child_by_type(node, "declaration_list")
    if decl_list:
        trait_scope = [*scope, name]
        for child in decl_list.children:
            if child.type == "function_item":
                _extract_function(child, source, entities, trait_scope, include_semantic)
            elif child.type == "function_signature_item":
                _extract_trait_signature(child, source, entities, trait_scope, include_semantic)


def _extract_trait_signature(
    node, source: bytes, entities: List[Dict[str, Any]],
    scope: List[str], include_semantic: bool,
) -> None:
    """Extract a trait method signature (no body)."""
    name = _extract_identifier(node, source)
    if not name:
        return

    is_method = _has_self_param(node)
    kind = "method" if is_method else "function"
    qualified_name = ".".join([*scope, name])

    entry: Dict[str, Any] = {
        "kind": kind,
        "name": name,
        "qualified_name": qualified_name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
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


def _extract_impl(
    node, source: bytes, entities: List[Dict[str, Any]],
    scope: List[str], include_semantic: bool,
) -> None:
    """Extract methods from an impl block.

    For `impl Foo`, methods are scoped as Foo.method_name.
    For `impl Trait for Foo`, methods are scoped as Foo.method_name,
    and get the trait recorded in bases.
    """
    # Determine the target type name and optional trait
    impl_type = ""
    impl_trait = ""

    # Check if this is `impl Trait for Type`
    has_for = any(c.type == "for" for c in node.children)

    if has_for:
        # impl Trait for Type — find trait and type
        before_for = True
        for child in node.children:
            if child.type == "for":
                before_for = False
                continue
            if child.type in ("impl", "declaration_list", "visibility_modifier",
                              "type_parameters", "where_clause"):
                continue
            if before_for:
                if child.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                    impl_trait = _node_text(child, source)
            else:
                if child.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                    impl_type = _node_text(child, source)
    else:
        # impl Type — find the type
        for child in node.children:
            if child.type in ("impl", "declaration_list", "visibility_modifier",
                              "type_parameters", "where_clause"):
                continue
            if child.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                impl_type = _node_text(child, source)
                break

    if not impl_type:
        return

    # Clean up generic parameters from type name
    if "<" in impl_type:
        impl_type = impl_type[:impl_type.index("<")]

    impl_scope = [*scope, impl_type]

    decl_list = _find_child_by_type(node, "declaration_list")
    if decl_list is None:
        return

    for child in decl_list.children:
        if child.type == "function_item":
            _extract_function(child, source, entities, impl_scope, include_semantic)
            # If this is a trait impl, record the trait in bases
            if include_semantic and impl_trait and entities:
                last = entities[-1]
                sem = last.get("semantic", {})
                if impl_trait not in sem.get("bases", []):
                    sem.setdefault("bases", []).append(impl_trait)
        elif child.type == "const_item":
            _extract_const(child, source, entities, impl_scope, include_semantic, kind="constant")


def _extract_const(
    node, source: bytes, entities: List[Dict[str, Any]],
    scope: List[str], include_semantic: bool, kind: str = "constant",
) -> None:
    """Extract a const or static item."""
    name = _extract_identifier(node, source)
    if not name:
        return

    qualified_name = ".".join([*scope, name]) if scope else name

    entry: Dict[str, Any] = {
        "kind": kind,
        "name": name,
        "qualified_name": qualified_name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
    }

    if include_semantic:
        entry["semantic"] = {
            "calls": [],
            "flags": "",
            "assigns": 0,
            "bases": [],
            "type_sig": {"param_types": [], "return_type": None},
        }

    entities.append(entry)


def _extract_mod(
    node, source: bytes, entities: List[Dict[str, Any]],
    scope: List[str], include_semantic: bool,
) -> None:
    """Extract an inline mod block and recurse into its contents."""
    name = _extract_identifier(node, source)
    if not name:
        return

    # Only extract inline modules (with a body block), not `mod foo;` declarations
    decl_list = _find_child_by_type(node, "declaration_list")
    if decl_list is None:
        return

    mod_scope = [*scope, name]
    for child in decl_list.children:
        _visit_top_level(child, source, entities, mod_scope, include_semantic)


def _extract_derives(node, source: bytes) -> List[str]:
    """Extract derive macro arguments (e.g., #[derive(Debug, Clone)])."""
    derives = []
    # Look for attribute_item siblings that precede this node
    # In tree-sitter, attributes are sibling nodes before the item
    parent = node.parent
    if parent is None:
        return derives

    found_node = False
    for sibling in reversed(parent.children):
        if sibling is node:
            found_node = True
            continue
        if found_node and sibling.type == "attribute_item":
            text = _node_text(sibling, source)
            if "derive(" in text:
                # Parse #[derive(X, Y, Z)]
                start = text.index("derive(") + 7
                end = text.rindex(")")
                inner = text[start:end]
                for d in inner.split(","):
                    d = d.strip()
                    if d:
                        derives.append(d)
        elif found_node and sibling.type != "attribute_item":
            break  # Stop when we hit a non-attribute

    return derives


def _extract_supertraits(node, source: bytes) -> List[str]:
    """Extract supertrait bounds from a trait definition."""
    # trait Foo: Bar + Baz { ... }
    # The supertraits appear as a trait_bounds node
    traits = []
    for child in node.children:
        if child.type == "trait_bounds":
            for bound_child in child.children:
                if bound_child.type in ("type_identifier", "scoped_type_identifier"):
                    traits.append(_node_text(bound_child, source))
                elif bound_child.type == "generic_type":
                    ident = _find_child_by_type(bound_child, "type_identifier")
                    if ident:
                        traits.append(_node_text(ident, source))
    return traits


# ---------------------------------------------------------------------------
# Use / import extraction
# ---------------------------------------------------------------------------

def _extract_use_paths(tree, source: bytes) -> List[str]:
    """Extract use paths as module-level import names."""
    imports = []
    root = tree.root_node
    for child in root.children:
        if child.type == "use_declaration":
            _collect_use_paths(child, source, imports)
    return sorted(set(imports))


def _collect_use_paths(node, source: bytes, imports: List[str]) -> None:
    """Recursively collect import paths from a use declaration."""
    for child in node.children:
        if child.type == "scoped_identifier":
            path = _scoped_path(child, source)
            if path:
                imports.append(path[0])
        elif child.type == "scoped_use_list":
            # e.g., std::io::{Read, Write}
            scope_ident = _find_child_by_type(child, "scoped_identifier")
            if scope_ident:
                path = _scoped_path(scope_ident, source)
                if path:
                    imports.append(path[0])
            else:
                ident = _find_child_by_type(child, "identifier")
                if ident:
                    imports.append(_node_text(ident, source))
        elif child.type == "identifier":
            imports.append(_node_text(child, source))


def _scoped_path(node, source: bytes) -> List[str]:
    """Extract the full path segments from a scoped_identifier.

    Returns the segments as a list, e.g., ['std', 'collections', 'HashMap'].
    """
    parts = []
    current = node
    while current.type == "scoped_identifier":
        last = None
        first_child = None
        for c in current.children:
            if c.type in ("identifier", "type_identifier", "super", "crate", "self"):
                if first_child is None:
                    first_child = c
                last = c
        if last:
            parts.append(_node_text(last, source))
        # Descend into the nested scoped_identifier
        nested = _find_child_by_type(current, "scoped_identifier")
        if nested:
            current = nested
        else:
            # We're at the root — add the first identifier
            if first_child:
                parts.append(_node_text(first_child, source))
            break

    parts.reverse()
    return parts


def _build_rust_import_map(tree, source: bytes) -> Dict[str, str]:
    """Build a map of locally bound names to their full paths from use statements.

    e.g., use std::collections::HashMap -> {"HashMap": "std.collections.HashMap"}
    """
    import_map: Dict[str, str] = {}
    root = tree.root_node
    for child in root.children:
        if child.type == "use_declaration":
            _collect_import_map_entries(child, source, import_map, [])
    return import_map


def _collect_import_map_entries(
    node, source: bytes, import_map: Dict[str, str], prefix: List[str],
) -> None:
    """Recursively collect import map entries from use declarations."""
    for child in node.children:
        if child.type == "scoped_identifier":
            path = _scoped_path(child, source)
            if path:
                local_name = path[-1]
                qualified = ".".join(path)
                import_map[local_name] = qualified
        elif child.type == "scoped_use_list":
            # e.g., std::io::{Read, Write}
            # Get the scope prefix
            scope_parts: List[str] = []
            scope_ident = _find_child_by_type(child, "scoped_identifier")
            if scope_ident:
                scope_parts = _scoped_path(scope_ident, source)
            else:
                ident = _find_child_by_type(child, "identifier")
                if ident:
                    scope_parts = [_node_text(ident, source)]

            use_list = _find_child_by_type(child, "use_list")
            if use_list:
                for item in use_list.children:
                    if item.type == "identifier":
                        name = _node_text(item, source)
                        qualified = ".".join([*scope_parts, name])
                        import_map[name] = qualified
                    elif item.type == "scoped_identifier":
                        sub_path = _scoped_path(item, source)
                        if sub_path:
                            local_name = sub_path[-1]
                            qualified = ".".join([*scope_parts, *sub_path])
                            import_map[local_name] = qualified
                    elif item.type == "self":
                        # use std::io::{self} -> import io
                        if scope_parts:
                            local_name = scope_parts[-1]
                            qualified = ".".join(scope_parts)
                            import_map[local_name] = qualified
        elif child.type == "use_as_clause":
            # use foo::bar as baz
            alias = None
            path = []
            for c in child.children:
                if c.type == "identifier" and alias is None:
                    # Could be the alias (after "as") or part of path
                    pass
                if c.type == "scoped_identifier":
                    path = _scoped_path(c, source)
            # Find the "as" keyword and the alias after it
            as_seen = False
            for c in child.children:
                if c.type == "as":
                    as_seen = True
                elif as_seen and c.type == "identifier":
                    alias = _node_text(c, source)
            if path:
                local_name = alias if alias else path[-1]
                import_map[local_name] = ".".join(path)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Rust file classification rules
_RUST_FILENAME_RULES = {
    "lib.rs": "core_logic",
    "main.rs": "core_logic",
    "mod.rs": "init",
    "build.rs": "config",
    "config.rs": "config",
    "errors.rs": "exceptions",
    "error.rs": "exceptions",
}

_RUST_DIR_KEYWORDS = {
    "tests": "tests",
    "test": "tests",
    "testing": "tests",
    "benches": "tests",
    "examples": "utils",
    "config": "config",
    "models": "schema",
    "schemas": "schema",
    "migrations": "schema",
}

# Rust domain classification by filename patterns
_RUST_DOMAIN_FILE_PATTERNS = {
    "http": "http", "client": "http", "server": "http", "api": "http",
    "request": "http", "response": "http", "transport": "http",
    "auth": "auth", "login": "auth", "oauth": "auth", "token": "auth",
    "crypto": "crypto", "hash": "crypto", "signing": "crypto",
    "db": "db", "database": "db", "orm": "db", "query": "db",
    "storage": "fs", "file": "fs", "upload": "fs",
    "cli": "cli", "args": "cli", "commands": "cli",
    "parser": "parse", "codec": "parse", "encoding": "parse",
    "serializer": "parse", "serde": "parse",
}

# Rust crate-level domain signals
_RUST_DOMAIN_CRATES = {
    "reqwest": "http", "hyper": "http", "actix_web": "http", "axum": "http",
    "warp": "http", "rocket": "http", "surf": "http", "ureq": "http",
    "jsonwebtoken": "auth", "oauth2": "auth",
    "ring": "crypto", "rustls": "crypto", "openssl": "crypto",
    "diesel": "db", "sqlx": "db", "rusqlite": "db", "sea_orm": "db",
    "tokio": "async", "async_std": "async",
    "serde": "parse", "serde_json": "parse", "toml": "parse",
    "clap": "cli", "structopt": "cli",
    "std.net": "net", "mio": "net",
    "std.fs": "fs", "tempfile": "fs",
}


def _classify_rust_file(file_path: Path, source: Optional[str] = None) -> str:
    """Classify a Rust file into a module category."""
    filename = file_path.name

    # 1. Filename rules
    if filename in _RUST_FILENAME_RULES:
        return _RUST_FILENAME_RULES[filename]

    stem = file_path.stem

    # Test files
    if stem.startswith("test_") or stem.endswith("_test") or stem.endswith("_tests"):
        return "tests"

    # 2. Directory rules
    for part in file_path.parts:
        lower = part.lower()
        if lower in _RUST_DIR_KEYWORDS:
            return _RUST_DIR_KEYWORDS[lower]

    # 3. Content-based analysis
    if source:
        # Check for #[cfg(test)] modules
        if "#[cfg(test)]" in source:
            # Only classify as tests if the ENTIRE file is test-oriented
            non_test_fn = False
            for line in source.split("\n"):
                stripped = line.strip()
                if stripped.startswith("pub fn ") or stripped.startswith("fn "):
                    if "#[test]" not in source[:source.index(stripped)]:
                        non_test_fn = True
                        break
            if not non_test_fn:
                return "tests"

        # Check for mostly type definitions (schema)
        struct_count = source.count("struct ")
        enum_count = source.count("enum ")
        fn_count = source.count("fn ")
        if (struct_count + enum_count) >= 3 and fn_count <= 1:
            return "schema"

        # Check for route/handler patterns
        route_attrs = source.count("#[get(") + source.count("#[post(") + \
                      source.count("#[put(") + source.count("#[delete(") + \
                      source.count("#[patch(")
        if route_attrs >= 2:
            return "router"

        # Constants-only file
        const_count = source.count("const ") + source.count("static ")
        if const_count >= 3 and fn_count == 0 and struct_count == 0:
            return "constants"

    return "core_logic"


def _classify_rust_domain(file_path: Path, source: Optional[str] = None) -> str:
    """Classify a Rust file by functional domain."""
    stem = file_path.stem.lower()
    if stem in _RUST_DOMAIN_FILE_PATTERNS:
        return _RUST_DOMAIN_FILE_PATTERNS[stem]

    for part in file_path.parts:
        lower = part.lower()
        if lower in _RUST_DOMAIN_FILE_PATTERNS:
            return _RUST_DOMAIN_FILE_PATTERNS[lower]

    # Check use statements for crate signals
    if source:
        scores: Dict[str, int] = {}
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("use "):
                crate_name = stripped[4:].split("::")[0].rstrip(";").strip()
                crate_name = crate_name.replace("-", "_")
                if crate_name in _RUST_DOMAIN_CRATES:
                    domain = _RUST_DOMAIN_CRATES[crate_name]
                    scores[domain] = scores.get(domain, 0) + 2

        if scores:
            return max(scores, key=scores.get)

    return "unknown"


# ---------------------------------------------------------------------------
# Frontend implementation
# ---------------------------------------------------------------------------

@register_frontend
class RustFrontend(LanguageFrontend):

    @property
    def name(self) -> str:
        return "rust"

    @property
    def extensions(self) -> List[str]:
        return [".rs"]

    def discover_package_roots(self, repo_path: Path) -> Set[str]:
        """Discover Rust crate roots.

        Looks for Cargo.toml and src/ directory structure.
        Returns crate names (from directory names containing src/ or Cargo.toml).
        """
        roots: Set[str] = set()

        # Check if this is a single-crate repo
        if (repo_path / "Cargo.toml").exists():
            if (repo_path / "src").is_dir():
                roots.add("src")
            roots.add(repo_path.name)

        # Check for workspace members (subdirectories with Cargo.toml)
        for child in repo_path.iterdir():
            if child.is_dir() and (child / "Cargo.toml").exists():
                roots.add(child.name)
            if child.is_dir() and (child / "src").is_dir():
                roots.add(child.name)

        return roots

    def parse_bare_entities(self, file_path: Path) -> List[Dict[str, Any]]:
        try:
            tree, source = _parse_file(file_path)
        except Exception:
            return []
        return _extract_entities(tree, source, include_semantic=False)

    def parse_entities(self, file_path: Path) -> List[Dict[str, Any]]:
        try:
            tree, source = _parse_file(file_path)
        except Exception:
            return []
        return _extract_entities(tree, source, include_semantic=True)

    def classify_file(self, file_path: Path, source: Optional[str] = None) -> str:
        if source is None:
            try:
                source = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                source = ""
        return _classify_rust_file(file_path, source)

    def classify_domain(self, file_path: Path, source: Optional[str] = None) -> str:
        if source is None:
            try:
                source = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                source = ""
        return _classify_rust_domain(file_path, source)

    def extract_imports(self, file_path: Path, source: Optional[str] = None) -> List[str]:
        try:
            tree, source_bytes = _parse_file(file_path)
        except Exception:
            return []
        return _extract_use_paths(tree, source_bytes)

    def split_imports(
        self, all_imports: List[str], package_roots: Set[str],
    ) -> Tuple[List[str], List[str]]:
        """Split imports into internal and external.

        Internal: crate, self, super, or names matching package roots.
        External: std, known third-party crates.
        """
        internal = sorted({
            n for n in all_imports
            if n in package_roots or n in ("crate", "self", "super")
        })
        external = sorted({n for n in all_imports if n not in internal})
        return internal, external

    def build_import_map(
        self, file_path: Path, repo_path: Path, source: Optional[str] = None,
    ) -> Dict[str, str]:
        try:
            tree, source_bytes = _parse_file(file_path)
        except Exception:
            return {}
        return _build_rust_import_map(tree, source_bytes)
