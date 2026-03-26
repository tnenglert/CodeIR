"""Search query logic for CodeIR entities store."""

from __future__ import annotations

import bisect
import fnmatch
import re
import sqlite3
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from index.store.db import connect, column_names


def search_entities(query: str, repo_path: Path, limit: int = 50, category: Optional[str] = None) -> List[Dict[str, object]]:
    """Return matching entities using LIKE search. Multiple space-separated terms are ORed,
    but entities matching more terms rank higher than those matching fewer."""
    terms = query.strip().split()
    if not terms:
        return []

    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        cols = column_names(conn, "entities")
        qualified_col = "qualified_name" if "qualified_name" in cols else "name"

        # Pre-compute wildcards once per term
        wildcards = [f"%{term}%" for term in terms]

        # WHERE: entity must match at least one term
        where_clauses = []
        where_params: list = []
        for wc in wildcards:
            where_clauses.append(
                f"(entities.{qualified_col} LIKE ? OR entities.name LIKE ? OR entities.file_path LIKE ? OR entities.kind LIKE ?)"
            )
            where_params.extend([wc, wc, wc, wc])

        # ORDER BY 1: count of matching terms (descending — more matches = higher rank)
        match_count_parts = []
        match_count_params: list = []
        for wc in wildcards:
            match_count_parts.append(
                f"(CASE WHEN entities.{qualified_col} LIKE ? OR entities.name LIKE ? OR entities.file_path LIKE ? OR entities.kind LIKE ? THEN 1 ELSE 0 END)"
            )
            match_count_params.extend([wc, wc, wc, wc])
        match_count_expr = " + ".join(match_count_parts)

        # ORDER BY 2: exact/prefix match on first term (primary relevance signal)
        first = terms[0]

        # Optional category filter via JOIN to modules table
        join_clause = ""
        category_clause = ""
        category_params: list = []
        if category:
            join_clause = "JOIN modules AS m ON m.file_path = entities.file_path"
            category_clause = "AND m.category = ?"
            category_params = [category]

        sql = f"""
            SELECT entities.id, entities.{qualified_col} AS qualified_name,
                   entities.file_path, entities.start_line, entities.end_line, entities.kind
            FROM entities
            {join_clause}
            WHERE ({" OR ".join(where_clauses)})
            {category_clause}
            ORDER BY
              ({match_count_expr}) DESC,
              CASE
                WHEN entities.{qualified_col} = ? THEN 0
                WHEN entities.name = ? THEN 1
                WHEN entities.{qualified_col} LIKE ? THEN 2
                ELSE 3
              END,
              entities.{qualified_col} ASC
            LIMIT ?
        """

        rows = conn.execute(
            sql,
            (*where_params, *category_params, *match_count_params, first, first, f"{first}%", limit),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "entity_id": row["id"],
            "qualified_name": row["qualified_name"],
            "file_path": row["file_path"],
            "line": row["start_line"],
            "kind": row["kind"],
            "line_count": row["end_line"] - row["start_line"] + 1 if row["end_line"] else None,
        }
        for row in rows
    ]


def _build_match_entry(
    line_num: int, line_text: str, all_lines: List[str], context: int,
) -> Dict[str, Any]:
    """Build a match dict, optionally including surrounding context lines."""
    entry: Dict[str, Any] = {"line": line_num, "text": line_text.rstrip()}
    if context > 0:
        total = len(all_lines)
        before_start = max(0, line_num - 1 - context)
        after_end = min(total, line_num + context)
        entry["context_before"] = [
            {"line": before_start + i + 1, "text": all_lines[before_start + i].rstrip()}
            for i in range(line_num - 1 - before_start)
        ]
        entry["context_after"] = [
            {"line": line_num + 1 + i, "text": all_lines[line_num + i].rstrip()}
            for i in range(after_end - line_num)
        ]
    return entry


