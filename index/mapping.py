"""Persist and retrieve compression maps."""

from __future__ import annotations

import sqlite3
from typing import Dict


def save_abbreviation_maps(conn: sqlite3.Connection, maps: Dict[str, Dict[str, str]]) -> int:
    """Upsert abbreviation mappings into mapping.db."""
    for map_type, token_map in maps.items():
        for original, token in token_map.items():
            conn.execute(
                """
                INSERT INTO abbreviations(map_type, original, token)
                VALUES (?, ?, ?)
                ON CONFLICT(map_type, original) DO UPDATE SET token = excluded.token
                """,
                (map_type, original, token),
            )
    conn.commit()
    return int(conn.execute("SELECT COUNT(*) FROM abbreviations").fetchone()[0])


def load_abbreviation_maps(conn: sqlite3.Connection) -> Dict[str, Dict[str, str]]:
    """Load all abbreviation mappings grouped by map_type."""
    rows = conn.execute("SELECT map_type, original, token FROM abbreviations").fetchall()
    out: Dict[str, Dict[str, str]] = {}
    for map_type, original, token in rows:
        out.setdefault(map_type, {})[original] = token
    return out
