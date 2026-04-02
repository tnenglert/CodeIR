"""Tests for abbreviation map generation."""

import pytest

from ir.abbreviations import (
    CORE_MAP,
    _next_index,
    _shorter_token,
    _token,
    build_abbreviation_maps,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestToken:
    def test_zero_padded(self):
        assert _token("N", 1) == "N001"
        assert _token("N", 42) == "N042"
        assert _token("C", 100) == "C100"


class TestShorterToken:
    def test_shorter_wins(self):
        # "N001" is shorter than a very long name
        assert _shorter_token("extremely_long_entity_name", "N001") == "N001"

    def test_original_when_not_shorter(self):
        # Short name stays
        assert _shorter_token("x", "N001") == "x"


class TestNextIndex:
    def test_empty_map(self):
        assert _next_index({}, "N") == 1

    def test_existing_tokens(self):
        existing = {"a": "N001", "b": "N003"}
        assert _next_index(existing, "N") == 4

    def test_mixed_prefixes(self):
        existing = {"a": "N001", "b": "C002"}
        assert _next_index(existing, "N") == 2
        assert _next_index(existing, "C") == 3


# ---------------------------------------------------------------------------
# build_abbreviation_maps
# ---------------------------------------------------------------------------

class TestBuildAbbreviationMaps:
    def test_empty_input(self):
        result = build_abbreviation_maps([], [])
        assert result == {"entity_name": {}, "file_path": {}, "call_name": {}}

    def test_core_map_used(self):
        """Names matching CORE_MAP keys get the core abbreviation."""
        result = build_abbreviation_maps(["mod.user"], [])
        assert result["entity_name"]["mod.user"] == "USR"

    def test_core_map_collision_falls_through(self):
        """If two names map to the same CORE_MAP value, second gets a different token."""
        result = build_abbreviation_maps(["mod.user", "other.user"], [])
        tokens = list(result["entity_name"].values())
        assert len(set(tokens)) == 2  # no duplicates
        assert "USR" in tokens  # first one gets USR

    def test_file_paths_get_tokens(self):
        result = build_abbreviation_maps([], ["src/main.py", "src/utils.py"])
        assert len(result["file_path"]) == 2

    def test_call_symbols(self):
        result = build_abbreviation_maps([], [], call_symbols=["foo", "bar"])
        assert len(result["call_name"]) == 2

    def test_call_core_map(self):
        """Call symbols also use CORE_MAP."""
        result = build_abbreviation_maps([], [], call_symbols=["user"])
        assert result["call_name"]["user"] == "USR"

    def test_deterministic(self):
        """Same inputs → same outputs."""
        names = ["mod.alpha", "mod.beta", "mod.gamma"]
        files = ["a.py", "b.py"]
        calls = ["foo", "bar"]
        r1 = build_abbreviation_maps(names, files, call_symbols=calls)
        r2 = build_abbreviation_maps(names, files, call_symbols=calls)
        assert r1 == r2

    def test_existing_maps_preserved(self):
        """Pre-existing abbreviations should be kept, new names added."""
        existing = {
            "entity_name": {"mod.foo": "NFOO"},
            "file_path": {},
            "call_name": {},
        }
        result = build_abbreviation_maps(
            ["mod.foo", "mod.bar"], [],
            existing_maps=existing,
        )
        assert result["entity_name"]["mod.foo"] == "NFOO"  # preserved
        assert "mod.bar" in result["entity_name"]  # new

    def test_compact_mode_ignores_existing(self):
        existing = {
            "entity_name": {"mod.foo": "NFOO"},
            "file_path": {},
            "call_name": {},
        }
        result = build_abbreviation_maps(
            ["mod.foo"], [],
            existing_maps=existing,
            compact_mode=True,
        )
        # In compact mode, existing maps are cleared — token may differ
        assert "mod.foo" in result["entity_name"]

    def test_no_token_collisions(self):
        """All generated tokens must be unique within each map."""
        names = [f"mod.entity_{i}" for i in range(50)]
        result = build_abbreviation_maps(names, [])
        tokens = list(result["entity_name"].values())
        assert len(tokens) == len(set(tokens))

    def test_deduplicates_input(self):
        result = build_abbreviation_maps(["mod.foo", "mod.foo"], [])
        assert len(result["entity_name"]) == 1
