"""Search query logic for SemanticIR entities store."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List

from index.store.db import connect


def search_entities(query: str, repo_path: Path, limit: int = 50) -> List[Dict[str, object]]:
    """Return matching entities from entities.db using phase-1 LIKE search."""
    normalized = query.strip()
    if not normalized:
        return []

    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    wildcard = f"%{normalized}%"
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    cols = {row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()}
    qualified_col = "qualified_name" if "qualified_name" in cols else "name"

    sql = f"""
        SELECT id, {qualified_col} AS qualified_name, file_path, start_line, kind
        FROM entities
        WHERE {qualified_col} LIKE ?
           OR name LIKE ?
           OR file_path LIKE ?
           OR kind LIKE ?
        ORDER BY
          CASE
            WHEN {qualified_col} = ? THEN 0
            WHEN name = ? THEN 1
            WHEN {qualified_col} LIKE ? THEN 2
            ELSE 3
          END,
          {qualified_col} ASC
        LIMIT ?
    """

    rows = conn.execute(
        sql,
        (wildcard, wildcard, wildcard, wildcard, normalized, normalized, f"{normalized}%", limit),
    ).fetchall()
    conn.close()

    return [
        {
            "entity_id": row["id"],
            "qualified_name": row["qualified_name"],
            "file_path": row["file_path"],
            "line": row["start_line"],
            "kind": row["kind"],
        }
        for row in rows
    ]
