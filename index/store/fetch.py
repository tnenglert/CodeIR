"""Read helpers for stored entities and IR rows."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from index.store.db import connect, column_names


def get_entity_with_ir(
    repo_path: Path, entity_id: str, mode: str = "Behavior",
) -> Optional[Dict[str, object]]:
    """Fetch a single entity and its stored IR text from entities.db.

    Returns None when the entity does not exist or has no stored IR row at the given mode.
    """
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    # Check if ir_rows has the mode column (new composite PK schema)
    has_mode = "mode" in column_names(conn, "ir_rows")

    if has_mode:
        row = conn.execute(
            "SELECT e.id AS entity_id, e.qualified_name, e.file_path, e.start_line, e.kind, "
            "e.module_id, e.complexity_class, r.ir_text, r.mode "
            "FROM entities AS e JOIN ir_rows AS r ON r.entity_id = e.id "
            "WHERE e.id = ? AND r.mode = ? LIMIT 1",
            (entity_id, mode),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT e.id AS entity_id, e.qualified_name, e.file_path, e.start_line, e.kind, "
            "r.ir_text "
            "FROM entities AS e JOIN ir_rows AS r ON r.entity_id = e.id "
            "WHERE e.id = ? LIMIT 1",
            (entity_id,),
        ).fetchone()
    conn.close()

    if row is None:
        return None

    result = {
        "entity_id": row["entity_id"],
        "qualified_name": row["qualified_name"],
        "file_path": row["file_path"],
        "line": row["start_line"],
        "kind": row["kind"],
        "ir_text": row["ir_text"],
    }
    if has_mode:
        result["mode"] = row["mode"]
        result["module_id"] = row["module_id"]
        result["complexity_class"] = row["complexity_class"]
    return result


def get_entity_all_levels(
    repo_path: Path, entity_id: str,
) -> List[Dict[str, object]]:
    """Fetch all IR levels for a single entity. Used by the compare command."""
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT e.id AS entity_id, e.qualified_name, e.file_path, e.start_line, e.end_line, e.kind, "
        "r.ir_text, r.mode, r.source_token_count, r.ir_token_count, r.compression_ratio "
        "FROM entities AS e JOIN ir_rows AS r ON r.entity_id = e.id "
        "WHERE e.id = ? ORDER BY r.mode",
        (entity_id,),
    ).fetchall()
    conn.close()

    return [
        {
            "entity_id": row["entity_id"],
            "qualified_name": row["qualified_name"],
            "file_path": row["file_path"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "kind": row["kind"],
            "ir_text": row["ir_text"],
            "mode": row["mode"],
            "source_token_count": row["source_token_count"],
            "ir_token_count": row["ir_token_count"],
            "compression_ratio": row["compression_ratio"],
        }
        for row in rows
    ]


def get_entity_location(repo_path: Path, entity_id: str) -> Optional[Dict[str, object]]:
    """Fetch entity location metadata from entities.db."""
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id AS entity_id, qualified_name, file_path, start_line, end_line, kind "
        "FROM entities WHERE id = ? LIMIT 1",
        (entity_id,),
    ).fetchone()
    conn.close()

    if row is None:
        return None

    return {
        "entity_id": row["entity_id"],
        "qualified_name": row["qualified_name"],
        "file_path": row["file_path"],
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "kind": row["kind"],
    }


def load_entity_ir_rows(
    repo_path: Path,
    level: Optional[str] = None,
    include_metrics: bool = False,
) -> List[Dict[str, Any]]:
    """Load entity+IR rows from entities.db, optionally filtered by compression level.

    When include_metrics=True, includes source_token_count, ir_token_count,
    compression_ratio, and parsed ir_json. Used by eval.py for level metrics.
    """
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    if include_metrics:
        columns = (
            "e.id AS entity_id, e.qualified_name, e.kind, "
            "r.ir_text, r.ir_json, r.source_token_count, r.ir_token_count, "
            "r.compression_ratio, r.mode"
        )
    else:
        columns = "e.id AS entity_id, e.qualified_name, e.kind, r.ir_text"

    base_sql = (
        f"SELECT {columns} FROM entities AS e "
        "JOIN ir_rows AS r ON r.entity_id = e.id"
    )

    if level:
        rows = conn.execute(f"{base_sql} WHERE r.mode = ?", (level,)).fetchall()
    else:
        rows = conn.execute(base_sql).fetchall()
    conn.close()

    out: List[Dict[str, Any]] = []
    for row in rows:
        entry: Dict[str, Any] = {
            "entity_id": row["entity_id"],
            "qualified_name": row["qualified_name"],
            "kind": row["kind"],
            "ir_text": row["ir_text"],
        }
        if include_metrics:
            try:
                entry["ir_json"] = json.loads(row["ir_json"])
            except (json.JSONDecodeError, TypeError):
                entry["ir_json"] = {}
            entry["source_token_count"] = int(row["source_token_count"])
            entry["ir_token_count"] = int(row["ir_token_count"])
            entry["compression_ratio"] = float(row["compression_ratio"])
            entry["mode"] = row["mode"]
        out.append(entry)
    return out
