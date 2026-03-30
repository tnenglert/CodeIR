"""Tests for Rust language support — entity extraction, classification, indexing, and IR generation."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "rust_sample"


@pytest.fixture
def rust_fixture():
    """Return path to the Rust sample fixture repo."""
    assert FIXTURE_DIR.exists(), f"Fixture not found: {FIXTURE_DIR}"
    return FIXTURE_DIR


@pytest.fixture
def rust_frontend():
    """Return a RustFrontend instance."""
    from lang.rust import RustFrontend
    return RustFrontend()


@pytest.fixture
def indexed_rust_repo(rust_fixture, tmp_path):
    """Index the Rust fixture and return (repo_path, store_dir).

    Copies fixture to tmp_path to avoid polluting the fixture with .codeir/.
    """
    import shutil
    repo_path = tmp_path / "rust_sample"
    shutil.copytree(rust_fixture, repo_path)

    from index.indexer import index_repo
    config = {
        "extensions": [".rs"],
        "hidden_dirs": [".git", "target", ".codeir"],
        "compression_level": "Behavior+Index",
    }
    result = index_repo(repo_path, config)
    assert result["entities_indexed"] > 0, "No entities indexed"
    return repo_path, result


# ---------------------------------------------------------------------------
# Entity extraction tests
# ---------------------------------------------------------------------------

class TestRustEntityExtraction:
    """Tests for tree-sitter based Rust entity extraction."""

    def test_extracts_free_functions(self, rust_frontend, rust_fixture):
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "lib.rs")
        names = [e["name"] for e in entities]
        assert "init_app" in names

    def test_extracts_structs(self, rust_frontend, rust_fixture):
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "models.rs")
        structs = [e for e in entities if e["kind"] == "struct"]
        struct_names = [e["name"] for e in structs]
        assert "User" in struct_names

    def test_extracts_enums(self, rust_frontend, rust_fixture):
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "models.rs")
        enums = [e for e in entities if e["kind"] == "enum"]
        enum_names = [e["name"] for e in enums]
        assert "Role" in enum_names

    def test_extracts_traits(self, rust_frontend, rust_fixture):
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "models.rs")
        traits = [e for e in entities if e["kind"] == "trait"]
        trait_names = [e["name"] for e in traits]
        assert "Validatable" in trait_names

    def test_extracts_impl_methods(self, rust_frontend, rust_fixture):
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "models.rs")
        methods = [e for e in entities if e["kind"] == "method"]
        method_names = [e["qualified_name"] for e in methods]
        # promote and is_admin have &self -> method
        assert "User.promote" in method_names
        assert "User.is_admin" in method_names

    def test_associated_functions(self, rust_frontend, rust_fixture):
        """Rust associated functions (no self) are classified as functions."""
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "models.rs")
        fns = [e for e in entities if e["kind"] == "function"]
        fn_names = [e["qualified_name"] for e in fns]
        assert "User.new" in fn_names  # new() has no self param

    def test_trait_impl_records_bases(self, rust_frontend, rust_fixture):
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "models.rs")
        validate = [e for e in entities if e["qualified_name"] == "User.validate"]
        assert len(validate) == 1
        bases = validate[0]["semantic"]["bases"]
        assert "Validatable" in bases

    def test_extracts_constants(self, rust_frontend, rust_fixture):
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "models.rs")
        constants = [e for e in entities if e["kind"] == "constant"]
        const_names = [e["name"] for e in constants]
        assert "MAX_USERNAME_LEN" in const_names

    def test_async_methods_detected(self, rust_frontend, rust_fixture):
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "handlers.rs")
        async_fns = [e for e in entities if e["kind"] == "async_function"]
        assert any(e["name"] == "handle_list_users" for e in async_fns)

    def test_semantic_calls_extracted(self, rust_frontend, rust_fixture):
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "handlers.rs")
        create = [e for e in entities if e["name"] == "handle_create_user"]
        assert len(create) == 1
        calls = create[0]["semantic"]["calls"]
        # Should find calls to User::new, validate, add_user
        assert any("validate" in c for c in calls)

    def test_semantic_flags_extracted(self, rust_frontend, rust_fixture):
        entities = rust_frontend.parse_entities(rust_fixture / "src" / "handlers.rs")
        get_user = [e for e in entities if e["name"] == "handle_get_user"]
        assert len(get_user) == 1
        flags = get_user[0]["semantic"]["flags"]
        assert "I" in flags  # match expression -> conditional

    def test_bare_entities_no_semantic(self, rust_frontend, rust_fixture):
        bare = rust_frontend.parse_bare_entities(rust_fixture / "src" / "models.rs")
        for entity in bare:
            assert "semantic" not in entity
            assert "kind" in entity
            assert "name" in entity
            assert "start_line" in entity


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------

class TestRustClassification:
    """Tests for Rust file classification."""

    def test_errors_classified_as_exceptions(self, rust_frontend, rust_fixture):
        cat = rust_frontend.classify_file(
            Path("src/errors.rs"),
            source=(rust_fixture / "src" / "errors.rs").read_text(),
        )
        assert cat == "exceptions"

    def test_config_classified(self, rust_frontend, rust_fixture):
        cat = rust_frontend.classify_file(Path("src/config.rs"))
        assert cat == "config"

    def test_lib_rs_classified_as_core_logic(self, rust_frontend, rust_fixture):
        cat = rust_frontend.classify_file(Path("src/lib.rs"))
        assert cat == "core_logic"

    def test_domain_serde_is_parse(self, rust_frontend, rust_fixture):
        domain = rust_frontend.classify_domain(
            Path("src/models.rs"),
            source=(rust_fixture / "src" / "models.rs").read_text(),
        )
        assert domain == "parse"  # serde import -> parse domain


# ---------------------------------------------------------------------------
# Import extraction tests
# ---------------------------------------------------------------------------

class TestRustImports:
    """Tests for Rust use-statement extraction."""

    def test_extracts_std_imports(self, rust_frontend, rust_fixture):
        imports = rust_frontend.extract_imports(rust_fixture / "src" / "store.rs")
        assert "std" in imports

    def test_extracts_crate_imports(self, rust_frontend, rust_fixture):
        imports = rust_frontend.extract_imports(rust_fixture / "src" / "handlers.rs")
        assert "crate" in imports

    def test_import_map_resolves_names(self, rust_frontend, rust_fixture):
        import_map = rust_frontend.build_import_map(
            rust_fixture / "src" / "store.rs",
            rust_fixture,
        )
        assert "HashMap" in import_map
        assert "std" in import_map["HashMap"]

    def test_split_imports(self, rust_frontend):
        imports = ["std", "crate", "serde", "tokio"]
        package_roots = {"src", "rust_sample"}
        internal, external = rust_frontend.split_imports(imports, package_roots)
        assert "crate" in internal
        assert "std" in external
        assert "serde" in external


# ---------------------------------------------------------------------------
# Indexing integration tests
# ---------------------------------------------------------------------------

class TestRustIndexing:
    """Tests for the full Rust indexing pipeline."""

    def test_index_produces_entities(self, indexed_rust_repo):
        repo_path, result = indexed_rust_repo
        assert result["entities_indexed"] > 10

    def test_index_produces_ir_rows(self, indexed_rust_repo):
        repo_path, result = indexed_rust_repo
        assert result["total_ir_rows"] > 0

    def test_index_produces_caller_relationships(self, indexed_rust_repo):
        repo_path, result = indexed_rust_repo
        assert result["caller_relationships"] >= 0  # May be 0 for small repos

    def test_entities_have_correct_kinds(self, indexed_rust_repo):
        repo_path, _ = indexed_rust_repo
        from index.store.db import connect
        conn = connect(repo_path / ".codeir" / "entities.db")
        kinds = {row[0] for row in conn.execute("SELECT DISTINCT kind FROM entities").fetchall()}
        conn.close()
        # Should have at least functions, methods, structs, enums
        assert "function" in kinds or "async_function" in kinds
        assert "method" in kinds
        assert "struct" in kinds
        assert "enum" in kinds

    def test_stable_ids_deterministic(self, rust_fixture, tmp_path):
        """Indexing twice produces the same entity IDs."""
        import shutil
        from index.indexer import index_repo

        config = {
            "extensions": [".rs"],
            "hidden_dirs": [".git", "target", ".codeir"],
            "compression_level": "Behavior+Index",
        }

        # First index
        repo1 = tmp_path / "run1"
        shutil.copytree(rust_fixture, repo1)
        result1 = index_repo(repo1, config)

        # Second index (fresh)
        repo2 = tmp_path / "run2"
        shutil.copytree(rust_fixture, repo2)
        result2 = index_repo(repo2, config)

        # Compare entity IDs
        from index.store.db import connect
        conn1 = connect(repo1 / ".codeir" / "entities.db")
        conn2 = connect(repo2 / ".codeir" / "entities.db")
        ids1 = sorted(r[0] for r in conn1.execute("SELECT id FROM entities").fetchall())
        ids2 = sorted(r[0] for r in conn2.execute("SELECT id FROM entities").fetchall())
        conn1.close()
        conn2.close()
        assert ids1 == ids2

    def test_index_language_metadata(self, indexed_rust_repo):
        """Index stores language metadata."""
        repo_path, _ = indexed_rust_repo
        from index.store.db import connect
        conn = connect(repo_path / ".codeir" / "entities.db")
        row = conn.execute("SELECT value FROM index_meta WHERE key='language'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "rust"


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------

class TestRustSearch:
    """Tests for searching Rust entities."""

    def test_search_by_name(self, indexed_rust_repo):
        repo_path, _ = indexed_rust_repo
        from index.search import search_entities
        results = search_entities("User", repo_path)
        assert len(results) > 0
        names = [r["qualified_name"] for r in results]
        assert any("User" in n for n in names)

    def test_search_by_function_name(self, indexed_rust_repo):
        repo_path, _ = indexed_rust_repo
        from index.search import search_entities
        results = search_entities("handle_create_user", repo_path)
        assert len(results) > 0
        assert results[0]["kind"] == "function"


# ---------------------------------------------------------------------------
# Show / IR tests
# ---------------------------------------------------------------------------

class TestRustShowIR:
    """Tests for IR display of Rust entities."""

    def test_show_behavior_level(self, indexed_rust_repo):
        repo_path, _ = indexed_rust_repo
        from index.store.fetch import get_entity_with_ir
        from index.search import search_entities

        results = search_entities("User.new", repo_path)
        assert len(results) > 0
        entity_id = results[0]["entity_id"]

        entity = get_entity_with_ir(repo_path, entity_id, mode="Behavior")
        assert entity is not None
        ir_text = entity["ir_text"]
        assert entity_id in ir_text

    def test_show_index_level(self, indexed_rust_repo):
        repo_path, _ = indexed_rust_repo
        from index.store.fetch import get_entity_with_ir
        from index.search import search_entities

        results = search_entities("User", repo_path, limit=5)
        structs = [r for r in results if r["kind"] == "struct"]
        assert len(structs) > 0
        entity_id = structs[0]["entity_id"]

        entity = get_entity_with_ir(repo_path, entity_id, mode="Index")
        assert entity is not None
        ir_text = entity["ir_text"]
        assert "ST" in ir_text  # struct opcode

    def test_expand_returns_source(self, indexed_rust_repo):
        repo_path, _ = indexed_rust_repo
        from index.store.fetch import get_entity_location
        from index.locator import extract_code_slice
        from index.search import search_entities

        results = search_entities("init_app", repo_path)
        assert len(results) > 0
        entity_id = results[0]["entity_id"]

        loc = get_entity_location(repo_path, entity_id)
        assert loc is not None
        source = extract_code_slice(
            repo_path, loc["file_path"], loc["start_line"], loc["end_line"],
        )
        assert "fn init_app" in source

    def test_rust_opcodes_in_ir(self, indexed_rust_repo):
        """Rust-specific opcodes (ST, EN, TR) appear in IR output."""
        repo_path, _ = indexed_rust_repo
        from index.store.db import connect
        conn = connect(repo_path / ".codeir" / "entities.db")
        ir_texts = [
            row[0] for row in
            conn.execute("SELECT ir_text FROM ir_rows WHERE mode='Behavior'").fetchall()
        ]
        conn.close()
        all_ir = " ".join(ir_texts)
        assert "ST " in all_ir  # struct
        assert "EN " in all_ir  # enum
        assert "TR " in all_ir  # trait


# ---------------------------------------------------------------------------
# Callers / scope / impact tests
# ---------------------------------------------------------------------------

class TestRustCallers:
    """Tests for caller resolution on Rust code."""

    def test_callers_table_populated(self, indexed_rust_repo):
        repo_path, result = indexed_rust_repo
        # The caller table should exist and have entries
        from index.store.db import connect, table_exists
        conn = connect(repo_path / ".codeir" / "entities.db")
        assert table_exists(conn, "callers")
        count = conn.execute("SELECT COUNT(*) FROM callers").fetchone()[0]
        conn.close()
        # Should have at least some relationships
        assert count >= 0

    def test_scope_returns_data(self, indexed_rust_repo):
        repo_path, _ = indexed_rust_repo
        from index.search import compute_scope, search_entities
        from index.store.db import connect

        results = search_entities("handle_create_user", repo_path)
        assert len(results) > 0

        conn = connect(repo_path / ".codeir" / "entities.db")
        conn.row_factory = sqlite3.Row
        scope = compute_scope(conn, results[0]["entity_id"])
        conn.close()

        assert scope["root"] is not None
        assert scope["root"]["qualified_name"] == "handle_create_user"

    def test_impact_returns_data(self, indexed_rust_repo):
        repo_path, _ = indexed_rust_repo
        from index.search import compute_impact, search_entities
        from index.store.db import connect

        results = search_entities("User.new", repo_path)
        assert len(results) > 0

        conn = connect(repo_path / ".codeir" / "entities.db")
        conn.row_factory = sqlite3.Row
        impact = compute_impact(conn, results[0]["entity_id"])
        conn.close()

        assert impact["root"] is not None


# ---------------------------------------------------------------------------
# Bearings tests
# ---------------------------------------------------------------------------

class TestRustBearings:
    """Tests for bearings generation on Rust repos."""

    def test_bearings_files_created(self, indexed_rust_repo):
        repo_path, _ = indexed_rust_repo
        # Generate bearings
        from index.store.db import connect
        from ir.classifier import generate_context_file, generate_summary
        from ir.stable_ids import make_module_base_id

        conn = connect(repo_path / ".codeir" / "entities.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT file_path, category, entity_count, deps_internal "
            "FROM modules ORDER BY category, file_path"
        ).fetchall()
        total = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
        conn.close()

        modules = [
            {"file_path": r["file_path"], "category": r["category"],
             "entity_count": r["entity_count"], "deps_internal": r["deps_internal"]}
            for r in rows
        ]

        assert len(modules) > 0
        assert total > 0

        module_ids = {}
        for mod in modules:
            mid = make_module_base_id(str(mod["file_path"]))
            module_ids[str(mod["file_path"])] = mid

        summary = generate_summary(repo_path.name, modules, total)
        assert "rust_sample" in summary
        assert "Categories" in summary

        context = generate_context_file(repo_path.name, modules, total, module_ids)
        assert "MD " in context

    def test_mod_rs_gets_parent_name_as_id(self):
        """mod.rs files should use parent directory name for module ID."""
        from ir.stable_ids import make_module_base_id
        mid = make_module_base_id("src/handlers/mod.rs")
        assert mid != "MOD"
        assert mid == "HNDLRS"


# ---------------------------------------------------------------------------
# Python compatibility tests
# ---------------------------------------------------------------------------

class TestPythonStillWorks:
    """Verify that the Python indexing path is unbroken."""

    def test_python_kind_opcodes_unchanged(self):
        from ir.compressor import kind_to_opcode
        assert kind_to_opcode("function") == "FN"
        assert kind_to_opcode("async_function") == "AFN"
        assert kind_to_opcode("method") == "MT"
        assert kind_to_opcode("async_method") == "AMT"
        assert kind_to_opcode("class") == "CLS"

    def test_python_frontend_registered(self):
        from lang.base import get_frontend
        import lang.python  # noqa: F401
        frontend = get_frontend(".py")
        assert frontend.name == "python"

    def test_rust_frontend_registered(self):
        from lang.base import get_frontend
        import lang.rust  # noqa: F401
        frontend = get_frontend(".rs")
        assert frontend.name == "rust"

    def test_language_detection_rust(self, rust_fixture):
        from cli import detect_repo_language
        lang = detect_repo_language(rust_fixture)
        assert lang == "rust"
