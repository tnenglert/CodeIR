"""Unit tests for the Rust language backend (tree-sitter entity extraction, classification)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from index.lang.rust_backend import RustBackend


@pytest.fixture
def backend():
    return RustBackend()


@pytest.fixture
def fixture_path():
    return Path(__file__).resolve().parent.parent / "fixtures" / "rust_sample"


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

class TestExtractEntities:
    def test_extracts_structs(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "models.rs")
        structs = [e for e in entities if e["kind"] == "struct"]
        names = {e["name"] for e in structs}
        assert "User" in names
        assert "TokenPayload" in names

    def test_extracts_enums(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "models.rs")
        enums = [e for e in entities if e["kind"] == "enum"]
        names = {e["name"] for e in enums}
        assert "UserRole" in names

    def test_extracts_traits(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "models.rs")
        traits = [e for e in entities if e["kind"] == "trait"]
        names = {e["name"] for e in traits}
        assert "JsonSerializable" in names

    def test_extracts_impl_methods_with_qualified_name(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "models.rs")
        methods = [e for e in entities if e["kind"] == "method"]
        qnames = {e["qualified_name"] for e in methods}
        assert "User.new" in qnames
        assert "User.is_admin" in qnames
        assert "User.promote" in qnames

    def test_extracts_trait_methods(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "models.rs")
        trait_methods = [e for e in entities if e["kind"] == "trait_method"]
        qnames = {e["qualified_name"] for e in trait_methods}
        assert "JsonSerializable.to_json" in qnames
        assert "JsonSerializable.from_json" in qnames

    def test_extracts_async_methods(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "handlers" / "user.rs")
        async_methods = [e for e in entities if e["kind"] == "async_method"]
        qnames = {e["qualified_name"] for e in async_methods}
        assert "UserHandler.list_users" in qnames
        assert "UserHandler.create_user" in qnames

    def test_extracts_async_functions(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "main.rs")
        async_fns = [e for e in entities if e["kind"] == "async_function"]
        names = {e["name"] for e in async_fns}
        assert "main" in names
        assert "run_server" in names

    def test_extracts_constants(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "config.rs")
        consts = [e for e in entities if e["kind"] == "constant"]
        names = {e["name"] for e in consts}
        assert "DEFAULT_HOST" in names
        assert "DEFAULT_PORT" in names

    def test_line_ranges_are_valid(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "models.rs")
        for e in entities:
            assert e["start_line"] > 0
            assert e["end_line"] >= e["start_line"]

    def test_bare_extraction_skips_semantic(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "models.rs", include_semantic=False)
        for e in entities:
            assert "semantic" not in e
            assert "kind" in e
            assert "name" in e


# ---------------------------------------------------------------------------
# Semantic analysis
# ---------------------------------------------------------------------------

class TestSemanticAnalysis:
    def test_calls_extracted(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "handlers" / "user.rs")
        create_user = next(e for e in entities if e["qualified_name"] == "UserHandler.create_user")
        calls = create_user["semantic"]["calls"]
        # create_user calls User.new, db.insert_user, is_empty, Err
        assert any("User" in c or "new" in c or "is_empty" in c for c in calls)

    def test_flags_if_expression(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "handlers" / "user.rs")
        create_user = next(e for e in entities if e["qualified_name"] == "UserHandler.create_user")
        assert "I" in create_user["semantic"]["flags"]

    def test_flags_return(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "models.rs")
        promote = next(e for e in entities if e["qualified_name"] == "User.promote")
        assert "R" in promote["semantic"]["flags"]

    def test_flags_error_try(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "db" / "connection.rs")
        insert = next(e for e in entities if e["qualified_name"] == "Database.insert_user")
        # insert_user uses ? operator
        assert "E" in insert["semantic"]["flags"]

    def test_flags_loop(self, backend, fixture_path):
        """Verify loop flag is detected when present."""
        entities = backend.extract_entities(fixture_path / "src" / "handlers" / "user.rs")
        # list_users filters with into_iter().filter() — closure flag W
        list_users = next(e for e in entities if e["qualified_name"] == "UserHandler.list_users")
        assert "W" in list_users["semantic"]["flags"]

    def test_assigns_counted(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "handlers" / "user.rs")
        list_users = next(e for e in entities if e["qualified_name"] == "UserHandler.list_users")
        # list_users has let users = ... and let active: Vec<User> = ...
        assert list_users["semantic"]["assigns"] >= 2

    def test_type_signature_extracted(self, backend, fixture_path):
        entities = backend.extract_entities(fixture_path / "src" / "config.rs")
        load = next(e for e in entities if e["qualified_name"] == "AppConfig.load")
        type_sig = load["semantic"]["type_sig"]
        assert type_sig["return_type"] is not None
        assert "Result" in type_sig["return_type"]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

class TestClassification:
    def test_config_file(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "src" / "config.rs")
        cat = backend.classify_file(Path("src/config.rs"), tree)
        assert cat == "config"

    def test_errors_file(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "src" / "errors.rs")
        cat = backend.classify_file(Path("src/errors.rs"), tree)
        assert cat == "exceptions"

    def test_models_is_schema(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "src" / "models.rs")
        cat = backend.classify_file(Path("src/models.rs"), tree)
        assert cat == "schema"

    def test_mod_rs_is_init(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "src" / "handlers" / "mod.rs")
        cat = backend.classify_file(Path("src/handlers/mod.rs"), tree)
        assert cat == "init"

    def test_lib_rs_is_init(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "src" / "lib.rs")
        cat = backend.classify_file(Path("src/lib.rs"), tree)
        assert cat == "init"

    def test_test_file_in_tests_dir(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "tests" / "integration_test.rs")
        cat = backend.classify_file(Path("tests/integration_test.rs"), tree)
        assert cat == "tests"


# ---------------------------------------------------------------------------
# Domain classification
# ---------------------------------------------------------------------------

class TestDomainClassification:
    def test_db_domain(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "src" / "db" / "connection.rs")
        domain = backend.classify_domain(Path("src/db/connection.rs"), tree)
        assert domain == "db"

    def test_config_domain(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "src" / "config.rs")
        domain = backend.classify_domain(Path("src/config.rs"), tree)
        assert domain == "config"


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------

class TestImportExtraction:
    def test_extracts_crate_names(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "src" / "main.rs")
        imports = backend.extract_imports(tree)
        assert "clap" in imports

    def test_extracts_internal_imports(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "src" / "handlers" / "user.rs")
        imports = backend.extract_imports(tree)
        assert "crate" in imports

    def test_package_root_discovery(self, backend, fixture_path):
        roots = backend.discover_package_roots(fixture_path)
        assert "rust_sample" in roots
        assert "crate" in roots

    def test_split_imports(self, backend, fixture_path):
        all_imports = ["crate", "serde", "std", "super"]
        roots = {"rust_sample", "crate", "super"}
        internal, external = backend.split_imports(all_imports, roots)
        assert "crate" in internal
        assert "super" in internal
        assert "serde" in external
        assert "std" in external


# ---------------------------------------------------------------------------
# Import map (for caller resolution)
# ---------------------------------------------------------------------------

class TestImportMap:
    def test_build_import_map(self, backend, fixture_path):
        tree = backend.parse_file(fixture_path / "src" / "handlers" / "user.rs")
        import_map = backend.build_import_map(tree, fixture_path / "src" / "handlers" / "user.rs", fixture_path)
        # use crate::models::{User, UserRole} should produce entries
        assert "User" in import_map
        assert "UserRole" in import_map
        assert "AppError" in import_map
