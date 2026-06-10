"""Tests for core indexer logic: ID assignment, change detection, compression level resolution."""

import sqlite3

from index.indexer import (
    _assign_entity_ids,
    _collect_existing_ids_by_base,
    _detect_changes,
    _entity_base_from_id,
    _next_entity_id,
    _primary_language,
    _refine_module_domains_from_entities,
    index_repo,
    map_legacy_mode_to_level,
    resolve_compression_level,
)
from ir.classifier import DomainDecision

# ---------------------------------------------------------------------------
# _entity_base_from_id
# ---------------------------------------------------------------------------

class TestEntityBaseFromId:
    def test_plain_id(self):
        assert _entity_base_from_id("AUTH") == "AUTH"

    def test_dotted_suffix(self):
        assert _entity_base_from_id("AUTH.02") == "AUTH"

    def test_dotted_suffix_03(self):
        assert _entity_base_from_id("RDTKN.03") == "RDTKN"

    def test_three_digit_suffix_not_stripped(self):
        """Three-digit suffixes are not collision suffixes (only 2-digit are)."""
        assert _entity_base_from_id("FOO.123") == "FOO.123"

    def test_non_numeric_suffix(self):
        assert _entity_base_from_id("FOO.bar") == "FOO.bar"

    def test_single_dot_numeric_but_not_two_digits(self):
        assert _entity_base_from_id("FOO.2") == "FOO.2"

    def test_nested_dots(self):
        """Multi-level dotted names: only last .XX is stripped."""
        assert _entity_base_from_id("A.B.02") == "A.B"

    def test_empty_string(self):
        assert _entity_base_from_id("") == ""


# ---------------------------------------------------------------------------
# _next_entity_id
# ---------------------------------------------------------------------------

class TestNextEntityId:
    def test_base_available(self):
        assert _next_entity_id("FOO", set()) == "FOO"

    def test_base_taken(self):
        assert _next_entity_id("FOO", {"FOO"}) == "FOO.02"

    def test_base_and_02_taken(self):
        assert _next_entity_id("FOO", {"FOO", "FOO.02"}) == "FOO.03"

    def test_gap_in_sequence(self):
        """Should fill the first available slot."""
        assert _next_entity_id("FOO", {"FOO", "FOO.02", "FOO.04"}) == "FOO.03"

    def test_many_collisions(self):
        used = {"FOO"} | {f"FOO.{i:02d}" for i in range(2, 20)}
        assert _next_entity_id("FOO", used) == "FOO.20"


# ---------------------------------------------------------------------------
# _assign_entity_ids
# ---------------------------------------------------------------------------

