"""Tests for Rust language support — indexing, search, show, callers, bearings.

Uses the Rust fixture repo at tests/fixtures/rust_repo/.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Ensure project root is on sys.path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from index.indexer import index_repo
from index.search import search_entities, compute_impact
from index.store.db import connect
from index.store.fetch import (
    get_entity_with_ir,
    get_entity_all_levels,
    get_entity_location,
)
from index.store.stats import get_stats
from ir.stable_ids import type_prefix_for_kind, make_entity_base_id, make_module_base_id
from ir.compressor import kind_to_opcode
from languages import detect_language, get_language
from languages.rust_lang import RustLanguage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "rust_repo"


@pytest.fixture(scope="module")
def indexed_repo(tmp_path_factory):
    """Copy the fixture to a temp dir, index it, and return the path."""
    tmp_dir = tmp_path_factory.mktemp("rust_repo")
    # Copy fixture
    shutil.copytree(FIXTURE_DIR, tmp_dir, dirs_exist_ok=True)

    # Clean any existing .codeir
    codeir_dir = tmp_dir / ".codeir"
    if codeir_dir.exists():
        shutil.rmtree(codeir_dir)

    config = {
        "hidden_dirs": [".git", "target", ".codeir"],
        "compression_level": "Behavior+Index",
    }
    result = index_repo(tmp_dir, config)
    assert result.get("language") == "rust"
    assert result.get("entities_indexed", 0) > 0
    return tmp_dir, result


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    def test_detect_rust_by_cargo_toml(self):
        assert detect_language(FIXTURE_DIR) == "rust"

    def test_rust_language_registered(self):
        lang = get_language("rust")
        assert lang is not None
        assert lang.name == "rust"
        assert ".rs" in lang.extensions


# ---------------------------------------------------------------------------
# Entity type system
# ---------------------------------------------------------------------------

class TestRustEntityKinds:
    def test_type_prefixes(self):
        assert type_prefix_for_kind("struct") == "ST"
        assert type_prefix_for_kind("enum") == "EN"
        assert type_prefix_for_kind("trait") == "TR"
        assert type_prefix_for_kind("constant") == "CN"
        assert type_prefix_for_kind("function") == "FN"
        assert type_prefix_for_kind("method") == "MT"
        assert type_prefix_for_kind("async_function") == "AFN"
        assert type_prefix_for_kind("async_method") == "AMT"

    def test_opcodes(self):
        assert kind_to_opcode("struct") == "ST"
        assert kind_to_opcode("enum") == "EN"
        assert kind_to_opcode("trait") == "TR"
        assert kind_to_opcode("constant") == "CN"

    def test_module_base_id_mod_rs(self):
        assert make_module_base_id("src/models/mod.rs") == "MDLS"

    def test_module_base_id_lib_rs(self):
        assert make_module_base_id("src/lib.rs") == "SRC"

    def test_module_base_id_normal(self):
        assert make_module_base_id("src/utils/helpers.rs") == "HLPRS"


# ---------------------------------------------------------------------------
# Rust parser
# ---------------------------------------------------------------------------

class TestRustParser:
    def setup_method(self):
        self.lang = RustLanguage()

    def test_parse_entities_from_file(self):
        path = FIXTURE_DIR / "src" / "models" / "user.rs"
        entities = self.lang.parse_entities(path, include_semantic=True)
        names = {e["name"] for e in entities}
        assert "User" in names
        assert "UserRole" in names
        assert "Validatable" in names

    def test_entity_kinds(self):
        path = FIXTURE_DIR / "src" / "models" / "user.rs"
        entities = self.lang.parse_entities(path, include_semantic=True)
        by_name = {e["name"]: e for e in entities}

        assert by_name["User"]["kind"] == "struct"
        assert by_name["UserRole"]["kind"] == "enum"
        assert by_name["Validatable"]["kind"] == "trait"

    def test_method_extraction(self):
        path = FIXTURE_DIR / "src" / "models" / "user.rs"
        entities = self.lang.parse_entities(path, include_semantic=True)
        methods = [e for e in entities if e["kind"] == "method"]
        method_names = {e["name"] for e in methods}
        assert "new" in method_names
        assert "with_email" in method_names
        assert "display_name" in method_names
        assert "validate" in method_names
        assert "can_edit" in method_names

    def test_qualified_names(self):
        path = FIXTURE_DIR / "src" / "models" / "user.rs"
        entities = self.lang.parse_entities(path, include_semantic=True)
        by_name = {e["qualified_name"]: e for e in entities}
        assert "User.new" in by_name
        assert "User.display_name" in by_name
        assert "Validatable.validate" in by_name
        assert "UserRole.can_edit" in by_name

    def test_semantic_flags(self):
        path = FIXTURE_DIR / "src" / "handlers" / "api.rs"
        entities = self.lang.parse_entities(path, include_semantic=True)
        by_name = {e["qualified_name"]: e for e in entities}

        # fetch_remote_user should have A (async) and E (error/?) flags
        fetch = by_name["fetch_remote_user"]
        assert "A" in fetch["semantic"]["flags"]
        assert "E" in fetch["semantic"]["flags"]

        # find_user should have I (if) and L (loop) and R (return)
        find = by_name["find_user"]
        flags = find["semantic"]["flags"]
        assert "L" in flags  # for loop
        assert "R" in flags  # return

    def test_semantic_calls(self):
        path = FIXTURE_DIR / "src" / "handlers" / "api.rs"
        entities = self.lang.parse_entities(path, include_semantic=True)
        by_name = {e["qualified_name"]: e for e in entities}

        # create_user should call User.new and validate
        create = by_name["create_user"]
        calls = create["semantic"]["calls"]
        assert "User.new" in calls or "new" in calls

    def test_derive_extraction(self):
        path = FIXTURE_DIR / "src" / "models" / "user.rs"
        entities = self.lang.parse_entities(path, include_semantic=True)
        by_name = {e["name"]: e for e in entities}

        user_bases = by_name["User"]["semantic"]["bases"]
        assert "Serialize" in user_bases
        assert "Deserialize" in user_bases
        assert "Debug" in user_bases
        assert "Clone" in user_bases

    def test_error_enum_flag(self):
        path = FIXTURE_DIR / "src" / "models" / "error.rs"
        entities = self.lang.parse_entities(path, include_semantic=True)
        by_name = {e["name"]: e for e in entities}
        assert "X" in by_name["AppError"]["semantic"]["flags"]

    def test_constant_extraction(self):
        path = FIXTURE_DIR / "src" / "main.rs"
        entities = self.lang.parse_entities(path, include_semantic=True)
        by_name = {e["name"]: e for e in entities}
        assert "VERSION" in by_name
        assert by_name["VERSION"]["kind"] == "constant"

    def test_async_function(self):
        path = FIXTURE_DIR / "src" / "main.rs"
        entities = self.lang.parse_entities(path, include_semantic=True)
        by_name = {e["name"]: e for e in entities}
        assert by_name["shutdown_signal"]["kind"] == "async_function"

    def test_bare_entities(self):
        path = FIXTURE_DIR / "src" / "models" / "user.rs"
        entities = self.lang.parse_entities(path, include_semantic=False)
        assert len(entities) > 0
        # Bare entities should have no semantic field
        for e in entities:
            assert "semantic" not in e


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

class TestRustClassification:
    def setup_method(self):
        self.lang = RustLanguage()

    def test_mod_rs_is_init(self):
        path = FIXTURE_DIR / "src" / "models" / "mod.rs"
        tree = self.lang.parse_ast(path)
        assert self.lang.classify_file(Path("src/models/mod.rs"), tree) == "init"

    def test_error_rs_is_exceptions(self):
        path = FIXTURE_DIR / "src" / "models" / "error.rs"
        tree = self.lang.parse_ast(path)
        assert self.lang.classify_file(Path("src/models/error.rs"), tree) == "exceptions"

    def test_config_rs_is_config(self):
        path = FIXTURE_DIR / "src" / "utils" / "config.rs"
        tree = self.lang.parse_ast(path)
        assert self.lang.classify_file(Path("src/utils/config.rs"), tree) == "config"

    def test_main_rs_is_core_logic(self):
        path = FIXTURE_DIR / "src" / "main.rs"
        tree = self.lang.parse_ast(path)
        assert self.lang.classify_file(Path("src/main.rs"), tree) == "core_logic"

    def test_user_rs_is_schema(self):
        """user.rs has Serialize/Deserialize derives → should be schema."""
        path = FIXTURE_DIR / "src" / "models" / "user.rs"
        tree = self.lang.parse_ast(path)
        cat = self.lang.classify_file(Path("src/models/user.rs"), tree)
        assert cat == "schema"

    def test_domain_detection(self):
        path = FIXTURE_DIR / "src" / "handlers" / "api.rs"
        tree = self.lang.parse_ast(path)
        domain = self.lang.classify_domain(Path("src/handlers/api.rs"), tree)
        assert domain == "http"


# ---------------------------------------------------------------------------
# Import/dependency resolution
# ---------------------------------------------------------------------------

class TestRustImports:
    def setup_method(self):
        self.lang = RustLanguage()

    def test_extract_import_names(self):
        path = FIXTURE_DIR / "src" / "models" / "user.rs"
        tree = self.lang.parse_ast(path)
        imports = self.lang.extract_import_names(tree)
        assert "serde" in imports

    def test_build_import_map(self):
        path = FIXTURE_DIR / "src" / "handlers" / "api.rs"
        tree = self.lang.parse_ast(path)
        imap = self.lang.build_import_map(tree, path, FIXTURE_DIR)
        assert "AppError" in imap
        assert "User" in imap
        assert "Config" in imap

    def test_discover_package_roots(self):
        roots = self.lang.discover_package_roots(FIXTURE_DIR)
        assert "crate" in roots
        assert "sample_app" in roots


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

class TestRustIndexing:
    def test_indexing_produces_entities(self, indexed_repo):
        repo_path, result = indexed_repo
        assert result["entities_indexed"] > 0
        assert result["total_entities"] > 0

    def test_indexing_produces_ir_rows(self, indexed_repo):
        repo_path, result = indexed_repo
        assert result["ir_rows"] > 0
        assert result["total_ir_rows"] > 0

    def test_indexing_detects_rust(self, indexed_repo):
        repo_path, result = indexed_repo
        assert result["language"] == "rust"

    def test_caller_relationships(self, indexed_repo):
        repo_path, result = indexed_repo
        assert result["caller_relationships"] > 0

    def test_stable_ids_deterministic(self, indexed_repo):
        """Re-indexing should produce the same entity IDs."""
        repo_path, result1 = indexed_repo

        # Get current entity IDs
        conn = connect(repo_path / ".codeir" / "entities.db")
        ids1 = sorted(row[0] for row in conn.execute("SELECT id FROM entities").fetchall())
        conn.close()

        # Force re-index
        shutil.rmtree(repo_path / ".codeir")
        config = {
            "hidden_dirs": [".git", "target", ".codeir"],
            "compression_level": "Behavior+Index",
        }
        result2 = index_repo(repo_path, config)

        conn = connect(repo_path / ".codeir" / "entities.db")
        ids2 = sorted(row[0] for row in conn.execute("SELECT id FROM entities").fetchall())
        conn.close()

        assert ids1 == ids2


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestRustSearch:
    def test_search_by_name(self, indexed_repo):
        repo_path, _ = indexed_repo
        results = search_entities("User", repo_path)
        names = {r["qualified_name"] for r in results}
        assert "User" in names

    def test_search_finds_structs(self, indexed_repo):
        repo_path, _ = indexed_repo
        results = search_entities("Config", repo_path)
        kinds = {r["kind"] for r in results}
        assert "struct" in kinds

    def test_search_finds_traits(self, indexed_repo):
        repo_path, _ = indexed_repo
        results = search_entities("Validatable", repo_path)
        assert len(results) > 0
        assert results[0]["kind"] == "trait"

    def test_search_finds_enums(self, indexed_repo):
        repo_path, _ = indexed_repo
        results = search_entities("AppError", repo_path)
        assert len(results) > 0
        kinds = {r["kind"] for r in results}
        assert "enum" in kinds

    def test_search_by_category(self, indexed_repo):
        repo_path, _ = indexed_repo
        results = search_entities("validate", repo_path, category="schema")
        assert len(results) > 0


# ---------------------------------------------------------------------------
# Show (IR display)
# ---------------------------------------------------------------------------

class TestRustShow:
    def test_show_behavior_level(self, indexed_repo):
        repo_path, _ = indexed_repo
        result = get_entity_with_ir(repo_path, "USER", mode="Behavior")
        assert result is not None
        assert "ST" in result["ir_text"]
        assert "USER" in result["ir_text"]

    def test_show_index_level(self, indexed_repo):
        repo_path, _ = indexed_repo
        result = get_entity_with_ir(repo_path, "USER", mode="Index")
        assert result is not None
        assert "ST" in result["ir_text"]

    def test_show_all_levels(self, indexed_repo):
        repo_path, _ = indexed_repo
        results = get_entity_all_levels(repo_path, "USER")
        modes = {r["mode"] for r in results}
        assert "Behavior" in modes
        assert "Index" in modes

    def test_behavior_ir_has_calls(self, indexed_repo):
        repo_path, _ = indexed_repo
        result = get_entity_with_ir(repo_path, "FTCHRMTSR", mode="Behavior")
        assert result is not None
        assert "C=" in result["ir_text"]

    def test_behavior_ir_has_flags(self, indexed_repo):
        repo_path, _ = indexed_repo
        result = get_entity_with_ir(repo_path, "FTCHRMTSR", mode="Behavior")
        assert result is not None
        assert "F=" in result["ir_text"]

    def test_behavior_ir_has_derives(self, indexed_repo):
        repo_path, _ = indexed_repo
        result = get_entity_with_ir(repo_path, "USER", mode="Behavior")
        assert result is not None
        assert "B=" in result["ir_text"]


# ---------------------------------------------------------------------------
# Expand (raw source)
# ---------------------------------------------------------------------------

class TestRustExpand:
    def test_expand_struct(self, indexed_repo):
        repo_path, _ = indexed_repo
        loc = get_entity_location(repo_path, "USER")
        assert loc is not None
        assert loc["kind"] == "struct"
        assert loc["file_path"] == "src/models/user.rs"

    def test_expand_method(self, indexed_repo):
        repo_path, _ = indexed_repo
        loc = get_entity_location(repo_path, "DSPLYNM")
        assert loc is not None
        assert loc["kind"] == "method"


# ---------------------------------------------------------------------------
# Callers
# ---------------------------------------------------------------------------

class TestRustCallers:
    def test_callers_for_validate(self, indexed_repo):
        repo_path, _ = indexed_repo
        conn = connect(repo_path / ".codeir" / "entities.db")
        rows = conn.execute(
            "SELECT caller_id, resolution FROM callers WHERE entity_id = 'VLDT'"
        ).fetchall()
        conn.close()
        # validate is called by create_user and fetch_remote_user
        caller_ids = {r[0] for r in rows}
        assert len(caller_ids) >= 1

    def test_impact_analysis(self, indexed_repo):
        repo_path, _ = indexed_repo
        conn = connect(repo_path / ".codeir" / "entities.db")
        conn.row_factory = sqlite3.Row
        impact = compute_impact(conn=conn, entity_id="VLDT")
        conn.close()
        assert impact.get("root") is not None


# ---------------------------------------------------------------------------
# Bearings
# ---------------------------------------------------------------------------

class TestRustBearings:
    def test_bearings_generation(self, indexed_repo):
        repo_path, _ = indexed_repo
        from ir.classifier import generate_summary, generate_context_file

        conn = connect(repo_path / ".codeir" / "entities.db")
        modules = conn.execute(
            "SELECT file_path, category, entity_count, deps_internal FROM modules"
        ).fetchall()
        conn.close()

        module_dicts = [
            {"file_path": m[0], "category": m[1], "entity_count": m[2], "deps_internal": m[3]}
            for m in modules
        ]

        summary = generate_summary("rust_repo", module_dicts, 35)
        assert "rust_repo" in summary
        assert "core_logic" in summary

    def test_bearings_has_rust_categories(self, indexed_repo):
        repo_path, _ = indexed_repo
        conn = connect(repo_path / ".codeir" / "entities.db")
        categories = {
            row[0] for row in
            conn.execute("SELECT DISTINCT category FROM modules").fetchall()
        }
        conn.close()

        # Should have meaningful categories
        assert "init" in categories  # mod.rs files
        assert "core_logic" in categories
        assert "config" in categories


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestRustStats:
    def test_stats_entity_count(self, indexed_repo):
        repo_path, _ = indexed_repo
        stats = get_stats(repo_path)
        assert stats["entity_count"] > 0

    def test_stats_has_rust_kinds(self, indexed_repo):
        repo_path, _ = indexed_repo
        stats = get_stats(repo_path)
        kinds = {k["kind"] for k in stats["entities_by_kind"]}
        assert "struct" in kinds
        assert "enum" in kinds
        assert "trait" in kinds
        assert "method" in kinds
        assert "function" in kinds

    def test_stats_file_coverage(self, indexed_repo):
        repo_path, _ = indexed_repo
        stats = get_stats(repo_path)
        fc = stats["file_coverage"]
        assert fc["files_indexed"] > 0
        assert fc["files_with_entities"] > 0


# ---------------------------------------------------------------------------
# Python compatibility preserved
# ---------------------------------------------------------------------------

class TestPythonCompatibility:
    def test_python_entity_kinds_unchanged(self):
        assert type_prefix_for_kind("function") == "FN"
        assert type_prefix_for_kind("async_function") == "AFN"
        assert type_prefix_for_kind("method") == "MT"
        assert type_prefix_for_kind("async_method") == "AMT"
        assert type_prefix_for_kind("class") == "CLS"
        assert type_prefix_for_kind("module") == "MD"

    def test_python_language_registered(self):
        lang = get_language("python")
        assert lang is not None
        assert lang.name == "python"
        assert ".py" in lang.extensions

    def test_python_module_base_id_unchanged(self):
        assert make_module_base_id("auth/__init__.py") == "AUTH"
        assert make_module_base_id("sessions.py") == "SSSNS"
