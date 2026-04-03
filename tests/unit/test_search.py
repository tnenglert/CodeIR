"""Tests for search, grep, impact, and scope logic."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from index.search import (
    _build_match_entry,
    compute_impact,
    compute_scope,
    search_entities,
    grep_entities,
)
from index.store.db import connect, ensure_store


def _make_test_store(tmp_path):
    """Create a fully initialized .codeir store with test data."""
    schema_path = Path(__file__).resolve().parent.parent.parent / "index" / "store" / "schema.json"
    store_paths = ensure_store(repo_path=tmp_path, schema_path=schema_path)
    return store_paths


def _populate_entities(conn, entities):
    """Insert entities into DB."""
    for e in entities:
        conn.execute(
            "INSERT INTO entities (id, kind, name, qualified_name, file_path, start_line, end_line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (e["id"], e["kind"], e["name"], e["qualified_name"],
             e["file_path"], e["start_line"], e["end_line"]),
        )
    conn.commit()


def _populate_ir_rows(conn, rows):
    """Insert IR rows into DB."""
    for r in rows:
        conn.execute(
            "INSERT INTO ir_rows (entity_id, mode, ir_text, ir_json) VALUES (?, ?, ?, ?)",
            (r["entity_id"], r["mode"], r["ir_text"], r.get("ir_json", "{}")),
        )
    conn.commit()


def _populate_callers(conn, callers):
    """Insert caller relationships."""
    for c in callers:
        conn.execute(
            "INSERT OR IGNORE INTO callers VALUES (?, ?, ?, ?, ?)",
            (c["entity_id"], c["caller_id"], c["caller_name"], c["caller_file"], c["resolution"]),
        )
    conn.commit()


def _populate_modules(conn, modules):
    """Insert module classifications."""
    for m in modules:
        conn.execute(
            "INSERT OR REPLACE INTO modules (file_path, category, content_hash, entity_count, indexed_at) "
            "VALUES (?, ?, ?, ?, '2024-01-01')",
            (m["file_path"], m["category"], "hash", m.get("entity_count", 1)),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# _build_match_entry
# ---------------------------------------------------------------------------

class TestBuildMatchEntry:
    def test_no_context(self):
        entry = _build_match_entry(3, "  foo = bar", ["a", "b", "  foo = bar", "d"], context=0)
        assert entry["line"] == 3
        assert entry["text"] == "  foo = bar"
        assert "context_before" not in entry

    def test_with_context(self):
        lines = ["line1", "line2", "line3", "line4", "line5"]
        entry = _build_match_entry(3, "line3", lines, context=1)
        assert entry["line"] == 3
        assert len(entry["context_before"]) == 1
        assert entry["context_before"][0]["line"] == 2
        assert len(entry["context_after"]) == 1
        assert entry["context_after"][0]["line"] == 4

    def test_context_at_start_of_file(self):
        lines = ["first", "second", "third"]
        entry = _build_match_entry(1, "first", lines, context=2)
        assert entry["context_before"] == []
        assert len(entry["context_after"]) == 2

    def test_context_at_end_of_file(self):
        lines = ["first", "second", "third"]
        entry = _build_match_entry(3, "third", lines, context=2)
        assert len(entry["context_before"]) == 2
        assert entry["context_after"] == []


# ---------------------------------------------------------------------------
# search_entities
# ---------------------------------------------------------------------------

class TestSearchEntities:
    def test_basic_search(self, tmp_path):
        store_paths = _make_test_store(tmp_path)
        conn = connect(store_paths["entities_db"])
        _populate_entities(conn, [
            {"id": "FOO", "kind": "function", "name": "foo",
             "qualified_name": "mod.foo", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
            {"id": "BAR", "kind": "function", "name": "bar",
             "qualified_name": "mod.bar", "file_path": "b.py",
             "start_line": 1, "end_line": 5},
        ])
        conn.close()

        results = search_entities("foo", tmp_path)
        assert len(results) == 1
        assert results[0]["entity_id"] == "FOO"

    def test_multi_term_ranking(self, tmp_path):
        store_paths = _make_test_store(tmp_path)
        conn = connect(store_paths["entities_db"])
        _populate_entities(conn, [
            {"id": "BOTH", "kind": "function", "name": "foo_bar",
             "qualified_name": "mod.foo_bar", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
            {"id": "ONE", "kind": "function", "name": "foo_baz",
             "qualified_name": "mod.foo_baz", "file_path": "b.py",
             "start_line": 1, "end_line": 5},
        ])
        conn.close()

        results = search_entities("foo bar", tmp_path)
        # BOTH matches both terms, ONE matches only "foo"
        assert len(results) == 2
        assert results[0]["entity_id"] == "BOTH"

    def test_empty_query(self, tmp_path):
        _make_test_store(tmp_path)
        results = search_entities("", tmp_path)
        assert results == []

    def test_no_matches(self, tmp_path):
        store_paths = _make_test_store(tmp_path)
        conn = connect(store_paths["entities_db"])
        _populate_entities(conn, [
            {"id": "FOO", "kind": "function", "name": "foo",
             "qualified_name": "mod.foo", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
        ])
        conn.close()
        results = search_entities("nonexistent", tmp_path)
        assert results == []

    def test_category_filter(self, tmp_path):
        store_paths = _make_test_store(tmp_path)
        conn = connect(store_paths["entities_db"])
        _populate_entities(conn, [
            {"id": "FOO", "kind": "function", "name": "foo",
             "qualified_name": "mod.foo", "file_path": "core.py",
             "start_line": 1, "end_line": 5},
            {"id": "FOO_T", "kind": "function", "name": "test_foo",
             "qualified_name": "tests.test_foo", "file_path": "test_core.py",
             "start_line": 1, "end_line": 5},
        ])
        _populate_modules(conn, [
            {"file_path": "core.py", "category": "core_logic"},
            {"file_path": "test_core.py", "category": "tests"},
        ])
        conn.close()

        results = search_entities("foo", tmp_path, category="core_logic")
        assert len(results) == 1
        assert results[0]["entity_id"] == "FOO"

    def test_missing_db_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            search_entities("foo", tmp_path)


# ---------------------------------------------------------------------------
# grep_entities
# ---------------------------------------------------------------------------

class TestGrepEntities:
    def test_basic_grep(self, tmp_path):
        store_paths = _make_test_store(tmp_path)
        # Create source file
        src = tmp_path / "a.py"
        src.write_text("def foo():\n    return 42\n")

        conn = connect(store_paths["entities_db"])
        _populate_entities(conn, [
            {"id": "FOO", "kind": "function", "name": "foo",
             "qualified_name": "mod.foo", "file_path": "a.py",
             "start_line": 1, "end_line": 2},
        ])
        _populate_ir_rows(conn, [
            {"entity_id": "FOO", "mode": "Behavior", "ir_text": "FN FOO C=-;F=R"},
        ])
        conn.close()

        results = grep_entities("return", tmp_path)
        assert len(results) == 1
        assert results[0]["type"] == "entity"
        assert results[0]["entity_id"] == "FOO"
        assert results[0]["matches"][0]["text"].strip() == "return 42"

    def test_grep_invalid_regex(self, tmp_path):
        _make_test_store(tmp_path)
        with pytest.raises(ValueError, match="Invalid regex"):
            grep_entities("[invalid", tmp_path)

    def test_grep_unmatched_lines(self, tmp_path):
        """Lines outside any entity span should be grouped as 'file' type."""
        store_paths = _make_test_store(tmp_path)
        src = tmp_path / "a.py"
        src.write_text("import os\n\ndef foo():\n    pass\n")

        conn = connect(store_paths["entities_db"])
        _populate_entities(conn, [
            {"id": "FOO", "kind": "function", "name": "foo",
             "qualified_name": "mod.foo", "file_path": "a.py",
             "start_line": 3, "end_line": 4},
        ])
        _populate_ir_rows(conn, [
            {"entity_id": "FOO", "mode": "Behavior", "ir_text": "FN FOO"},
        ])
        conn.close()

        results = grep_entities("import", tmp_path)
        assert len(results) == 1
        assert results[0]["type"] == "file"

    def test_grep_nested_entities_innermost(self, tmp_path):
        """A line inside a nested entity should match the innermost one."""
        store_paths = _make_test_store(tmp_path)
        src = tmp_path / "a.py"
        src.write_text(
            "class Outer:\n"       # line 1
            "    x = 1\n"          # line 2
            "    def inner(self):\n"  # line 3
            "        return x\n"   # line 4
            "    y = 2\n"          # line 5
        )

        conn = connect(store_paths["entities_db"])
        _populate_entities(conn, [
            {"id": "OUTER", "kind": "class", "name": "Outer",
             "qualified_name": "Outer", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
            {"id": "INNER", "kind": "method", "name": "inner",
             "qualified_name": "Outer.inner", "file_path": "a.py",
             "start_line": 3, "end_line": 4},
        ])
        _populate_ir_rows(conn, [
            {"entity_id": "OUTER", "mode": "Behavior", "ir_text": "CLS OUTER"},
            {"entity_id": "INNER", "mode": "Behavior", "ir_text": "MT INNER F=R"},
        ])
        conn.close()

        results = grep_entities("return", tmp_path)
        assert len(results) == 1
        assert results[0]["entity_id"] == "INNER"  # innermost, not OUTER

    def test_grep_path_filter(self, tmp_path):
        store_paths = _make_test_store(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "a.py").write_text("x = 1\n")
        (tmp_path / "tests" / "b.py").write_text("x = 1\n")

        conn = connect(store_paths["entities_db"])
        _populate_entities(conn, [
            {"id": "A", "kind": "function", "name": "a",
             "qualified_name": "a", "file_path": "src/a.py",
             "start_line": 1, "end_line": 1},
            {"id": "B", "kind": "function", "name": "b",
             "qualified_name": "b", "file_path": "tests/b.py",
             "start_line": 1, "end_line": 1},
        ])
        conn.close()

        results = grep_entities("x", tmp_path, path_filter="src")
        file_paths = {r.get("file_path") or r["matches"][0].get("file_path", "") for r in results}
        assert all("tests" not in fp for fp in file_paths)

    def test_grep_ignore_case(self, tmp_path):
        store_paths = _make_test_store(tmp_path)
        src = tmp_path / "a.py"
        src.write_text("MyClass = True\n")
        conn = connect(store_paths["entities_db"])
        _populate_entities(conn, [
            {"id": "MC", "kind": "class", "name": "MyClass",
             "qualified_name": "MyClass", "file_path": "a.py",
             "start_line": 1, "end_line": 1},
        ])
        conn.close()

        results_sensitive = grep_entities("myclass", tmp_path, ignore_case=False)
        results_insensitive = grep_entities("myclass", tmp_path, ignore_case=True)
        assert len(results_sensitive) == 0
        assert len(results_insensitive) == 1

    def test_grep_normalizes_escaped_pipe_alternation(self, tmp_path):
        store_paths = _make_test_store(tmp_path)
        src = tmp_path / "a.py"
        src.write_text("alpha()\nbeta()\ngamma()\n")
        conn = connect(store_paths["entities_db"])
        _populate_entities(conn, [
            {"id": "FOO", "kind": "function", "name": "foo",
             "qualified_name": "mod.foo", "file_path": "a.py",
             "start_line": 1, "end_line": 3},
        ])
        _populate_ir_rows(conn, [
            {"entity_id": "FOO", "mode": "Behavior", "ir_text": "FN FOO"},
        ])
        conn.close()

        plain = grep_entities("alpha|beta", tmp_path)
        escaped = grep_entities(r"alpha\|beta", tmp_path)

        assert plain == escaped
        assert len(escaped) == 1
        assert [m["text"] for m in escaped[0]["matches"]] == ["alpha()", "beta()"]


# ---------------------------------------------------------------------------
# compute_impact
# ---------------------------------------------------------------------------

class TestComputeImpact:
    def _setup(self, tmp_path):
        store_paths = _make_test_store(tmp_path)
        conn = connect(store_paths["entities_db"])
        conn.row_factory = sqlite3.Row
        return conn

    def test_missing_root(self, tmp_path):
        conn = self._setup(tmp_path)
        result = compute_impact(conn, "NONEXIST")
        assert result["root"] is None
        assert result["impact_by_depth"] == {}
        conn.close()

    def test_no_callers(self, tmp_path):
        conn = self._setup(tmp_path)
        _populate_entities(conn, [
            {"id": "ROOT", "kind": "function", "name": "root",
             "qualified_name": "mod.root", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
        ])
        result = compute_impact(conn, "ROOT")
        assert result["root"]["entity_id"] == "ROOT"
        assert result["impact_by_depth"] == {}
        conn.close()

    def test_single_depth_callers(self, tmp_path):
        conn = self._setup(tmp_path)
        _populate_entities(conn, [
            {"id": "ROOT", "kind": "function", "name": "root",
             "qualified_name": "mod.root", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
            {"id": "C1", "kind": "function", "name": "caller1",
             "qualified_name": "mod.caller1", "file_path": "a.py",
             "start_line": 6, "end_line": 10},
        ])
        _populate_callers(conn, [
            {"entity_id": "ROOT", "caller_id": "C1", "caller_name": "mod.caller1",
             "caller_file": "a.py", "resolution": "local"},
        ])
        result = compute_impact(conn, "ROOT", depth=2)
        assert 1 in result["impact_by_depth"]
        assert len(result["impact_by_depth"][1]) == 1
        assert result["impact_by_depth"][1][0]["entity_id"] == "C1"
        conn.close()

    def test_depth_limiting(self, tmp_path):
        """BFS should not traverse beyond the specified depth."""
        conn = self._setup(tmp_path)
        _populate_entities(conn, [
            {"id": "A", "kind": "function", "name": "a",
             "qualified_name": "a", "file_path": "a.py", "start_line": 1, "end_line": 1},
            {"id": "B", "kind": "function", "name": "b",
             "qualified_name": "b", "file_path": "a.py", "start_line": 2, "end_line": 2},
            {"id": "C", "kind": "function", "name": "c",
             "qualified_name": "c", "file_path": "a.py", "start_line": 3, "end_line": 3},
        ])
        _populate_callers(conn, [
            {"entity_id": "A", "caller_id": "B", "caller_name": "b",
             "caller_file": "a.py", "resolution": "local"},
            {"entity_id": "B", "caller_id": "C", "caller_name": "c",
             "caller_file": "a.py", "resolution": "local"},
        ])
        result = compute_impact(conn, "A", depth=1)
        assert 1 in result["impact_by_depth"]
        assert 2 not in result["impact_by_depth"]
        conn.close()

    def test_cycle_handling(self, tmp_path):
        """Circular caller relationships should not cause infinite loops."""
        conn = self._setup(tmp_path)
        _populate_entities(conn, [
            {"id": "A", "kind": "function", "name": "a",
             "qualified_name": "a", "file_path": "a.py", "start_line": 1, "end_line": 1},
            {"id": "B", "kind": "function", "name": "b",
             "qualified_name": "b", "file_path": "a.py", "start_line": 2, "end_line": 2},
        ])
        _populate_callers(conn, [
            {"entity_id": "A", "caller_id": "B", "caller_name": "b",
             "caller_file": "a.py", "resolution": "local"},
            {"entity_id": "B", "caller_id": "A", "caller_name": "a",
             "caller_file": "a.py", "resolution": "local"},
        ])
        # Should terminate without error
        result = compute_impact(conn, "A", depth=10)
        all_ids = [e["entity_id"] for d_list in result["impact_by_depth"].values() for e in d_list]
        assert len(all_ids) == len(set(all_ids))  # no duplicates
        conn.close()

    def test_affected_categories(self, tmp_path):
        conn = self._setup(tmp_path)
        _populate_entities(conn, [
            {"id": "ROOT", "kind": "function", "name": "root",
             "qualified_name": "root", "file_path": "core.py",
             "start_line": 1, "end_line": 1},
            {"id": "TEST", "kind": "function", "name": "test_root",
             "qualified_name": "test_root", "file_path": "test_core.py",
             "start_line": 1, "end_line": 1},
        ])
        _populate_modules(conn, [
            {"file_path": "core.py", "category": "core_logic"},
            {"file_path": "test_core.py", "category": "tests"},
        ])
        _populate_callers(conn, [
            {"entity_id": "ROOT", "caller_id": "TEST", "caller_name": "test_root",
             "caller_file": "test_core.py", "resolution": "fuzzy"},
        ])
        result = compute_impact(conn, "ROOT")
        assert "tests" in result["affected_categories"]
        conn.close()


# ---------------------------------------------------------------------------
# compute_scope
# ---------------------------------------------------------------------------

class TestComputeScope:
    def _setup(self, tmp_path):
        store_paths = _make_test_store(tmp_path)
        conn = connect(store_paths["entities_db"])
        conn.row_factory = sqlite3.Row
        return conn

    def test_missing_entity(self, tmp_path):
        conn = self._setup(tmp_path)
        result = compute_scope(conn, "NONEXIST")
        assert result["root"] is None
        conn.close()

    def test_callers_and_callees(self, tmp_path):
        conn = self._setup(tmp_path)
        _populate_entities(conn, [
            {"id": "TARGET", "kind": "function", "name": "target",
             "qualified_name": "target", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
            {"id": "CALLER", "kind": "function", "name": "caller",
             "qualified_name": "caller", "file_path": "a.py",
             "start_line": 6, "end_line": 10},
            {"id": "CALLEE", "kind": "function", "name": "callee",
             "qualified_name": "callee", "file_path": "b.py",
             "start_line": 1, "end_line": 5},
        ])
        _populate_callers(conn, [
            # CALLER calls TARGET
            {"entity_id": "TARGET", "caller_id": "CALLER", "caller_name": "caller",
             "caller_file": "a.py", "resolution": "local"},
            # TARGET calls CALLEE
            {"entity_id": "CALLEE", "caller_id": "TARGET", "caller_name": "target",
             "caller_file": "a.py", "resolution": "fuzzy"},
        ])
        result = compute_scope(conn, "TARGET")
        assert result["root"]["entity_id"] == "TARGET"
        assert len(result["callers"]) == 1
        assert result["callers"][0]["entity_id"] == "CALLER"
        assert len(result["callees"]) == 1
        assert result["callees"][0]["entity_id"] == "CALLEE"
        conn.close()

    def test_siblings(self, tmp_path):
        """Methods of the same class should appear as siblings."""
        conn = self._setup(tmp_path)
        _populate_entities(conn, [
            {"id": "M1", "kind": "method", "name": "method1",
             "qualified_name": "MyClass.method1", "file_path": "a.py",
             "start_line": 2, "end_line": 5},
            {"id": "M2", "kind": "method", "name": "method2",
             "qualified_name": "MyClass.method2", "file_path": "a.py",
             "start_line": 6, "end_line": 10},
            {"id": "OTHER", "kind": "method", "name": "other",
             "qualified_name": "OtherClass.other", "file_path": "a.py",
             "start_line": 11, "end_line": 15},
        ])
        result = compute_scope(conn, "M1")
        sibling_ids = [s["entity_id"] for s in result["siblings"]]
        assert "M2" in sibling_ids
        assert "OTHER" not in sibling_ids
        conn.close()

    def test_no_siblings_for_functions(self, tmp_path):
        """Top-level functions (no dot in qualified_name) shouldn't have siblings."""
        conn = self._setup(tmp_path)
        _populate_entities(conn, [
            {"id": "F1", "kind": "function", "name": "func1",
             "qualified_name": "func1", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
        ])
        result = compute_scope(conn, "F1")
        assert result["siblings"] == []
        conn.close()
