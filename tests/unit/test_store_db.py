"""Tests for database connection, schema bootstrap, and migrations."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from index.store.db import (
    _connect_immutable,
    _ensure_callers_table,
    _ensure_calls_json_column,
    _ensure_entities_migrations,
    _ensure_file_metadata_table,
    _ensure_index_meta_table,
    _ensure_ir_rows_composite_pk,
    _ensure_modules_deps_column,
    _ensure_modules_table,
    column_names,
    connect,
    ensure_store,
    init_db,
    load_schema,
    table_exists,
)


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------

class TestConnect:
    def test_creates_parent_dirs(self, tmp_path):
        db_path = tmp_path / "sub" / "dir" / "test.db"
        conn = connect(db_path)
        assert db_path.exists()
        conn.close()

    def test_wal_mode(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = connect(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_read_only_mode(self, tmp_path):
        db_path = tmp_path / "test.db"
        # Create the DB first
        conn = connect(db_path)
        conn.execute("CREATE TABLE t (x TEXT)")
        conn.execute("INSERT INTO t VALUES ('hello')")
        conn.commit()
        conn.close()

        ro_conn = connect(db_path, read_only=True)
        rows = ro_conn.execute("SELECT * FROM t").fetchall()
        assert rows == [("hello",)]
        ro_conn.close()


# ---------------------------------------------------------------------------
# _connect_immutable
# ---------------------------------------------------------------------------

class TestConnectImmutable:
    def test_reads_existing_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
        conn.commit()
        conn.close()

        ro_conn = _connect_immutable(db_path)
        rows = ro_conn.execute("SELECT * FROM t").fetchall()
        assert rows == [(42,)]
        ro_conn.close()

    def test_nonexistent_db_connects_via_fallback(self, tmp_path):
        """nolock=1 fallback may create the file; verify connection succeeds."""
        conn = _connect_immutable(tmp_path / "no_such.db")
        # Should get a valid connection (empty DB) via one of the fallback strategies
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert tables == []  # empty DB, no tables
        conn.close()


# ---------------------------------------------------------------------------
# table_exists / column_names
# ---------------------------------------------------------------------------

class TestIntrospection:
    def test_table_exists_true(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE foo (x TEXT)")
        assert table_exists(conn, "foo") is True
        conn.close()

    def test_table_exists_false(self):
        conn = sqlite3.connect(":memory:")
        assert table_exists(conn, "foo") is False
        conn.close()

    def test_column_names(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE foo (x TEXT, y INTEGER, z REAL)")
        cols = column_names(conn, "foo")
        assert cols == {"x", "y", "z"}
        conn.close()


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_tables(self):
        conn = sqlite3.connect(":memory:")
        init_db(conn, [
            "CREATE TABLE IF NOT EXISTS test (id TEXT PRIMARY KEY, val TEXT)",
        ])
        assert table_exists(conn, "test")
        conn.close()

    def test_idempotent(self):
        conn = sqlite3.connect(":memory:")
        stmts = ["CREATE TABLE IF NOT EXISTS test (id TEXT PRIMARY KEY)"]
        init_db(conn, stmts)
        init_db(conn, stmts)  # should not raise
        assert table_exists(conn, "test")
        conn.close()


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

class TestMigrations:
    def test_entities_migrations_add_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO entities VALUES ('FOO', 'foo')")
        conn.commit()

        _ensure_entities_migrations(conn)
        cols = column_names(conn, "entities")
        assert "qualified_name" in cols
        assert "module_id" in cols
        assert "complexity_class" in cols

        # qualified_name should be backfilled from name
        row = conn.execute("SELECT qualified_name FROM entities WHERE id='FOO'").fetchone()
        assert row[0] == "foo"
        conn.close()

    def test_entities_migrations_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT, "
            "qualified_name TEXT, module_id TEXT, complexity_class TEXT)"
        )
        _ensure_entities_migrations(conn)  # should not raise
        conn.close()

    def test_entities_migrations_no_table(self):
        conn = sqlite3.connect(":memory:")
        _ensure_entities_migrations(conn)  # should not raise (no-op)
        conn.close()

    def test_modules_table_created(self):
        conn = sqlite3.connect(":memory:")
        _ensure_modules_table(conn)
        assert table_exists(conn, "modules")
        conn.close()

    def test_modules_deps_column_added(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE modules (file_path TEXT PRIMARY KEY, category TEXT, "
            "content_hash TEXT, entity_count INTEGER, indexed_at TEXT)"
        )
        _ensure_modules_deps_column(conn)
        assert "deps_internal" in column_names(conn, "modules")
        conn.close()

    def test_modules_deps_column_idempotent(self):
        conn = sqlite3.connect(":memory:")
        _ensure_modules_table(conn)
        _ensure_modules_deps_column(conn)
        _ensure_modules_deps_column(conn)  # should not raise
        conn.close()

    def test_file_metadata_table(self):
        conn = sqlite3.connect(":memory:")
        _ensure_file_metadata_table(conn)
        assert table_exists(conn, "file_metadata")
        conn.close()

    def test_index_meta_table(self):
        conn = sqlite3.connect(":memory:")
        _ensure_index_meta_table(conn)
        assert table_exists(conn, "index_meta")
        conn.close()

    def test_calls_json_column_added(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT, "
            "qualified_name TEXT, file_path TEXT, start_line INTEGER, "
            "end_line INTEGER, kind TEXT)"
        )
        _ensure_calls_json_column(conn)
        assert "calls_json" in column_names(conn, "entities")
        conn.close()

    def test_callers_table(self):
        conn = sqlite3.connect(":memory:")
        _ensure_callers_table(conn)
        assert table_exists(conn, "callers")
        cols = column_names(conn, "callers")
        assert {"entity_id", "caller_id", "caller_name", "caller_file", "resolution"} <= cols
        conn.close()

    def test_ir_rows_composite_pk_migration(self):
        """Migrate ir_rows from single PK to composite PK (entity_id, mode)."""
        conn = sqlite3.connect(":memory:")
        # Old schema
        conn.execute(
            "CREATE TABLE entities (id TEXT PRIMARY KEY)"
        )
        conn.execute("INSERT INTO entities VALUES ('E1')")
        conn.execute(
            "CREATE TABLE ir_rows (entity_id TEXT PRIMARY KEY, "
            "ir_text TEXT, ir_json TEXT, "
            "source_char_count INTEGER DEFAULT 0, ir_char_count INTEGER DEFAULT 0, "
            "source_token_count INTEGER DEFAULT 0, ir_token_count INTEGER DEFAULT 0, "
            "compression_ratio REAL DEFAULT 1.0, "
            "FOREIGN KEY(entity_id) REFERENCES entities(id))"
        )
        conn.execute(
            "INSERT INTO ir_rows VALUES ('E1', 'FN FOO', '{}', 100, 10, 20, 5, 0.25)"
        )
        conn.commit()

        _ensure_ir_rows_composite_pk(conn)

        # Verify mode column now exists
        assert "mode" in column_names(conn, "ir_rows")

        # Verify data migrated with mode='Behavior'
        row = conn.execute(
            "SELECT entity_id, mode, ir_text FROM ir_rows"
        ).fetchone()
        assert row[0] == "E1"
        assert row[1] == "Behavior"
        assert row[2] == "FN FOO"
        conn.close()

    def test_ir_rows_migration_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE ir_rows (entity_id TEXT, mode TEXT, "
            "ir_text TEXT, ir_json TEXT, "
            "source_char_count INTEGER DEFAULT 0, ir_char_count INTEGER DEFAULT 0, "
            "source_token_count INTEGER DEFAULT 0, ir_token_count INTEGER DEFAULT 0, "
            "compression_ratio REAL DEFAULT 1.0, "
            "PRIMARY KEY(entity_id, mode))"
        )
        _ensure_ir_rows_composite_pk(conn)  # should be a no-op
        conn.close()


# ---------------------------------------------------------------------------
# ensure_store (integration)
# ---------------------------------------------------------------------------

class TestEnsureStore:
    def test_creates_store_directory(self, tmp_path):
        schema_path = Path(__file__).resolve().parent.parent.parent / "index" / "store" / "schema.json"
        paths = ensure_store(tmp_path, schema_path)
        assert paths["store_dir"].exists()
        assert paths["entities_db"].exists()
        assert paths["mapping_db"].exists()

    def test_idempotent(self, tmp_path):
        schema_path = Path(__file__).resolve().parent.parent.parent / "index" / "store" / "schema.json"
        paths1 = ensure_store(tmp_path, schema_path)
        paths2 = ensure_store(tmp_path, schema_path)
        assert paths1 == paths2

    def test_all_tables_created(self, tmp_path):
        schema_path = Path(__file__).resolve().parent.parent.parent / "index" / "store" / "schema.json"
        paths = ensure_store(tmp_path, schema_path)

        conn = sqlite3.connect(paths["entities_db"])
        for table in ["entities", "ir_rows", "modules", "file_metadata", "index_meta", "callers"]:
            assert table_exists(conn, table), f"Missing table: {table}"
        conn.close()

        mconn = sqlite3.connect(paths["mapping_db"])
        assert table_exists(mconn, "abbreviations")
        assert table_exists(mconn, "abbrev_meta")
        mconn.close()


# ---------------------------------------------------------------------------
# load_schema
# ---------------------------------------------------------------------------

class TestLoadSchema:
    def test_loads_json(self):
        schema_path = Path(__file__).resolve().parent.parent.parent / "index" / "store" / "schema.json"
        schema = load_schema(schema_path)
        assert "entities_db" in schema
        assert "mapping_db" in schema
        assert isinstance(schema["entities_db"], list)