def grep_entities(
    pattern: str,
    repo_path: Path,
    level: str = "Behavior",
    limit: int = 50,
    ignore_case: bool = False,
    context: int = 0,
    path_filter: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Grep source files for a regex pattern and return matches with IR context.

    Each result is either:
    - An entity group: matches within an indexed entity, with its IR text attached.
    - An unmatched group: matches outside any entity (top-level code, imports, etc.).

    Results are grouped by entity (or by file for unmatched lines) and deduplicated —
    multiple hits within the same entity produce one result with a match list.
    """
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc

    # Load all entity spans and IR from DB
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        entity_rows = conn.execute(
            "SELECT e.id, e.qualified_name, e.kind, e.file_path, e.start_line, e.end_line, "
            "r.ir_text, r.mode "
            "FROM entities AS e "
            "LEFT JOIN ir_rows AS r ON r.entity_id = e.id AND r.mode = ? "
            "ORDER BY e.file_path, e.start_line",
            (level,),
        ).fetchall()
    finally:
        conn.close()

    # Build a lookup: file_path -> sorted list of (start, end, entity_info)
    file_entities: Dict[str, List[Dict[str, Any]]] = {}
    for row in entity_rows:
        fp = row["file_path"]
        file_entities.setdefault(fp, []).append({
            "entity_id": row["id"],
            "qualified_name": row["qualified_name"],
            "kind": row["kind"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "ir_text": row["ir_text"],
        })

    # Derive indexed file list from already-fetched entity data
    indexed_files = list(file_entities.keys())

    # Apply path filter(s) (directory prefix or glob pattern)
    path_filters: List[str] = []
    if isinstance(path_filter, str):
        path_filters = [path_filter]
    elif isinstance(path_filter, (list, tuple)):
        path_filters = [p for p in path_filter if p]

    if path_filters:
        def _matches_any_filter(rel_path: str) -> bool:
            for filt in path_filters:
                if any(c in filt for c in "*?["):
                    if fnmatch.fnmatch(rel_path, filt):
                        return True
                else:
                    prefix = filt.rstrip("/")
                    if rel_path == prefix or rel_path.startswith(prefix + "/"):
                        return True
            return False

        indexed_files = [f for f in indexed_files if _matches_any_filter(f)]

    # Grep each indexed file
    results_by_key: Dict[str, Dict[str, Any]] = {}  # key -> grouped result
    result_order: List[str] = []

    limit_reached = False
    for rel_path in indexed_files:
        if limit_reached:
            break
        abs_path = (repo_path / rel_path).resolve()
        if not abs_path.is_file():
            continue
        try:
            lines = abs_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue

        spans = file_entities.get(rel_path, [])
        # Pre-extract start_lines for bisect (spans are sorted by start_line)
        span_starts = [s["start_line"] for s in spans]

        for line_num_0, line_text in enumerate(lines):
            line_num = line_num_0 + 1
            if not regex.search(line_text):
                continue

            # Find innermost enclosing entity via bisect.
            # Spans are sorted by start_line ASC, so the last span whose
            # start_line <= line_num is the innermost (nested entities start later).
            enclosing = None
            idx = bisect.bisect_right(span_starts, line_num) - 1
            while idx >= 0:
                span = spans[idx]
                if span["end_line"] >= line_num:
                    enclosing = span
                    break  # innermost match (latest start_line that still contains the line)
                idx -= 1

            if enclosing:
                key = f"entity:{enclosing['entity_id']}"
                is_new = key not in results_by_key
                if is_new and len(result_order) >= limit:
                    limit_reached = True
                    break
                if is_new:
                    results_by_key[key] = {
                        "type": "entity",
                        "entity_id": enclosing["entity_id"],
                        "qualified_name": enclosing["qualified_name"],
                        "kind": enclosing["kind"],
                        "file_path": rel_path,
                        "start_line": enclosing["start_line"],
                        "end_line": enclosing["end_line"],
                        "ir_text": enclosing["ir_text"],
                        "matches": [],
                    }
                    result_order.append(key)
                match_entry = _build_match_entry(line_num, line_text, lines, context)
                results_by_key[key]["matches"].append(match_entry)
            else:
                key = f"file:{rel_path}"
                is_new = key not in results_by_key
                if is_new and len(result_order) >= limit:
                    limit_reached = True
                    break
                if is_new:
                    results_by_key[key] = {
                        "type": "file",
                        "file_path": rel_path,
                        "matches": [],
                    }
                    result_order.append(key)
                match_entry = _build_match_entry(line_num, line_text, lines, context)
                results_by_key[key]["matches"].append(match_entry)

    # Collect in insertion order (limit already enforced during scanning)
    return [results_by_key[key] for key in result_order]


def compute_impact(
    conn: sqlite3.Connection,
    entity_id: str,
    depth: int = 2,
    level: str = "Behavior",
) -> Dict[str, Any]:
    """BFS traversal through callers graph. Returns structured impact data.

    Expects conn.row_factory to be sqlite3.Row.
    """
    # Verify root exists
    root = conn.execute(
        "SELECT id, qualified_name, file_path, start_line, kind FROM entities WHERE id = ? LIMIT 1",
        (entity_id,),
    ).fetchone()
    if not root:
        return {"root": None, "impact_by_depth": {}, "affected_files": set(), "affected_categories": set()}

    root_ir = conn.execute(
        "SELECT ir_text FROM ir_rows WHERE entity_id = ? AND mode = ? LIMIT 1",
        (entity_id, level),
    ).fetchone()

    root_info = {
        "entity_id": root["id"], "qualified_name": root["qualified_name"],
        "file_path": root["file_path"], "start_line": root["start_line"],
        "kind": root["kind"], "ir_text": root_ir["ir_text"] if root_ir else None,
    }

    visited: set = {entity_id}
    queue: deque = deque([(entity_id, 0)])
    impact_by_depth: Dict[int, List[Dict[str, Any]]] = {}
    affected_files: set = set()
    affected_categories: set = set()

    while queue:
        current_id, current_depth = queue.popleft()
        if current_depth >= depth:
            continue

        callers = conn.execute(
            "SELECT caller_id, caller_name, caller_file, resolution FROM callers WHERE entity_id = ?",
            (current_id,),
        ).fetchall()

        for row in callers:
            caller_id = row["caller_id"]
            if caller_id in visited:
                continue
            visited.add(caller_id)

            next_depth = current_depth + 1
            entity = conn.execute(
                "SELECT id, qualified_name, file_path, start_line, kind FROM entities WHERE id = ? LIMIT 1",
                (caller_id,),
            ).fetchone()
            ir_row = conn.execute(
                "SELECT ir_text FROM ir_rows WHERE entity_id = ? AND mode = ? LIMIT 1",
                (caller_id, level),
            ).fetchone()

            category = None
            if entity:
                mod = conn.execute(
                    "SELECT category FROM modules WHERE file_path = ? LIMIT 1",
                    (entity["file_path"],),
                ).fetchone()
                category = mod["category"] if mod else None

            impact_by_depth.setdefault(next_depth, []).append({
                "entity_id": caller_id,
                "qualified_name": entity["qualified_name"] if entity else row["caller_name"],
                "file_path": entity["file_path"] if entity else row["caller_file"],
                "start_line": entity["start_line"] if entity else None,
                "kind": entity["kind"] if entity else "unknown",
                "resolution": row["resolution"],
                "ir_text": ir_row["ir_text"] if ir_row else None,
                "via": current_id,
                "category": category,
            })

            file_path = entity["file_path"] if entity else row["caller_file"]
            affected_files.add(file_path)
            if category:
                affected_categories.add(category)

            queue.append((caller_id, next_depth))

    return {
        "root": root_info,
        "impact_by_depth": impact_by_depth,
        "affected_files": affected_files,
        "affected_categories": affected_categories,
    }


def compute_scope(
    conn: sqlite3.Connection,
    entity_id: str,
    level: str = "Behavior",
) -> Dict[str, Any]:
    """Return the minimal set of entities needed to safely modify an entity.

    Includes: the entity itself, its direct callers, its callees (what it calls),
    and sibling methods (same class, sharing self state).

    Expects conn.row_factory to be sqlite3.Row.
    """
    def _entity_info(eid: str) -> Optional[Dict[str, Any]]:
        row = conn.execute(
            "SELECT id, qualified_name, file_path, start_line, kind "
            "FROM entities WHERE id = ? LIMIT 1", (eid,),
        ).fetchone()
        if not row:
            return None
        ir = conn.execute(
            "SELECT ir_text FROM ir_rows WHERE entity_id = ? AND mode = ? LIMIT 1",
            (eid, level),
        ).fetchone()
        return {
            "entity_id": row["id"], "qualified_name": row["qualified_name"],
            "file_path": row["file_path"], "start_line": row["start_line"],
            "kind": row["kind"], "ir_text": ir["ir_text"] if ir else None,
        }

    root = _entity_info(entity_id)
    if not root:
        return {"root": None, "callers": [], "callees": [], "siblings": []}

    # Direct callers — entities that call this one
    caller_rows = conn.execute(
        "SELECT caller_id, resolution FROM callers WHERE entity_id = ?",
        (entity_id,),
    ).fetchall()
    callers = []
    for row in caller_rows:
        info = _entity_info(row["caller_id"])
        if info:
            info["resolution"] = row["resolution"]
            callers.append(info)

    # Callees — entities this one calls (reverse lookup in callers table)
    callee_rows = conn.execute(
        "SELECT entity_id, resolution FROM callers WHERE caller_id = ?",
        (entity_id,),
    ).fetchall()
    callees = []
    for row in callee_rows:
        info = _entity_info(row["entity_id"])
        if info:
            info["resolution"] = row["resolution"]
            callees.append(info)

    # Siblings — other methods in the same class (shared self state)
    siblings = []
    qname = root["qualified_name"]
    if "." in qname and root["kind"] in ("method", "async_method"):
        class_prefix = qname.rsplit(".", 1)[0]
        sibling_rows = conn.execute(
            "SELECT id FROM entities WHERE qualified_name LIKE ? AND id != ? "
            "AND kind IN ('method', 'async_method')",
            (class_prefix + ".%", entity_id),
        ).fetchall()
        for row in sibling_rows:
            info = _entity_info(row["id"])
            if info:
                siblings.append(info)

    return {"root": root, "callers": callers, "callees": callees, "siblings": siblings}
