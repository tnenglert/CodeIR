"""Tests for caller resolution logic."""

import pytest

from index.callers import (
    FUZZY_MATCH_LIMIT,
    _matching_entities,
    resolve_calls_for_entity,
)


def _entity(eid, name, qname, file_path="a.py", language="python"):
    return {
        "entity_id": eid,
        "name": name,
        "qualified_name": qname,
        "file_path": file_path,
        "language": language,
    }


# ---------------------------------------------------------------------------
# _matching_entities
# ---------------------------------------------------------------------------

class TestMatchingEntities:
    def test_finds_by_name(self):
        name_map = {"foo": [_entity("E1", "foo", "mod.foo")]}
        result = _matching_entities(
            "foo", entity_id="CALLER", language="python", name_to_entities=name_map,
        )
        assert len(result) == 1
        assert result[0]["entity_id"] == "E1"

    def test_excludes_self(self):
        name_map = {"foo": [_entity("CALLER", "foo", "mod.foo")]}
        result = _matching_entities(
            "foo", entity_id="CALLER", language="python", name_to_entities=name_map,
        )
        assert result == []

    def test_filters_by_language(self):
        name_map = {"foo": [_entity("E1", "foo", "mod.foo", language="rust")]}
        result = _matching_entities(
            "foo", entity_id="CALLER", language="python", name_to_entities=name_map,
        )
        assert result == []

    def test_filters_by_file_path(self):
        name_map = {"foo": [
            _entity("E1", "foo", "a.foo", file_path="a.py"),
            _entity("E2", "foo", "b.foo", file_path="b.py"),
        ]}
        result = _matching_entities(
            "foo", entity_id="CALLER", language="python",
            name_to_entities=name_map, file_path="a.py",
        )
        assert len(result) == 1
        assert result[0]["entity_id"] == "E1"

    def test_no_file_filter_returns_all(self):
        name_map = {"foo": [
            _entity("E1", "foo", "a.foo", file_path="a.py"),
            _entity("E2", "foo", "b.foo", file_path="b.py"),
        ]}
        result = _matching_entities(
            "foo", entity_id="CALLER", language="python", name_to_entities=name_map,
        )
        assert len(result) == 2

    def test_unknown_call_name(self):
        result = _matching_entities(
            "nonexistent", entity_id="CALLER", language="python",
            name_to_entities={},
        )
        assert result == []


# ---------------------------------------------------------------------------
# resolve_calls_for_entity — stoplist
# ---------------------------------------------------------------------------

class TestResolveCallsStoplist:
    def test_bare_call_on_stoplist_skipped(self):
        caller = _entity("CALLER", "func", "mod.func")
        name_map = {"get": [_entity("E1", "get", "mod.get")]}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["get"], file_path="a.py",
            import_map={}, name_to_entities=name_map,
            qualified_to_entity={}, stoplist={"get"},
        )
        assert rels == []
        assert amb == []

    def test_qualified_call_bypasses_stoplist(self):
        """password_helper.hash should resolve even though 'hash' is on stoplist."""
        caller = _entity("CALLER", "func", "mod.func")
        name_map = {"hash": [_entity("E1", "hash", "helpers.hash")]}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["password_helper.hash"], file_path="a.py",
            import_map={}, name_to_entities=name_map,
            qualified_to_entity={}, stoplist={"hash"},
        )
        assert len(rels) == 1
        assert rels[0]["entity_id"] == "E1"


# ---------------------------------------------------------------------------
# resolve_calls_for_entity — import resolution
# ---------------------------------------------------------------------------

class TestResolveCallsImport:
    def test_import_map_exact_match(self):
        caller = _entity("CALLER", "func", "mod.func")
        target = _entity("TARGET", "User", "models.User")
        name_map = {"User": [target]}
        qualified_map = {("python", "models.User"): target}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["User"], file_path="a.py",
            import_map={"User": "models.User"},
            name_to_entities=name_map,
            qualified_to_entity=qualified_map,
            stoplist=set(),
        )
        assert len(rels) == 1
        assert rels[0]["entity_id"] == "TARGET"
        assert rels[0]["resolution"] == "import"

    def test_import_map_fallback_to_bare_name(self):
        """If qualified name not in qualified_to_entity, fall back to bare name match."""
        caller = _entity("CALLER", "func", "mod.func")
        target = _entity("TARGET", "User", "different.User")
        name_map = {"User": [target]}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["User"], file_path="a.py",
            import_map={"User": "models.User"},  # models.User not in qualified map
            name_to_entities=name_map,
            qualified_to_entity={},
            stoplist=set(),
        )
        assert len(rels) == 1
        assert rels[0]["entity_id"] == "TARGET"
        assert rels[0]["resolution"] == "import"

    def test_import_map_bare_fallback_needs_single_match(self):
        """When import bare fallback has >1 candidate, don't resolve via import."""
        caller = _entity("CALLER", "func", "mod.func")
        name_map = {"User": [
            _entity("T1", "User", "a.User"),
            _entity("T2", "User", "b.User"),
        ]}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["User"], file_path="a.py",
            import_map={"User": "models.User"},
            name_to_entities=name_map,
            qualified_to_entity={},
            stoplist=set(),
        )
        # Should fall through import to local/fuzzy, not resolve via import
        assert all(r["resolution"] != "import" for r in rels)


# ---------------------------------------------------------------------------
# resolve_calls_for_entity — local resolution
# ---------------------------------------------------------------------------

