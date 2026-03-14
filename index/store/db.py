"""SQLite bootstrap, connection helpers, and schema migrations for CodeIR."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict


def load_schema(schema_path: Path) -> Dict[str, list]:
    """Load schema definition JSON."""
    with schema_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection, statements: list[str]) -> None:
    for statement in statements:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "no such column" in str(exc):
                continue
            raise
    conn.commit()


# ---------------------------------------------------------------------------
# Column introspection helper
# ---------------------------------------------------------------------------

def column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row[0] > 0


# ---------------------------------------------------------------------------
# Entity DB migrations
# ---------------------------------------------------------------------------

def _ensure_entities_migrations(conn: sqlite3.Connection) -> None:
    """Backfill schema changes for existing entities table."""
    if not table_exists(conn, "entities"):
        return
    cols = column_names(conn, "entities")
    if "qualified_name" not in cols:
        conn.execute("ALTER TABLE entities ADD COLUMN qualified_name TEXT")
    if "module_id" not in cols:
        conn.execute("ALTER TABLE entities ADD COLUMN module_id TEXT")
    if "complexity_class" not in cols:
        conn.execute("ALTER TABLE entities ADD COLUMN complexity_class TEXT")
    conn.execute("UPDATE entities SET qualified_name = name WHERE qualified_name IS NULL OR qualified_name = ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_qualified_name ON entities(qualified_name)")
    conn.commit()


def _ensure_ir_rows_composite_pk(conn: sqlite3.Connection) -> None:
    """Migrate ir_rows from single PK (entity_id) to composite PK (entity_id, mode).

    SQLite does not support ALTER TABLE to change a PRIMARY KEY, so we use
    rename-copy-drop strategy.
    """
    if not table_exists(conn, "ir_rows"):
        return

    # Check if ir_rows already has the 'mode' column
    cols = column_names(conn, "ir_rows")
    if "mode" in cols:
        return  # Already migrated

    # Old schema had entity_id as sole PK; migrate to composite (entity_id, mode)
    conn.execute("ALTER TABLE ir_rows RENAME TO _ir_rows_old")
    conn.execute(
        "CREATE TABLE ir_rows ("
        "entity_id TEXT NOT NULL, mode TEXT NOT NULL, "
        "ir_text TEXT NOT NULL, ir_json TEXT NOT NULL, "
        "source_char_count INTEGER NOT NULL DEFAULT 0, "
        "ir_char_count INTEGER NOT NULL DEFAULT 0, "
        "source_token_count INTEGER NOT NULL DEFAULT 0, "
        "ir_token_count INTEGER NOT NULL DEFAULT 0, "
        "compression_ratio REAL NOT NULL DEFAULT 1.0, "
        "PRIMARY KEY(entity_id, mode), "
        "FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE)"
    )
    # Copy existing rows, setting mode to 'Behavior' (the closest equivalent to old hybrid output)
    old_cols = column_names(conn, "_ir_rows_old")
    if "source_char_count" in old_cols:
        conn.execute(
            "INSERT INTO ir_rows (entity_id, mode, ir_text, ir_json, "
            "source_char_count, ir_char_count, source_token_count, ir_token_count, compression_ratio) "
            "SELECT entity_id, 'Behavior', ir_text, ir_json, "
            "source_char_count, ir_char_count, source_token_count, ir_token_count, compression_ratio "
            "FROM _ir_rows_old"
        )
    else:
        conn.execute(
            "INSERT INTO ir_rows (entity_id, mode, ir_text, ir_json) "
            "SELECT entity_id, 'Behavior', ir_text, ir_json FROM _ir_rows_old"
        )
    conn.execute("DROP TABLE _ir_rows_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ir_rows_mode ON ir_rows(mode)")
    conn.commit()


def _ensure_modules_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS modules ("
        "file_path TEXT PRIMARY KEY, category TEXT NOT NULL, "
        "content_hash TEXT NOT NULL, entity_count INTEGER NOT NULL DEFAULT 0, "
        "indexed_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_modules_category ON modules(category)")
    conn.commit()


def _ensure_modules_deps_column(conn: sqlite3.Connection) -> None:
    """Add deps_internal column to modules table if missing."""
    if not table_exists(conn, "modules"):
        return
    cols = column_names(conn, "modules")
    if "deps_internal" not in cols:
        conn.execute("ALTER TABLE modules ADD COLUMN deps_internal TEXT NOT NULL DEFAULT ''")
        conn.commit()


def _ensure_file_metadata_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS file_metadata ("
        "file_path TEXT PRIMARY KEY, content_hash TEXT NOT NULL, "
        "last_indexed_at TEXT NOT NULL, byte_size INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()


def _ensure_index_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()


def _ensure_calls_json_column(conn: sqlite3.Connection) -> None:
    """Add calls_json column to entities table if missing."""
    if not table_exists(conn, "entities"):
        return
    cols = column_names(conn, "entities")
    if "calls_json" not in cols:
        conn.execute("ALTER TABLE entities ADD COLUMN calls_json TEXT NOT NULL DEFAULT ''")
        conn.commit()


def _ensure_callers_table(conn: sqlite3.Connection) -> None:
    """Create the callers table for reverse call lookup."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS callers (
            entity_id TEXT NOT NULL,
            caller_id TEXT NOT NULL,
            caller_name TEXT NOT NULL,
            caller_file TEXT NOT NULL,
            resolution TEXT NOT NULL,
            PRIMARY KEY (entity_id, caller_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_callers_entity ON callers(entity_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_callers_caller ON callers(caller_id)")
    conn.commit()


# ---------------------------------------------------------------------------
# Mapping DB migrations
# ---------------------------------------------------------------------------

def _ensure_abbreviations_version(conn: sqlite3.Connection) -> None:
    """Add version column to abbreviations table if missing."""
    if not table_exists(conn, "abbreviations"):
        return
    cols = column_names(conn, "abbreviations")
    if "version" not in cols:
        conn.execute("ALTER TABLE abbreviations ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        conn.commit()


def _ensure_abbrev_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS abbrev_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_store(repo_path: Path, schema_path: Path) -> Dict[str, Path]:
    """Create .codeir store and initialize entities/mapping DB schemas."""
    store_dir = repo_path / ".codeir"
    store_dir.mkdir(parents=True, exist_ok=True)

    entities_db = store_dir / "entities.db"
    mapping_db = store_dir / "mapping.db"

    schema = load_schema(schema_path)

    entities_conn = connect(entities_db)
    init_db(entities_conn, schema.get("entities_db", []))
    _ensure_entities_migrations(entities_conn)
    _ensure_ir_rows_composite_pk(entities_conn)
    _ensure_modules_table(entities_conn)
    _ensure_modules_deps_column(entities_conn)
    _ensure_file_metadata_table(entities_conn)
    _ensure_index_meta_table(entities_conn)
    _ensure_calls_json_column(entities_conn)
    _ensure_callers_table(entities_conn)
    entities_conn.close()

    mapping_conn = connect(mapping_db)
    init_db(mapping_conn, schema.get("mapping_db", []))
    _ensure_abbreviations_version(mapping_conn)
    _ensure_abbrev_meta_table(mapping_conn)
    mapping_conn.close()

    return {
        "store_dir": store_dir,
        "entities_db": entities_db,
        "mapping_db": mapping_db,
    }