class TestAssignEntityIds:
    def test_single_entity(self):
        entities = [
            {"kind": "function", "qualified_name": "mod.foo", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
        ]
        _assign_entity_ids(entities)
        assert "id" in entities[0]
        assert isinstance(entities[0]["id"], str)
        assert len(entities[0]["id"]) > 0

    def test_two_entities_same_base_get_different_ids(self):
        entities = [
            {"kind": "function", "qualified_name": "mod.foo", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
            {"kind": "function", "qualified_name": "other.foo", "file_path": "b.py",
             "start_line": 1, "end_line": 5},
        ]
        _assign_entity_ids(entities)
        assert entities[0]["id"] != entities[1]["id"]

    def test_no_duplicate_ids(self):
        """Even many entities with the same base name must all get unique IDs."""
        entities = [
            {"kind": "function", "qualified_name": f"pkg{i}.foo", "file_path": f"f{i}.py",
             "start_line": 1, "end_line": 5}
            for i in range(10)
        ]
        _assign_entity_ids(entities)
        ids = [e["id"] for e in entities]
        assert len(ids) == len(set(ids))

    def test_existing_ids_respected(self):
        """New entities don't collide with pre-existing IDs from unchanged files."""
        from ir.stable_ids import make_entity_base_id
        base = make_entity_base_id(kind="function", qualified_name="mod.foo")
        existing = {base: {base}}  # base ID already taken

        entities = [
            {"kind": "function", "qualified_name": "mod.foo", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
        ]
        _assign_entity_ids(entities, existing_ids_by_base=existing)
        assert entities[0]["id"] != base  # must get a suffixed ID

    def test_deterministic_ordering(self):
        """Same input should always produce the same ID assignment."""
        entities1 = [
            {"kind": "function", "qualified_name": "b.foo", "file_path": "b.py",
             "start_line": 10, "end_line": 20},
            {"kind": "function", "qualified_name": "a.foo", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
        ]
        entities2 = [
            {"kind": "function", "qualified_name": "a.foo", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
            {"kind": "function", "qualified_name": "b.foo", "file_path": "b.py",
             "start_line": 10, "end_line": 20},
        ]
        _assign_entity_ids(entities1)
        _assign_entity_ids(entities2)
        # After sorting inside _assign_entity_ids, a.py entity should get base, b.py gets .02
        id_map1 = {e["qualified_name"]: e["id"] for e in entities1}
        id_map2 = {e["qualified_name"]: e["id"] for e in entities2}
        assert id_map1 == id_map2


# ---------------------------------------------------------------------------
# _collect_existing_ids_by_base
# ---------------------------------------------------------------------------

class TestCollectExistingIds:
    def test_groups_by_base(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO entities VALUES (?)",
                         [("FOO",), ("FOO.02",), ("BAR",)])
        result = _collect_existing_ids_by_base(conn)
        assert result["FOO"] == {"FOO", "FOO.02"}
        assert result["BAR"] == {"BAR"}
        conn.close()

    def test_empty_table(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY)")
        result = _collect_existing_ids_by_base(conn)
        assert result == {}
        conn.close()


# ---------------------------------------------------------------------------
# resolve_compression_level
# ---------------------------------------------------------------------------

class TestResolveCompressionLevel:
    def test_exact_level(self):
        assert resolve_compression_level({"compression_level": "Behavior"}) == "Behavior"

    def test_case_insensitive(self):
        assert resolve_compression_level({"compression_level": "behavior"}) == "Behavior"

    def test_source_level(self):
        assert resolve_compression_level({"compression_level": "Source"}) == "Source"

    def test_index_level(self):
        assert resolve_compression_level({"compression_level": "Index"}) == "Index"

    def test_all_level(self):
        assert resolve_compression_level({"compression_level": "all"}) == "all"

    def test_behavior_plus_index(self):
        assert resolve_compression_level({"compression_level": "Behavior+Index"}) == "Behavior+Index"

    def test_legacy_mode_a(self):
        assert resolve_compression_level({"compression_mode": "a"}) == "Index"

    def test_legacy_mode_b(self):
        assert resolve_compression_level({"compression_mode": "b"}) == "Behavior"

    def test_legacy_mode_hybrid(self):
        assert resolve_compression_level({"compression_mode": "hybrid"}) == "Behavior"

    def test_default_when_empty(self):
        assert resolve_compression_level({}) == "Behavior"

    def test_level_takes_precedence_over_mode(self):
        assert resolve_compression_level({
            "compression_level": "Index",
            "compression_mode": "b",
        }) == "Index"

    def test_unknown_mode_defaults_to_behavior(self):
        assert resolve_compression_level({"compression_mode": "xyz"}) == "Behavior"


# ---------------------------------------------------------------------------
# map_legacy_mode_to_level
# ---------------------------------------------------------------------------

class TestMapLegacyMode:
    def test_a_maps_to_index(self):
        assert map_legacy_mode_to_level("a") == "Index"

    def test_b_maps_to_behavior(self):
        assert map_legacy_mode_to_level("b") == "Behavior"

    def test_hybrid_maps_to_behavior(self):
        assert map_legacy_mode_to_level("hybrid") == "Behavior"

    def test_unknown_defaults_to_behavior(self):
        assert map_legacy_mode_to_level("unknown") == "Behavior"

    def test_case_insensitive(self):
        assert map_legacy_mode_to_level("A") == "Index"
        assert map_legacy_mode_to_level("B") == "Behavior"

    def test_whitespace_stripped(self):
        assert map_legacy_mode_to_level("  a  ") == "Index"


# ---------------------------------------------------------------------------
# _primary_language
# ---------------------------------------------------------------------------

class TestPrimaryLanguage:
    def test_single(self):
        assert _primary_language(["python"]) == "python"

    def test_multiple(self):
        assert _primary_language(["python", "rust"]) == "mixed"

    def test_empty(self):
        """Empty list should return 'mixed' (or handle gracefully)."""
        assert _primary_language([]) == "mixed"


# ---------------------------------------------------------------------------
# _detect_changes
# ---------------------------------------------------------------------------

class TestDetectChanges:
    def _make_db(self, stored_hashes):
        """Create an in-memory DB with file_metadata table."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE file_metadata (file_path TEXT PRIMARY KEY, content_hash TEXT)"
        )
        for path, hash_val in stored_hashes.items():
            conn.execute("INSERT INTO file_metadata VALUES (?, ?)", (path, hash_val))
        conn.commit()
        return conn

    def test_new_file_detected_as_changed(self, tmp_path):
        conn = self._make_db({})
        f = tmp_path / "new.py"
        f.write_text("print('hello')")
        changed, unchanged = _detect_changes(conn, [f], tmp_path)
        assert f in changed
        assert unchanged == []
        conn.close()

    def test_unchanged_file_detected(self, tmp_path):
        f = tmp_path / "same.py"
        f.write_text("x = 1")
        from index.locator import compute_file_content_hash
        h = compute_file_content_hash(f)
        conn = self._make_db({"same.py": h})
        changed, unchanged = _detect_changes(conn, [f], tmp_path)
        assert f in unchanged
        assert changed == []
        conn.close()

    def test_modified_file_detected(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1")
        conn = self._make_db({"mod.py": "oldhash"})
        changed, unchanged = _detect_changes(conn, [f], tmp_path)
        assert f in changed
        assert unchanged == []
        conn.close()


def test_index_repo_marks_callers_stale_when_pass4_fails(monkeypatch, tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(
        "def helper():\n    return 1\n\n\ndef call_helper():\n    return helper()\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "index.indexer.build_callers_table",
        lambda repo_path, db_path: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = index_repo(
        tmp_path,
        {
            "compression_level": "Behavior",
            "hidden_dirs": [".git", ".codeir", "__pycache__"],
        },
    )

    assert result["callers_status"] == "stale"
    assert "boom" in result["callers_error"]
    assert result["total_entities"] >= 2

    conn = sqlite3.connect(tmp_path / ".codeir" / "entities.db")
    meta = dict(conn.execute("SELECT key, value FROM index_meta").fetchall())
    conn.close()

    assert meta["callers_status"] == "stale"
    assert "boom" in meta["callers_error"]


def test_index_repo_emits_progress_events(monkeypatch, tmp_path):
    (tmp_path / "sample.py").write_text(
        "def helper():\n    return 1\n\n\ndef call_helper():\n    return helper()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "index.indexer.build_callers_table",
        lambda repo_path, db_path: (0, []),
    )
    events = []

    index_repo(
        tmp_path,
        {
            "compression_level": "Behavior",
            "hidden_dirs": [".git", ".codeir", "__pycache__"],
            "_progress": lambda phase, stats: events.append((phase, stats)),
        },
    )

    phases = [phase for phase, _ in events]
    assert phases[:2] == ["setup", "discovery"]
    assert "bare_parse" in phases
    assert "semantic_parse" in phases
    assert "ir_persist" in phases
    assert "caller_graph_start" in phases
    assert "caller_graph_done" in phases
    assert phases[-1] == "complete"
    discovery = dict(events)["discovery"]
    assert discovery["files_scanned"] == 1
    assert discovery["files_changed"] == 1


class TestModuleDomainRefinement:
    def test_upgrades_misc_module_from_entity_votes(self):
        file_domains = {"pkg/project_manager.py": "misc"}
        decisions = {
            "pkg/project_manager.py": DomainDecision("misc", "no_signal", "unresolved")
        }
        full_entities = [
            {
                "file_path": "pkg/project_manager.py",
                "name": "save_file_to_project",
                "qualified_name": "pkg.project_manager.ProjectManager.save_file_to_project",
                "semantic": {"calls": ["read_path"]},
            },
            {
                "file_path": "pkg/project_manager.py",
                "name": "get_project_files",
                "qualified_name": "pkg.project_manager.ProjectManager.get_project_files",
                "semantic": {"calls": ["write_file"]},
            },
            {
                "file_path": "pkg/project_manager.py",
                "name": "project_dir",
                "qualified_name": "pkg.project_manager.ProjectManager.project_dir",
                "semantic": {"calls": []},
            },
        ]

        refinement = _refine_module_domains_from_entities(file_domains, decisions, full_entities)

        assert file_domains["pkg/project_manager.py"] == "fs"
        assert refinement["summary"]["upgraded_misc"] == 1
        assert refinement["log"][0]["action"] == "upgrade"

    def test_retags_weak_module_when_entity_votes_are_clear(self):
        file_domains = {"pkg/blog.py": "http"}
        decisions = {
            "pkg/blog.py": DomainDecision("http", "category_hint", "weak")
        }
        full_entities = [
            {
                "file_path": "pkg/blog.py",
                "name": "save_file",
                "qualified_name": "pkg.blog.Blog.save_file",
                "semantic": {"calls": ["write_file"]},
            },
            {
                "file_path": "pkg/blog.py",
                "name": "get_project_files",
                "qualified_name": "pkg.blog.Blog.get_project_files",
                "semantic": {"calls": ["read_path"]},
            },
            {
                "file_path": "pkg/blog.py",
                "name": "project_dir",
                "qualified_name": "pkg.blog.Blog.project_dir",
                "semantic": {"calls": []},
            },
        ]

        refinement = _refine_module_domains_from_entities(file_domains, decisions, full_entities)

        assert file_domains["pkg/blog.py"] == "fs"
        assert refinement["summary"]["retagged_weak"] == 1
        assert refinement["log"][0]["action"] == "retag"

    def test_logs_disagreement_for_strong_module_without_overriding(self):
        file_domains = {"pkg/http_helpers.py": "http"}
        decisions = {
            "pkg/http_helpers.py": DomainDecision("http", "strong_import", "strong")
        }
        full_entities = [
            {
                "file_path": "pkg/http_helpers.py",
                "name": "save_file",
                "qualified_name": "pkg.http_helpers.save_file",
                "semantic": {"calls": ["write_file"]},
            },
            {
                "file_path": "pkg/http_helpers.py",
                "name": "get_project_files",
                "qualified_name": "pkg.http_helpers.get_project_files",
                "semantic": {"calls": ["read_path"]},
            },
            {
                "file_path": "pkg/http_helpers.py",
                "name": "project_dir",
                "qualified_name": "pkg.http_helpers.project_dir",
                "semantic": {"calls": []},
            },
        ]

        refinement = _refine_module_domains_from_entities(file_domains, decisions, full_entities)

        assert file_domains["pkg/http_helpers.py"] == "http"
        assert refinement["summary"]["strong_disagreements"] == 1
        assert refinement["log"][0]["action"] == "log_disagreement"

    def test_fs_rollup_requires_extra_consensus(self):
        file_domains = {"pkg/renderers.py": "misc"}
        decisions = {
            "pkg/renderers.py": DomainDecision("misc", "no_signal", "unresolved")
        }
        full_entities = [
            {
                "file_path": "pkg/renderers.py",
                "name": "save_file",
                "qualified_name": "pkg.renderers.Renderer.save_file",
                "semantic": {"calls": ["write_file"]},
            },
            {
                "file_path": "pkg/renderers.py",
                "name": "get_binary_file",
                "qualified_name": "pkg.renderers.Renderer.get_binary_file",
                "semantic": {"calls": []},
            },
        ]

        refinement = _refine_module_domains_from_entities(file_domains, decisions, full_entities)

        assert file_domains["pkg/renderers.py"] == "misc"
        assert refinement["summary"]["upgraded_misc"] == 0
        assert refinement["summary"]["log_entries"] == 0

    def test_fs_rollup_still_upgrades_when_consensus_is_strong(self):
        file_domains = {"pkg/project_files.py": "misc"}
        decisions = {
            "pkg/project_files.py": DomainDecision("misc", "no_signal", "unresolved")
        }
        full_entities = [
            {
                "file_path": "pkg/project_files.py",
                "name": "save_file_to_project",
                "qualified_name": "pkg.project_files.Manager.save_file_to_project",
                "semantic": {"calls": ["write_file"]},
            },
            {
                "file_path": "pkg/project_files.py",
                "name": "get_project_files",
                "qualified_name": "pkg.project_files.Manager.get_project_files",
                "semantic": {"calls": ["read_path"]},
            },
            {
                "file_path": "pkg/project_files.py",
                "name": "project_dir",
                "qualified_name": "pkg.project_files.Manager.project_dir",
                "semantic": {"calls": []},
            },
        ]

        refinement = _refine_module_domains_from_entities(file_domains, decisions, full_entities)

        assert file_domains["pkg/project_files.py"] == "fs"
        assert refinement["summary"]["upgraded_misc"] == 1