class TestResolveCallsLocal:
    def test_same_file_resolution(self):
        caller = _entity("CALLER", "func", "mod.func", file_path="a.py")
        local_target = _entity("LOCAL", "helper", "mod.helper", file_path="a.py")
        remote_target = _entity("REMOTE", "helper", "other.helper", file_path="b.py")
        name_map = {"helper": [local_target, remote_target]}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["helper"], file_path="a.py",
            import_map={}, name_to_entities=name_map,
            qualified_to_entity={}, stoplist=set(),
        )
        assert len(rels) == 1
        assert rels[0]["entity_id"] == "LOCAL"
        assert rels[0]["resolution"] == "local"


# ---------------------------------------------------------------------------
# resolve_calls_for_entity — fuzzy resolution
# ---------------------------------------------------------------------------

class TestResolveCallsFuzzy:
    def test_fuzzy_within_limit(self):
        caller = _entity("CALLER", "func", "mod.func", file_path="a.py")
        targets = [
            _entity(f"T{i}", "helper", f"pkg{i}.helper", file_path=f"f{i}.py")
            for i in range(FUZZY_MATCH_LIMIT)
        ]
        name_map = {"helper": targets}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["helper"], file_path="a.py",
            import_map={}, name_to_entities=name_map,
            qualified_to_entity={}, stoplist=set(),
        )
        assert len(rels) == FUZZY_MATCH_LIMIT
        assert all(r["resolution"] == "fuzzy" for r in rels)
        assert amb == []

    def test_fuzzy_over_limit_is_ambiguous(self):
        caller = _entity("CALLER", "func", "mod.func", file_path="a.py")
        targets = [
            _entity(f"T{i}", "helper", f"pkg{i}.helper", file_path=f"f{i}.py")
            for i in range(FUZZY_MATCH_LIMIT + 1)
        ]
        name_map = {"helper": targets}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["helper"], file_path="a.py",
            import_map={}, name_to_entities=name_map,
            qualified_to_entity={}, stoplist=set(),
        )
        assert rels == []
        assert len(amb) == 1
        assert amb[0]["call_name"] == "helper"
        assert amb[0]["candidate_count"] == FUZZY_MATCH_LIMIT + 1

    def test_exactly_one_fuzzy_candidate(self):
        caller = _entity("CALLER", "func", "mod.func", file_path="a.py")
        target = _entity("TARGET", "helper", "pkg.helper", file_path="b.py")
        name_map = {"helper": [target]}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["helper"], file_path="a.py",
            import_map={}, name_to_entities=name_map,
            qualified_to_entity={}, stoplist=set(),
        )
        assert len(rels) == 1
        assert rels[0]["resolution"] == "fuzzy"


# ---------------------------------------------------------------------------
# resolve_calls_for_entity — qualified calls
# ---------------------------------------------------------------------------

class TestResolveCallsQualified:
    def test_qualified_local_resolution(self):
        """obj.method resolves to same-file entity named 'method'."""
        caller = _entity("CALLER", "func", "mod.func", file_path="a.py")
        target = _entity("TARGET", "process", "mod.process", file_path="a.py")
        name_map = {"process": [target]}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["obj.process"], file_path="a.py",
            import_map={}, name_to_entities=name_map,
            qualified_to_entity={}, stoplist=set(),
        )
        assert len(rels) == 1
        assert rels[0]["resolution"] == "local"

    def test_qualified_fuzzy_fallback(self):
        """If no local match for qualified call, fall back to fuzzy."""
        caller = _entity("CALLER", "func", "mod.func", file_path="a.py")
        target = _entity("TARGET", "process", "other.process", file_path="b.py")
        name_map = {"process": [target]}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["obj.process"], file_path="a.py",
            import_map={}, name_to_entities=name_map,
            qualified_to_entity={}, stoplist=set(),
        )
        assert len(rels) == 1
        assert rels[0]["resolution"] == "fuzzy"


# ---------------------------------------------------------------------------
# resolve_calls_for_entity — deduplication
# ---------------------------------------------------------------------------

class TestResolveCallsDedup:
    def test_same_target_not_duplicated(self):
        """If two calls resolve to the same target, it appears once."""
        caller = _entity("CALLER", "func", "mod.func", file_path="a.py")
        target = _entity("TARGET", "helper", "mod.helper", file_path="a.py")
        name_map = {"helper": [target]}
        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["helper", "helper"], file_path="a.py",
            import_map={}, name_to_entities=name_map,
            qualified_to_entity={}, stoplist=set(),
        )
        assert len(rels) == 1


# ---------------------------------------------------------------------------
# resolve_calls_for_entity — resolution priority
# ---------------------------------------------------------------------------

class TestResolveCallsPriority:
    def test_import_beats_local(self):
        """Import resolution takes priority — local is only tried if import fails."""
        caller = _entity("CALLER", "func", "mod.func", file_path="a.py")
        import_target = _entity("IMPORT_T", "User", "models.User", file_path="models.py")
        local_target = _entity("LOCAL_T", "User", "local.User", file_path="a.py")
        name_map = {"User": [import_target, local_target]}
        qualified_map = {("python", "models.User"): import_target}

        rels, amb = resolve_calls_for_entity(
            entity=caller, calls=["User"], file_path="a.py",
            import_map={"User": "models.User"},
            name_to_entities=name_map,
            qualified_to_entity=qualified_map,
            stoplist=set(),
        )
        assert len(rels) == 1
        assert rels[0]["resolution"] == "import"
        assert rels[0]["entity_id"] == "IMPORT_T"
