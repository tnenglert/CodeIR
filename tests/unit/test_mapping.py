"""Tests for abbreviation map persistence (save/load round trip)."""

import sqlite3

import pytest

from index.mapping import load_abbreviation_maps, save_abbreviation_maps


def _make_mapping_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE abbreviations ("
        "map_type TEXT NOT NULL, original TEXT NOT NULL, token TEXT NOT NULL, "
        "version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(map_type, original))"
    )
    return conn


class TestSaveLoad:
    def test_round_trip(self):
        conn = _make_mapping_db()
        maps = {
            "entity_name": {"mod.foo": "NFOO", "mod.bar": "NBAR"},
            "file_path": {"src/main.py": "F001"},
            "call_name": {"process": "CPRCS"},
        }
        count = save_abbreviation_maps(conn, maps)
        assert count == 4

        loaded = load_abbreviation_maps(conn)
        assert loaded == maps
        conn.close()

    def test_upsert_updates_token(self):
        conn = _make_mapping_db()
        maps1 = {"entity_name": {"mod.foo": "OLD"}}
        save_abbreviation_maps(conn, maps1)

        maps2 = {"entity_name": {"mod.foo": "NEW"}}
        save_abbreviation_maps(conn, maps2)

        loaded = load_abbreviation_maps(conn)
        assert loaded["entity_name"]["mod.foo"] == "NEW"
        conn.close()

    def test_empty_maps(self):
        conn = _make_mapping_db()
        maps = {"entity_name": {}, "file_path": {}, "call_name": {}}
        count = save_abbreviation_maps(conn, maps)
        assert count == 0

        loaded = load_abbreviation_maps(conn)
        assert loaded == {}  # no rows
        conn.close()

    def test_load_empty_db(self):
        conn = _make_mapping_db()
        loaded = load_abbreviation_maps(conn)
        assert loaded == {}
        conn.close()

    def test_preserves_existing_while_adding(self):
        conn = _make_mapping_db()
        save_abbreviation_maps(conn, {"entity_name": {"mod.foo": "FOO"}})
        save_abbreviation_maps(conn, {"entity_name": {"mod.bar": "BAR"}})

        loaded = load_abbreviation_maps(conn)
        assert loaded["entity_name"]["mod.foo"] == "FOO"
        assert loaded["entity_name"]["mod.bar"] == "BAR"
        conn.close()
