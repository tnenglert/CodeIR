"""Reverse caller resolution — builds a callers table mapping entity→callers.

Pass 2 of the indexing pipeline. Runs after entity extraction and ID assignment.
Resolves semantic.calls references to entity IDs and stores inverse relationships.

Resolution tiers:
  import — resolved through file imports (high confidence)
  local  — resolved to entity in same file (high confidence)
  fuzzy  — matched by bare name against repo-wide entities (lower confidence)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from index.languages import get_frontend_for_file
from index.locator import parse_ast
from index.store.db import connect

# Max fuzzy matches before we consider the name too ambiguous
FUZZY_MATCH_LIMIT = 4


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

def resolve_calls_for_entity(
    entity: Dict,
    calls: List[str],
    file_path: str,
    import_map: Dict[str, str],
    name_to_entities: Dict[str, List[Dict]],
    qualified_to_entity: Dict[str, Dict],
    stoplist: set[str],
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

        if not is_qualified and call_name in stoplist:
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
                    bare = qualified_source.rsplit(".", 1)[-1]
                    candidates = [
                        e for e in name_to_entities.get(bare, [])
                        if e["entity_id"] != entity["entity_id"]
                    ]
                    if len(candidates) == 1:
                        resolved.append((candidates[0]["entity_id"], "import"))

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

        frontend = get_frontend_for_file(abs_path)
        import_map = frontend.build_import_map(tree, abs_path, repo_path)

        for entity in entities:
            calls = calls_by_id.get(entity["entity_id"], [])

            relationships, ambiguous = resolve_calls_for_entity(
                entity=entity,
                calls=calls,
                file_path=file_path_str,
                import_map=import_map,
                name_to_entities=name_to_entities,
                qualified_to_entity=qualified_to_entity,
                stoplist=frontend.stoplist,
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
