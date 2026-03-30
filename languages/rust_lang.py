"""Rust language support for CodeIR.

Uses tree-sitter-rust for parsing. Extracts functions, methods, structs,
enums, traits, constants, and their semantic signals.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import tree_sitter as ts
import tree_sitter_rust as tsrust

from languages import LanguageSupport, register_language

# ---------------------------------------------------------------------------
# Tree-sitter setup
# ---------------------------------------------------------------------------

_RUST_LANGUAGE = ts.Language(tsrust.language())


def _make_parser() -> ts.Parser:
    return ts.Parser(_RUST_LANGUAGE)


# ---------------------------------------------------------------------------
# Call stoplist — Rust builtins and common methods too generic for caller resolution
# ---------------------------------------------------------------------------

RUST_CALL_STOPLIST: Set[str] = {
    # Common constructors / conversions
    "new", "default", "from", "into", "clone", "to_string", "to_owned",
    "as_ref", "as_mut", "as_slice", "as_str", "as_bytes",
    # Result/Option combinators
    "unwrap", "expect", "ok", "err", "map", "and_then", "or_else",
    "unwrap_or", "unwrap_or_else", "unwrap_or_default", "map_err",
    "is_some", "is_none", "is_ok", "is_err",
    # Iterator methods
    "iter", "into_iter", "iter_mut", "collect", "filter", "fold",
    "map", "flat_map", "for_each", "any", "all", "find", "position",
    "enumerate", "zip", "chain", "take", "skip", "count",
    # Vec / collection methods
    "push", "pop", "len", "is_empty", "insert", "remove", "contains",
    "get", "set", "clear", "extend", "drain", "retain", "sort",
    "sort_by", "sort_by_key", "reverse", "first", "last",
    # String methods
    "format", "to_lowercase", "to_uppercase", "trim", "split",
    "starts_with", "ends_with", "contains", "replace", "chars", "bytes",
    # IO / display
    "println", "eprintln", "print", "eprint", "write", "writeln",
    "read", "read_to_string", "flush",
    # Memory / pointer
    "drop", "forget", "size_of", "align_of", "transmute",
    # Debug / display
    "fmt", "debug", "display",
    # Common trait methods
    "eq", "ne", "cmp", "partial_cmp", "hash",
    # Constructors that are too generic
    "Some", "None", "Ok", "Err",
    # Type conversions
    "parse", "try_from", "try_into",
}

# ---------------------------------------------------------------------------
# Node text helpers
# ---------------------------------------------------------------------------

def _node_text(node, source: bytes) -> str:
    """Extract UTF-8 text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child(node, type_name: str):
    """Find first direct child of a given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_children(node, type_name: str) -> list:
    """Find all direct children of a given type."""
    return [c for c in node.children if c.type == type_name]


def _has_child_type(node, type_name: str) -> bool:
    return any(c.type == type_name for c in node.children)


def _is_pub(node) -> bool:
    """Check if a node has a visibility_modifier child (pub)."""
    return _has_child_type(node, "visibility_modifier")


def _is_async(node) -> bool:
    """Check if a function node is async.

    In tree-sitter-rust, async functions have a function_modifiers child
    containing 'async', not a direct 'async' child.
    """
    for child in node.children:
        if child.type == "function_modifiers":
            for gc in child.children:
                if gc.type == "async":
                    return True
        if child.type == "async":
            return True
    return False


# ---------------------------------------------------------------------------
# Semantic analysis — walk a subtree collecting behavioral signals
# ---------------------------------------------------------------------------

def _extract_calls_and_flags(node, source: bytes) -> dict:
    """Walk a tree-sitter subtree extracting calls and behavioral flags."""
    calls: Set[str] = set()
    flags: Set[str] = set()
    assign_count = 0

    def _walk(n):
        nonlocal assign_count

        ntype = n.type

        # Calls
        if ntype == "call_expression":
            func_node = n.children[0] if n.children else None
            if func_node:
                call_name = _extract_call_name(func_node, source)
                if call_name:
                    calls.add(call_name)

        # Macro invocations (println!, vec!, etc.)
        elif ntype == "macro_invocation":
            macro_name_node = _find_child(n, "identifier")
            if macro_name_node:
                calls.add(_node_text(macro_name_node, source))

        # Conditionals
        elif ntype == "if_expression":
            flags.add("I")
        elif ntype == "match_expression":
            flags.add("I")

        # Loops
        elif ntype in ("for_expression", "while_expression", "loop_expression"):
            flags.add("L")

        # Return
        elif ntype == "return_expression":
            flags.add("R")

        # Error propagation (? operator)
        elif ntype == "try_expression":
            flags.add("E")

        # Await
        elif ntype == "await_expression":
            flags.add("A")

        # Unsafe
        elif ntype == "unsafe_block":
            flags.add("U")

        # Assignments (let bindings, assignment expressions)
        elif ntype == "let_declaration":
            assign_count += 1
        elif ntype == "assignment_expression":
            assign_count += 1
        elif ntype == "compound_assignment_expr":
            assign_count += 1

        for child in n.children:
            _walk(child)

    _walk(node)
    return {
        "calls": sorted(calls),
        "flags": "".join(sorted(flags)),
        "assigns": assign_count,
    }


def _extract_call_name(func_node, source: bytes) -> str:
    """Extract a call name from the function part of a call_expression.

    Simple call: foo() → "foo"
    Scoped call: bar::baz() → "bar.baz"
    Method call: self.method() → "method"
    Field call:  obj.method() → "obj.method"
    Nested:      SomeStruct::new() → "SomeStruct.new"
    """
    ntype = func_node.type

    if ntype == "identifier":
        return _node_text(func_node, source)

    if ntype == "scoped_identifier":
        parts = _collect_scoped_parts(func_node, source)
        # Strip crate/self/super prefixes
        while parts and parts[0] in ("crate", "self", "super", "Self"):
            parts = parts[1:]
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return parts[0] if parts else ""

    if ntype == "field_expression":
        # obj.method or self.method
        obj_node = func_node.children[0] if func_node.children else None
        field_node = _find_child(func_node, "field_identifier")
        if field_node:
            field_name = _node_text(field_node, source)
            if obj_node and obj_node.type == "self":
                return field_name
            elif obj_node and obj_node.type == "identifier":
                obj_name = _node_text(obj_node, source)
                return "%s.%s" % (obj_name, field_name)
            return field_name

    return ""


def _collect_scoped_parts(node, source: bytes) -> List[str]:
    """Collect all identifier parts from a scoped_identifier chain."""
    parts = []
    current = node
    while current.type == "scoped_identifier":
        children = current.children
        # scoped_identifier has: left :: right
        right = children[-1] if children else None
        left = children[0] if children else None
        if right and right.type in ("identifier", "type_identifier"):
            parts.append(_node_text(right, source))
        current = left
        if current is None:
            break
    if current and current.type in ("identifier", "type_identifier", "crate", "self", "super"):
        parts.append(_node_text(current, source))
    parts.reverse()
    return parts


# ---------------------------------------------------------------------------
# Type signature extraction
# ---------------------------------------------------------------------------

def _extract_type_signature(node, source: bytes) -> dict:
    """Extract parameter types and return type from a function_item or function_signature_item."""
    param_types: List[str] = []
    return_type: Optional[str] = None

    params_node = _find_child(node, "parameters")
    if params_node:
        for param in params_node.children:
            if param.type == "parameter":
                type_node = _find_child(param, "type_identifier") or \
                           _find_child(param, "reference_type") or \
                           _find_child(param, "generic_type") or \
                           _find_child(param, "primitive_type") or \
                           _find_child(param, "scoped_type_identifier") or \
                           _find_child(param, "array_type") or \
                           _find_child(param, "tuple_type")
                if type_node:
                    param_types.append(_node_text(type_node, source))
                else:
                    # Try to find any type child after ':'
                    found_colon = False
                    for child in param.children:
                        if child.type == ":":
                            found_colon = True
                        elif found_colon:
                            param_types.append(_node_text(child, source))
                            break
                    else:
                        param_types.append("?")
            elif param.type == "self_parameter":
                pass  # skip self/&self/&mut self

    # Return type: look for the type after ->
    found_arrow = False
    for child in node.children:
        if child.type == "->":
            found_arrow = True
        elif found_arrow and child.type not in ("->", "block", "where_clause"):
            return_type = _node_text(child, source)
            break

    return {"param_types": param_types, "return_type": return_type}


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def _extract_entities_from_tree(
    tree, source: bytes, include_semantic: bool = True,
) -> List[dict]:
    """Walk the tree-sitter tree and extract entities."""
    entities: List[dict] = []
    root = tree.root_node

    def _process_function(node, scope: List[str], is_async: bool = False):
        name_node = _find_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)

        # Determine kind
        if scope:
            kind = "async_method" if is_async else "method"
        else:
            kind = "async_function" if is_async else "function"

        qualified_name = ".".join([*scope, name]) if scope else name
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        entry = {
            "kind": kind,
            "name": name,
            "qualified_name": qualified_name,
            "start_line": start_line,
            "end_line": end_line,
        }

        if include_semantic:
            sem = _extract_calls_and_flags(node, source)
            sem["bases"] = []
            sem["type_sig"] = _extract_type_signature(node, source)
            # Check for async
            if is_async:
                sem["flags"] = "".join(sorted(set(sem["flags"]) | {"A"}))
            entry["semantic"] = sem

        entities.append(entry)

    def _process_struct(node, scope: List[str]):
        name_node = _find_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qualified_name = ".".join([*scope, name]) if scope else name
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        entry = {
            "kind": "struct",
            "name": name,
            "qualified_name": qualified_name,
            "start_line": start_line,
            "end_line": end_line,
        }

        if include_semantic:
            # Extract derive traits as "bases"
            bases = _extract_derives(node, source)
            sem = _extract_calls_and_flags(node, source)
            sem["bases"] = bases
            sem["type_sig"] = {"param_types": [], "return_type": None}
            entry["semantic"] = sem

        entities.append(entry)

    def _process_enum(node, scope: List[str]):
        name_node = _find_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qualified_name = ".".join([*scope, name]) if scope else name
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        entry = {
            "kind": "enum",
            "name": name,
            "qualified_name": qualified_name,
            "start_line": start_line,
            "end_line": end_line,
        }

        if include_semantic:
            bases = _extract_derives(node, source)
            sem = _extract_calls_and_flags(node, source)
            sem["bases"] = bases
            sem["type_sig"] = {"param_types": [], "return_type": None}
            # Mark error-like enums
            if _looks_like_error_enum(name, bases):
                sem["flags"] = "".join(sorted(set(sem["flags"]) | {"X"}))
            entry["semantic"] = sem

        entities.append(entry)

    def _process_trait(node, scope: List[str]):
        name_node = _find_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qualified_name = ".".join([*scope, name]) if scope else name
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        entry = {
            "kind": "trait",
            "name": name,
            "qualified_name": qualified_name,
            "start_line": start_line,
            "end_line": end_line,
        }

        if include_semantic:
            # Trait supertraits as bases
            bases = _extract_trait_bounds(node, source)
            sem = _extract_calls_and_flags(node, source)
            sem["bases"] = bases
            sem["type_sig"] = {"param_types": [], "return_type": None}
            entry["semantic"] = sem

        entities.append(entry)

        # Extract trait method signatures
        decl_list = _find_child(node, "declaration_list")
        if decl_list:
            for child in decl_list.children:
                if child.type == "function_item":
                    is_async = _is_async(child)
                    _process_function(child, [name], is_async)
                elif child.type == "function_signature_item":
                    _process_trait_method_sig(child, [name])

    def _process_trait_method_sig(node, scope: List[str]):
        """Extract a trait method signature (no body)."""
        name_node = _find_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qualified_name = ".".join([*scope, name]) if scope else name
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        entry = {
            "kind": "method",
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
                "type_sig": _extract_type_signature(node, source),
            }

        entities.append(entry)

    def _process_const(node, scope: List[str]):
        name_node = _find_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qualified_name = ".".join([*scope, name]) if scope else name
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        entry = {
            "kind": "constant",
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
                "type_sig": {"param_types": [], "return_type": None},
            }

        entities.append(entry)

    def _process_impl(node):
        """Process an impl block — extract the type name as scope for methods."""
        # impl Type { ... } or impl Trait for Type { ... }
        type_name = _extract_impl_type_name(node, source)
        if not type_name:
            return

        decl_list = _find_child(node, "declaration_list")
        if not decl_list:
            return

        for child in decl_list.children:
            if child.type == "function_item":
                is_async = _is_async(child)
                _process_function(child, [type_name], is_async)
            elif child.type == "const_item":
                _process_const(child, [type_name])

    def _visit_top_level(node):
        for child in node.children:
            ntype = child.type
            if ntype == "function_item":
                is_async = _is_async(child)
                _process_function(child, [], is_async)
            elif ntype == "struct_item":
                _process_struct(child, [])
            elif ntype == "enum_item":
                _process_enum(child, [])
            elif ntype == "trait_item":
                _process_trait(child, [])
            elif ntype == "impl_item":
                _process_impl(child)
            elif ntype == "const_item":
                _process_const(child, [])
            elif ntype == "static_item":
                _process_const(child, [])  # treat statics like constants
            elif ntype == "mod_item":
                # Inline module: mod foo { ... }
                mod_name_node = _find_child(child, "identifier")
                decl_list = _find_child(child, "declaration_list")
                if mod_name_node and decl_list:
                    # Recurse into inline module — entities get module-prefixed names
                    # but for simplicity we treat them as top-level for now
                    _visit_top_level(decl_list)

    _visit_top_level(root)
    return entities


def _extract_impl_type_name(node, source: bytes) -> str:
    """Extract the type name from an impl block.

    impl User { ... } → "User"
    impl Trait for User { ... } → "User"
    impl<T> Display for MyType<T> { ... } → "MyType"
    """
    children = node.children
    has_for = any(c.type == "for" for c in children)

    if has_for:
        # impl Trait for Type — find the type after 'for'
        found_for = False
        for child in children:
            if child.type == "for":
                found_for = True
            elif found_for and child.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
                if child.type == "generic_type":
                    ti = _find_child(child, "type_identifier")
                    return _node_text(ti, source) if ti else _node_text(child, source)
                if child.type == "scoped_type_identifier":
                    # Get the last identifier
                    parts = _collect_scoped_parts(child, source)
                    return parts[-1] if parts else _node_text(child, source)
                return _node_text(child, source)
    else:
        # impl Type — find the type_identifier or generic_type after 'impl'
        found_impl = False
        for child in children:
            if child.type == "impl":
                found_impl = True
            elif found_impl and child.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
                if child.type == "generic_type":
                    ti = _find_child(child, "type_identifier")
                    return _node_text(ti, source) if ti else _node_text(child, source)
                if child.type == "scoped_type_identifier":
                    parts = _collect_scoped_parts(child, source)
                    return parts[-1] if parts else _node_text(child, source)
                return _node_text(child, source)
            elif found_impl and child.type == "type_parameters":
                continue  # skip <T> generics

    return ""


def _extract_derives(node, source: bytes) -> List[str]:
    """Extract #[derive(...)] trait names from attributes on a struct/enum.

    In tree-sitter-rust, attributes are preceding siblings of the struct/enum
    node, not children. We walk backwards from the node's position in its
    parent's children list.
    """
    derives: List[str] = []

    # First check direct children (some versions nest them)
    for child in node.children:
        if child.type == "attribute_item":
            _collect_derive_names(child, source, derives)

    # Then check preceding siblings in the parent
    if node.parent:
        found = False
        for sibling in reversed(node.parent.children):
            if sibling.id == node.id:
                found = True
                continue
            if found:
                if sibling.type == "attribute_item":
                    _collect_derive_names(sibling, source, derives)
                else:
                    break  # stop at first non-attribute

    return sorted(set(derives))


def _collect_derive_names(attr_node, source: bytes, out: List[str]):
    """Extract derive names from an attribute_item node."""
    text = _node_text(attr_node, source)
    m = re.search(r"derive\(([^)]+)\)", text)
    if m:
        for name in m.group(1).split(","):
            name = name.strip()
            if name:
                out.append(name)


def _extract_trait_bounds(node, source: bytes) -> List[str]:
    """Extract supertrait bounds from a trait definition.

    trait Foo: Bar + Baz { ... } → ["Bar", "Baz"]
    """
    bounds: List[str] = []
    # Look for trait_bounds node
    for child in node.children:
        if child.type == "trait_bounds":
            for bound_child in child.children:
                if bound_child.type == "type_identifier":
                    bounds.append(_node_text(bound_child, source))
                elif bound_child.type == "scoped_type_identifier":
                    parts = _collect_scoped_parts(bound_child, source)
                    if parts:
                        bounds.append(parts[-1])
    return sorted(bounds)


def _looks_like_error_enum(name: str, derives: List[str]) -> bool:
    """Check if an enum looks like an error type."""
    lower = name.lower()
    if lower.endswith("error") or lower.endswith("err"):
        return True
    if "Error" in derives or "thiserror" in " ".join(derives).lower():
        return True
    return False


# ---------------------------------------------------------------------------
# Use/import extraction
# ---------------------------------------------------------------------------

def _extract_use_declarations(tree, source: bytes) -> List[dict]:
    """Extract all use declarations from the tree.

    Returns list of dicts with:
        path: list of path segments (e.g., ["std", "collections", "HashMap"])
        names: list of imported names (for use lists)
        alias: optional alias name
    """
    uses: List[dict] = []

    for child in tree.root_node.children:
        if child.type == "use_declaration":
            _extract_use_paths(child, source, uses)

    return uses


def _extract_use_paths(node, source: bytes, out: List[dict]):
    """Recursively extract paths from a use_declaration."""
    for child in node.children:
        if child.type == "use_as_clause":
            # use foo::bar as baz;
            path_node = child.children[0] if child.children else None
            alias_node = _find_child(child, "identifier")
            if path_node:
                parts = _collect_scoped_parts(path_node, source) if path_node.type == "scoped_identifier" else [_node_text(path_node, source)]
                alias = _node_text(alias_node, source) if alias_node else None
                out.append({"path": parts, "names": [parts[-1]] if parts else [], "alias": alias})

        elif child.type == "scoped_identifier":
            parts = _collect_scoped_parts(child, source)
            out.append({"path": parts, "names": [parts[-1]] if parts else [], "alias": None})

        elif child.type == "scoped_use_list":
            # use foo::bar::{A, B};
            prefix_parts = []
            use_list = None
            for sc in child.children:
                if sc.type == "scoped_identifier":
                    prefix_parts = _collect_scoped_parts(sc, source)
                elif sc.type in ("identifier", "crate", "self", "super"):
                    prefix_parts = [_node_text(sc, source)]
                elif sc.type == "use_list":
                    use_list = sc

            if use_list:
                for item in use_list.children:
                    if item.type == "identifier":
                        name = _node_text(item, source)
                        out.append({"path": prefix_parts + [name], "names": [name], "alias": None})
                    elif item.type == "scoped_identifier":
                        parts = _collect_scoped_parts(item, source)
                        out.append({"path": prefix_parts + parts, "names": [parts[-1]] if parts else [], "alias": None})
                    elif item.type == "use_as_clause":
                        path_node = item.children[0] if item.children else None
                        alias_node = None
                        for ic in item.children:
                            if ic.type == "identifier" and ic != item.children[0]:
                                alias_node = ic
                        if path_node:
                            name = _node_text(path_node, source)
                            alias = _node_text(alias_node, source) if alias_node else None
                            out.append({"path": prefix_parts + [name], "names": [name], "alias": alias})

        elif child.type == "use_wildcard":
            # use foo::*; — we can't resolve individual names, skip
            pass

        elif child.type == "identifier":
            # use foo;
            name = _node_text(child, source)
            out.append({"path": [name], "names": [name], "alias": None})


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Filename-based rules for Rust
_FILENAME_RULES = [
    (lambda p: p.name == "mod.rs", "init"),
    (lambda p: p.name == "lib.rs", "init"),
    (lambda p: p.name == "main.rs", "core_logic"),
    (lambda p: p.name == "build.rs", "config"),
    (lambda p: p.name in ("config.rs", "settings.rs", "conf.rs", "configuration.rs"), "config"),
    (lambda p: p.name in ("errors.rs", "error.rs"), "exceptions"),
    (lambda p: p.name in ("constants.rs", "consts.rs", "const.rs"), "constants"),
]

_DIR_KEYWORDS = {
    "tests": "tests",
    "test": "tests",
    "testing": "tests",
    "benches": "tests",
    "config": "config",
    "schemas": "schema",
    "models": "schema",
}

# Domain signals from Rust use statements
_DOMAIN_IMPORTS_STRONG = {
    # HTTP
    "reqwest": "http", "hyper": "http", "actix_web": "http", "axum": "http",
    "warp": "http", "rocket": "http", "tide": "http",
    # Auth
    "jsonwebtoken": "auth", "oauth2": "auth",
    # Crypto
    "ring": "crypto", "rustls": "crypto", "openssl": "crypto",
    "sha2": "crypto", "aes": "crypto", "ed25519": "crypto",
    # Database
    "diesel": "db", "sqlx": "db", "rusqlite": "db", "mongodb": "db",
    "redis": "db", "sea_orm": "db",
    # CLI
    "clap": "cli", "structopt": "cli",
    # Async
    "tokio": "async", "async_std": "async", "futures": "async",
    # Parsing
    "serde": "parse", "serde_json": "parse", "toml": "parse",
    "csv": "parse", "nom": "parse",
    # Net
    "mio": "net", "tungstenite": "net",
}

_DOMAIN_IMPORTS_WEAK = {
    "std::net": "net", "std::io": "fs", "std::fs": "fs",
    "std::path": "fs", "std::collections": "parse",
}


def _classify_rust_file(file_path: Path, source: bytes, tree) -> str:
    """Classify a Rust file into a category."""
    # 1. Filename rules
    for rule_fn, category in _FILENAME_RULES:
        if rule_fn(file_path):
            return category

    # 2. Directory rules
    for part in file_path.parts:
        lower = part.lower()
        if lower in _DIR_KEYWORDS:
            return _DIR_KEYWORDS[lower]

    # 3. Content analysis
    source_text = source.decode("utf-8", errors="replace")

    # Test module detection
    if "#[cfg(test)]" in source_text or "#[test]" in source_text:
        # Check if the whole file is tests
        test_count = source_text.count("#[test]")
        fn_count = source_text.count("fn ")
        if test_count > 0 and test_count >= fn_count * 0.5:
            return "tests"

    # Count structural signals
    struct_count = 0
    enum_count = 0
    fn_count = 0
    trait_count = 0
    const_count = 0
    route_attr_count = 0
    derive_model_count = 0

    for child in tree.root_node.children:
        if child.type == "struct_item":
            struct_count += 1
            derives = _extract_derives(child, source)
            if any(d in ("Serialize", "Deserialize", "FromRow", "Model") for d in derives):
                derive_model_count += 1
        elif child.type == "enum_item":
            enum_count += 1
            derives = _extract_derives(child, source)
            if any(d in ("Serialize", "Deserialize") for d in derives):
                derive_model_count += 1
        elif child.type == "function_item":
            fn_count += 1
            # Check for route attributes
            for attr in _find_children(child, "attribute_item"):
                attr_text = _node_text(attr, source)
                if any(r in attr_text for r in ("#[get(", "#[post(", "#[put(", "#[delete(",
                       "#[patch(", "#[route(", "#[handler")):
                    route_attr_count += 1
        elif child.type == "trait_item":
            trait_count += 1
        elif child.type in ("const_item", "static_item"):
            const_count += 1
        elif child.type == "impl_item":
            decl = _find_child(child, "declaration_list")
            if decl:
                fn_count += sum(1 for c in decl.children if c.type == "function_item")

    # Router-heavy
    if route_attr_count >= 2:
        return "router"

    # Schema/model-heavy
    if derive_model_count >= 2:
        return "schema"
    if derive_model_count == 1 and struct_count + enum_count <= 3 and fn_count <= 2:
        return "schema"

    # Error types
    if enum_count > 0 and all(
        _looks_like_error_enum(_node_text(_find_child(c, "type_identifier"), source) or "", [])
        for c in tree.root_node.children if c.type == "enum_item" and _find_child(c, "type_identifier")
    ) and fn_count == 0:
        return "exceptions"

    # Constants-only
    if const_count >= 3 and fn_count == 0 and struct_count == 0 and enum_count == 0:
        return "constants"

    # Fallback
    total_defs = fn_count + struct_count + enum_count + trait_count
    if total_defs == 0:
        return "constants" if const_count > 0 else "docs"
    if total_defs <= 3 and const_count == 0:
        return "utils"

    return "core_logic"


def _classify_rust_domain(file_path: Path, source: bytes, tree) -> str:
    """Classify a Rust file by functional domain."""
    # 1. Filename
    stem = file_path.stem.lower()
    from ir.classifier import _DOMAIN_FILE_PATTERNS
    if stem in _DOMAIN_FILE_PATTERNS:
        return _DOMAIN_FILE_PATTERNS[stem]

    # 2. Directory
    for part in file_path.parts:
        lower = part.lower()
        if lower in _DOMAIN_FILE_PATTERNS:
            return _DOMAIN_FILE_PATTERNS[lower]

    # 3. Use statements
    uses = _extract_use_declarations(tree, source)
    domain_scores: Dict[str, int] = {}
    for use in uses:
        if not use["path"]:
            continue
        crate_name = use["path"][0]
        # Also try crate_name with underscores (e.g., actix_web)
        full_path = "::".join(use["path"][:2]) if len(use["path"]) >= 2 else crate_name

        if crate_name in _DOMAIN_IMPORTS_STRONG:
            domain = _DOMAIN_IMPORTS_STRONG[crate_name]
            domain_scores[domain] = domain_scores.get(domain, 0) + 2
        elif full_path in _DOMAIN_IMPORTS_WEAK:
            domain = _DOMAIN_IMPORTS_WEAK[full_path]
            domain_scores[domain] = domain_scores.get(domain, 0) + 1
        # Try with underscored crate name
        crate_under = crate_name.replace("-", "_")
        if crate_under in _DOMAIN_IMPORTS_STRONG and crate_under != crate_name:
            domain = _DOMAIN_IMPORTS_STRONG[crate_under]
            domain_scores[domain] = domain_scores.get(domain, 0) + 2

    qualifying = {d: s for d, s in domain_scores.items() if s >= 2}
    if qualifying:
        return max(qualifying, key=qualifying.get)

    return "unknown"


# ---------------------------------------------------------------------------
# Module/crate root discovery
# ---------------------------------------------------------------------------

def _discover_rust_package_roots(repo_path: Path) -> Set[str]:
    """Find internal crate roots from Cargo.toml workspace or src/ structure."""
    roots: Set[str] = set()

    # Check if this is a workspace
    cargo_toml = repo_path / "Cargo.toml"
    if cargo_toml.exists():
        text = cargo_toml.read_text(encoding="utf-8", errors="ignore")
        # Simple parsing: look for [workspace] members
        if "[workspace]" in text:
            members_match = re.search(r'members\s*=\s*\[(.*?)\]', text, re.DOTALL)
            if members_match:
                for m in re.finditer(r'"([^"]+)"', members_match.group(1)):
                    member = m.group(1).rstrip("/")
                    # Handle globs like "crates/*"
                    if "*" in member:
                        base = member.split("*")[0].rstrip("/")
                        base_path = repo_path / base
                        if base_path.is_dir():
                            for child in base_path.iterdir():
                                if child.is_dir() and (child / "Cargo.toml").exists():
                                    roots.add(child.name)
                    else:
                        roots.add(member.rsplit("/", 1)[-1])

        # The main crate itself
        name_match = re.search(r'name\s*=\s*"([^"]+)"', text)
        if name_match:
            roots.add(name_match.group(1).replace("-", "_"))

    # src/ directory is always internal
    if (repo_path / "src").is_dir():
        roots.add("crate")

    return roots


# ---------------------------------------------------------------------------
# Import map for caller resolution
# ---------------------------------------------------------------------------

def _build_rust_import_map(tree, source: bytes, file_path: Path, repo_path: Path) -> Dict[str, str]:
    """Build a map from local names to qualified paths for caller resolution.

    use std::collections::HashMap; → {"HashMap": "std.collections.HashMap"}
    use crate::models::User;      → {"User": "crate.models.User"}
    use super::helpers;            → {"helpers": "super.helpers"}
    """
    import_map: Dict[str, str] = {}
    uses = _extract_use_declarations(tree, source)

    for use in uses:
        path = use["path"]
        names = use["names"]
        alias = use["alias"]

        if not path or not names:
            continue

        qualified = ".".join(path)
        local_name = alias if alias else names[0]
        import_map[local_name] = qualified

    return import_map


# ---------------------------------------------------------------------------
# LanguageSupport implementation
# ---------------------------------------------------------------------------

class RustLanguage(LanguageSupport):

    @property
    def name(self) -> str:
        return "rust"

    @property
    def extensions(self) -> List[str]:
        return [".rs"]

    @property
    def call_stoplist(self) -> Set[str]:
        return RUST_CALL_STOPLIST

    def parse_entities(self, file_path: Path, include_semantic: bool = True) -> List[dict]:
        source = file_path.read_bytes()
        parser = _make_parser()
        tree = parser.parse(source)
        if tree.root_node.has_error:
            # Still try to extract what we can — tree-sitter is error-tolerant
            pass
        return _extract_entities_from_tree(tree, source, include_semantic)

    def parse_ast(self, file_path: Path):
        """Parse a Rust file and return (tree, source_bytes) tuple."""
        source = file_path.read_bytes()
        parser = _make_parser()
        tree = parser.parse(source)
        return (tree, source)

    def classify_file(self, file_path: Path, tree_and_source) -> str:
        if tree_and_source is None:
            return "core_logic"
        tree, source = tree_and_source
        return _classify_rust_file(file_path, source, tree)

    def classify_domain(self, file_path: Path, tree_and_source) -> str:
        if tree_and_source is None:
            return "unknown"
        tree, source = tree_and_source
        return _classify_rust_domain(file_path, source, tree)

    def extract_import_names(self, tree_and_source, file_path: Optional[Path] = None) -> List[str]:
        if tree_and_source is None:
            return []
        tree, source = tree_and_source
        uses = _extract_use_declarations(tree, source)
        names: Set[str] = set()
        for use in uses:
            if use["path"]:
                root = use["path"][0]
                if root not in ("crate", "self", "super"):
                    names.add(root)
        return sorted(names)

    def discover_package_roots(self, repo_path: Path) -> Set[str]:
        return _discover_rust_package_roots(repo_path)

    def split_imports(
        self, all_imports: List[str], package_roots: Set[str],
    ) -> Tuple[List[str], List[str]]:
        internal = sorted({n for n in all_imports if n in package_roots})
        external = sorted({n for n in all_imports if n not in package_roots})
        return internal, external

    def build_import_map(
        self, tree_and_source, file_path: Path, repo_path: Path,
    ) -> Dict[str, str]:
        if tree_and_source is None:
            return {}
        tree, source = tree_and_source
        return _build_rust_import_map(tree, source, file_path, repo_path)


# Auto-register on import
_instance = RustLanguage()
register_language(_instance)
