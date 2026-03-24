"""Reverse caller resolution — builds a callers table mapping entity→callers.

Pass 2 of the indexing pipeline. Runs after entity extraction and ID assignment.
Resolves semantic.calls references to entity IDs and stores inverse relationships.

Resolution tiers:
  import — resolved through file imports (high confidence)
  local  — resolved to entity in same file (high confidence)
  fuzzy  — matched by bare name against repo-wide entities (lower confidence)
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from index.locator import parse_ast
from index.store.db import connect

# Max fuzzy matches before we consider the name too ambiguous
FUZZY_MATCH_LIMIT = 4

# Names too common to produce useful caller relationships
CALL_STOPLIST = {
    # Python builtins
    "len", "range", "print", "str", "int", "float", "bool", "list", "dict",
    "set", "tuple", "type", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "super", "property", "staticmethod", "classmethod", "enumerate",
    "zip", "map", "filter", "sorted", "reversed", "any", "all", "min", "max",
    "abs", "sum", "round", "id", "hash", "repr", "next", "iter", "callable",
    "vars", "dir", "hex", "oct", "bin", "ord", "chr",
    # Common method names that resolve to too many targets
    "get", "set", "put", "post", "delete", "update", "pop", "add", "remove",
    "append", "extend", "insert", "clear", "copy", "keys", "values", "items",
    "format", "join", "split", "strip", "replace", "find", "index", "count",
    "read", "write", "close", "open", "flush", "seek",
    "encode", "decode", "lower", "upper", "startswith", "endswith",
    "run", "start", "stop", "init", "setup", "teardown",
}


# ---------------------------------------------------------------------------
# Step 1: Build repo-wide name maps from entities table
# ---------------------------------------------------------------------------

def build_name_maps(
    conn,
) -> Tuple[Dict[str, List[Dict]], Dict[str, Dict]]:
    """Build name→entities and qualified_name→entity lookup maps."""
    rows = conn.execute(
        "SELECT id, name, qualified_name, file_path FROM entities"
    ).fetchall()

    name_to_entities: Dict[str, List[Dict]] = {}
    qualified_to_entity: Dict[str, Dict] = {}

    for row in rows:
        entity = {
            "entity_id": row[0],
            "name": row[1],
            "qualified_name": row[2],
            "file_path": row[3],
        }
        name_to_entities.setdefault(row[1], []).append(entity)
        qualified_to_entity[row[2]] = entity

    return name_to_entities, qualified_to_entity


# ---------------------------------------------------------------------------
# Step 2: Build per-file import maps
# ---------------------------------------------------------------------------

def build_import_map(
    tree: ast.Module, file_path: Path, repo_path: Path,
) -> Dict[str, str]:
    """Map locally bound names to their fully qualified origin.

    Examples:
        from flask.sessions import SessionInterface
            → {"SessionInterface": "flask.sessions.SessionInterface"}
        from flask.sessions import SessionInterface as SI
            → {"SI": "flask.sessions.SessionInterface"}
        import os.path
            → {"os": "os"}
        from . import helpers  (in src/flask/__init__.py)
            → {"helpers": "flask.helpers"}
    """
    import_map: Dict[str, str] = {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname if alias.asname else alias.name.split(".")[0]
                import_map[local_name] = alias.name

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""

            # Handle relative imports
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


# ---------------------------------------------------------------------------
# Step 3: Resolve calls for a single entity
# ---------------------------------------------------------------------------

def resolve_calls_for_entity(
    entity: Dict,
    calls: List[str],
    file_path: str,
    import_map: Dict[str, str],
    name_to_entities: Dict[str, List[Dict]],
    qualified_to_entity: Dict[str, Dict],
) -> Tuple[List[Dict], List[Dict]]:
    """Resolve an entity's calls to caller relationships.

    Returns:
        (relationships, ambiguous) where:
        - relationships: dicts ready for insertion into callers table
        - ambiguous: dicts describing unresolved calls due to too many candidates

    Qualified calls (e.g., "password_helper.hash") bypass the stoplist
    and resolve by matching the method suffix against entity names.
    """
    relationships: List[Dict] = []
    ambiguous: List[Dict] = []
    seen_targets: set = set()

    for call_name in calls:
        # Qualified calls (contain a dot) bypass stoplist
        is_qualified = "." in call_name

        if not is_qualified and call_name in CALL_STOPLIST:
            continue

        resolved: List[Tuple[str, str]] = []
        candidates_for_ambiguity: List[Dict] = []

        if is_qualified:
            # For qualified calls like "password_helper.hash",
            # extract the method name and match against entities
            method_name = call_name.rsplit(".", 1)[-1]

            # Try same-file resolution first
            same_file = [
                e for e in name_to_entities.get(method_name, [])
                if e["file_path"] == file_path and e["entity_id"] != entity["entity_id"]
            ]
            for target in same_file:
                resolved.append((target["entity_id"], "local"))

            # Then fuzzy repo-wide match
            if not resolved:
                candidates = [
                    e for e in name_to_entities.get(method_name, [])
                    if e["entity_id"] != entity["entity_id"]
                ]
                if 1 <= len(candidates) <= FUZZY_MATCH_LIMIT:
                    for target in candidates:
                        resolved.append((target["entity_id"], "fuzzy"))
                elif len(candidates) > FUZZY_MATCH_LIMIT:
                    candidates_for_ambiguity = candidates
        else:
            # Unqualified call — use original resolution tiers

            # Tier 1: Import resolution
            if call_name in import_map:
                qualified_source = import_map[call_name]
                if qualified_source in qualified_to_entity:
                    target = qualified_to_entity[qualified_source]
                    resolved.append((target["entity_id"], "import"))
                else:
                    # Try matching the bare name from the qualified import
                    # e.g. "flask.redirect" → look for entity named "redirect"
                    bare = qualified_source.rsplit(".", 1)[-1]
                    if bare in qualified_to_entity:
                        target = qualified_to_entity[bare]
                        resolved.append((target["entity_id"], "import"))

            # Tier 2: Same-file resolution (only if Tier 1 didn't resolve)
            if not resolved:
                same_file = [
                    e for e in name_to_entities.get(call_name, [])
                    if e["file_path"] == file_path and e["entity_id"] != entity["entity_id"]
                ]
                for target in same_file:
                    resolved.append((target["entity_id"], "local"))

            # Tier 3: Fuzzy repo-wide match (only if nothing resolved above)
            if not resolved:
                candidates = [
                    e for e in name_to_entities.get(call_name, [])
                    if e["entity_id"] != entity["entity_id"]
                ]
                if 1 <= len(candidates) <= FUZZY_MATCH_LIMIT:
                    for target in candidates:
                        resolved.append((target["entity_id"], "fuzzy"))
                elif len(candidates) > FUZZY_MATCH_LIMIT:
                    candidates_for_ambiguity = candidates

        # Track ambiguous calls (exceeded fuzzy limit, no other resolution)
        if not resolved and candidates_for_ambiguity:
            ambiguous.append({
                "caller_id": entity["entity_id"],
                "call_name": call_name,
                "candidate_count": len(candidates_for_ambiguity),
                "candidate_ids": [c["entity_id"] for c in candidates_for_ambiguity[:6]],
            })

        for target_id, resolution in resolved:
            if target_id not in seen_targets:
                seen_targets.add(target_id)
                relationships.append({
                    "entity_id": target_id,
                    "caller_id": entity["entity_id"],
                    "caller_name": entity["qualified_name"],
                    "caller_file": file_path,
                    "resolution": resolution,
                })

    return relationships, ambiguous



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_callers_table(repo_path: Path, db_path: Path) -> Tuple[int, List[Dict]]:
    """Run caller resolution and populate the callers table.

    Returns:
        (relationship_count, ambiguous_calls) where ambiguous_calls contains
        unresolved calls that exceeded FUZZY_MATCH_LIMIT.
    """
    from index.store.db import _ensure_callers_table

    conn = connect(db_path)

    # 1. Build repo-wide name maps (also used for grouping by file)
    name_to_entities, qualified_to_entity = build_name_maps(conn)

    # 2. Group all entities by file, reusing the qualified_name map (one entry per entity)
    entities_by_file: Dict[str, List[Dict]] = {}
    for entity in qualified_to_entity.values():
        entities_by_file.setdefault(entity["file_path"], []).append(entity)

    # 3. Bulk-load all calls_json to avoid N+1 queries
    calls_by_id: Dict[str, List[str]] = {}
    for row in conn.execute("SELECT id, calls_json FROM entities WHERE calls_json != ''").fetchall():
        try:
            calls_by_id[row[0]] = json.loads(row[1])
        except (json.JSONDecodeError, TypeError):
            pass

    # 4. Drop and recreate callers table (schema defined in db.py)
    conn.execute("DROP TABLE IF EXISTS callers")
    _ensure_callers_table(conn)

    # 5. Resolve all caller relationships
    all_relationships: List[tuple] = []
    all_ambiguous: List[Dict] = []

    for file_path_str, entities in entities_by_file.items():
        abs_path = repo_path / file_path_str
        if not abs_path.exists():
            continue

        tree = parse_ast(abs_path)
        if tree is None:
            continue

        import_map = build_import_map(tree, abs_path, repo_path)

        for entity in entities:
            calls = calls_by_id.get(entity["entity_id"], [])

            relationships, ambiguous = resolve_calls_for_entity(
                entity=entity,
                calls=calls,
                file_path=file_path_str,
                import_map=import_map,
                name_to_entities=name_to_entities,
                qualified_to_entity=qualified_to_entity,
            )

            for rel in relationships:
                all_relationships.append((
                    rel["entity_id"], rel["caller_id"],
                    rel["caller_name"], rel["caller_file"], rel["resolution"],
                ))
            all_ambiguous.extend(ambiguous)

    # 6. Batch insert all relationships
    conn.executemany(
        "INSERT OR IGNORE INTO callers VALUES (?, ?, ?, ?, ?)",
        all_relationships,
    )
    conn.commit()

    total_relationships = conn.execute("SELECT COUNT(*) FROM callers").fetchone()[0]
    conn.close()
    return total_relationships, all_ambiguous
