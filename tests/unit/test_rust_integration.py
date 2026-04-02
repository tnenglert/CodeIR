"""Integration tests for Rust indexing — index the fixture and verify all CLI commands."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from index.indexer import index_repo
from index.search import search_entities, compute_impact, compute_scope, grep_entities
from index.store.db import connect
from index.store.fetch import get_entity_with_ir, get_entity_location


FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "rust_sample"


@pytest.fixture(scope="module")
def indexed_repo(tmp_path_factory):
    """Index the Rust fixture into a temporary copy and return the repo path."""
    tmp = tmp_path_factory.mktemp("rust_index")
    dest = tmp / "rust_sample"
    shutil.copytree(FIXTURE_PATH, dest, ignore=shutil.ignore_patterns(".codeir"))

    config = {
        "compression_level": "Behavior+Index",
        "hidden_dirs": [".git", ".codeir", "target"],
    }
    result = index_repo(dest, config)

    assert result["entities_indexed"] > 0
    assert result.get("language") == "rust"
    return dest


class TestIndexing:
    def test_entity_count(self, indexed_repo):
        """Should extract a reasonable number of entities."""
        conn = connect(indexed_repo / ".codeir" / "entities.db")
        count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        conn.close()
        assert count >= 40  # fixture has ~49 entities

    def test_entity_kinds(self, indexed_repo):
        """Should have Rust-specific entity kinds."""
        conn = connect(indexed_repo / ".codeir" / "entities.db")
        kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM entities").fetchall()}
        conn.close()
        assert "struct" in kinds
        assert "enum" in kinds
        assert "method" in kinds
        assert "async_method" in kinds
        assert "function" in kinds or "async_function" in kinds
        assert "trait" in kinds

    def test_language_stored(self, indexed_repo):
        """Index should store language=rust in index_meta."""
        conn = connect(indexed_repo / ".codeir" / "entities.db")
        row = conn.execute("SELECT value FROM index_meta WHERE key='language'").fetchone()
        conn.close()
        assert row[0] == "rust"

    def test_modules_classified(self, indexed_repo):
        """Module classification should produce meaningful categories."""
        conn = connect(indexed_repo / ".codeir" / "entities.db")
        cats = {r[0] for r in conn.execute("SELECT DISTINCT category FROM modules").fetchall()}
        conn.close()
        assert "config" in cats
        assert "core_logic" in cats
        assert "exceptions" in cats

    def test_ir_rows_generated(self, indexed_repo):
        """Should have IR rows at both Behavior and Index levels."""
        conn = connect(indexed_repo / ".codeir" / "entities.db")
        modes = {r[0] for r in conn.execute("SELECT DISTINCT mode FROM ir_rows").fetchall()}
        conn.close()
        assert "Behavior" in modes
        assert "Index" in modes


class TestSearch:
    def test_search_finds_struct(self, indexed_repo):
        results = search_entities("User", indexed_repo)
        ids = {r["entity_id"] for r in results}
        assert "USER" in ids

    def test_search_finds_method(self, indexed_repo):
        results = search_entities("promote", indexed_repo)
        assert any(r["entity_id"] == "PRMT" for r in results)

    def test_search_by_file(self, indexed_repo):
        results = search_entities("connection", indexed_repo)
        assert len(results) > 0


class TestShow:
    def test_show_struct(self, indexed_repo):
        result = get_entity_with_ir(indexed_repo, "USER", mode="Behavior")
        assert result is not None
        assert "ST USER" in result["ir_text"]

    def test_show_method(self, indexed_repo):
        result = get_entity_with_ir(indexed_repo, "PRMT", mode="Behavior")
        assert result is not None
        assert "MT PRMT" in result["ir_text"]
        assert "F=" in result["ir_text"]

    def test_show_async_method(self, indexed_repo):
        result = get_entity_with_ir(indexed_repo, "LSTSRS", mode="Behavior")
        assert result is not None
        assert "AMT LSTSRS" in result["ir_text"]

    def test_show_index_level(self, indexed_repo):
        result = get_entity_with_ir(indexed_repo, "USER", mode="Index")
        assert result is not None
        assert "ST USER" in result["ir_text"]


class TestExpand:
    def test_expand_returns_source(self, indexed_repo):
        loc = get_entity_location(indexed_repo, "PRMT")
        assert loc is not None
        from index.locator import extract_code_slice
        source = extract_code_slice(
            indexed_repo, loc["file_path"],
            loc["start_line"], loc["end_line"],
        )
        assert "fn promote" in source
        assert "Result" in source


class TestCallers:
    def test_caller_links_exist(self, indexed_repo):
        conn = connect(indexed_repo / ".codeir" / "entities.db")
        count = conn.execute("SELECT COUNT(*) FROM callers").fetchone()[0]
        conn.close()
        assert count > 0

    def test_promote_has_callers(self, indexed_repo):
        conn = connect(indexed_repo / ".codeir" / "entities.db")
        callers = conn.execute(
            "SELECT caller_id FROM callers WHERE entity_id = 'PRMT'"
        ).fetchall()
        conn.close()
        caller_ids = {r[0] for r in callers}
        # promote is called by update_role and test_user_promote_to_admin
        assert "UPDTRL" in caller_ids


class TestImpact:
    def test_impact_analysis(self, indexed_repo):
        conn = connect(indexed_repo / ".codeir" / "entities.db")
        conn.row_factory = sqlite3.Row
        result = compute_impact(conn, "LSTSRS", depth=2)
        conn.close()
        assert result["root"] is not None
        assert len(result["affected_files"]) > 0


class TestScope:
    def test_scope_includes_callers_and_siblings(self, indexed_repo):
        conn = connect(indexed_repo / ".codeir" / "entities.db")
        conn.row_factory = sqlite3.Row
        result = compute_scope(conn, "PRMT")
        conn.close()
        assert result["root"] is not None
        # promote has callers (update_role, test)
        assert len(result["callers"]) > 0
        # promote has siblings (new, is_admin, deactivate, etc.)
        assert len(result["siblings"]) > 0


class TestGrep:
    def test_grep_finds_pattern(self, indexed_repo):
        results = grep_entities("Result", indexed_repo)
        assert len(results) > 0
        # Should find entities and attach IR
        entity_results = [r for r in results if r["type"] == "entity"]
        assert len(entity_results) > 0


class TestDeterministicIds:
    def test_reindex_produces_same_ids(self, tmp_path):
        """Re-indexing from scratch should produce identical entity IDs."""
        dest = tmp_path / "rust_sample"
        shutil.copytree(FIXTURE_PATH, dest, ignore=shutil.ignore_patterns(".codeir"))

        config = {
            "compression_level": "Behavior+Index",
            "hidden_dirs": [".git", ".codeir", "target"],
        }

        # First index
        index_repo(dest, config)
        conn = connect(dest / ".codeir" / "entities.db")
        ids_first = sorted(r[0] for r in conn.execute("SELECT id FROM entities").fetchall())
        conn.close()

        # Delete and re-index
        shutil.rmtree(dest / ".codeir")
        index_repo(dest, config)
        conn = connect(dest / ".codeir" / "entities.db")
        ids_second = sorted(r[0] for r in conn.execute("SELECT id FROM entities").fetchall())
        conn.close()

        assert ids_first == ids_second
        assert len(ids_first) >= 40
