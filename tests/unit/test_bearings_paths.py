"""Tests for bearings path helpers and refresh behavior."""

import sqlite3
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli import _get_bearings_paths, _load_modules_for_bearings, _resolve_bearings_paths, cmd_index, cmd_init


def test_get_bearings_paths_defaults_to_codeir(tmp_path):
    paths = _get_bearings_paths(tmp_path)

    assert paths["base"] == tmp_path / ".codeir"
    assert paths["summary"] == tmp_path / ".codeir" / "bearings-summary.md"
    assert paths["map"] == tmp_path / ".codeir" / "bearings.md"
    assert paths["categories"] == tmp_path / ".codeir" / "bearings"


def test_resolve_bearings_paths_prefers_new_location(tmp_path):
    current = _get_bearings_paths(tmp_path)
    legacy = _get_bearings_paths(tmp_path, legacy=True)
    current["base"].mkdir(parents=True)
    legacy["base"].mkdir(parents=True)
    current["summary"].write_text("new", encoding="utf-8")
    legacy["summary"].write_text("old", encoding="utf-8")

    paths, using_legacy = _resolve_bearings_paths(tmp_path)

    assert paths["summary"] == current["summary"]
    assert using_legacy is False


def test_resolve_bearings_paths_falls_back_to_legacy(tmp_path):
    legacy = _get_bearings_paths(tmp_path, legacy=True)
    legacy["base"].mkdir(parents=True)
    legacy["summary"].write_text("old", encoding="utf-8")

    paths, using_legacy = _resolve_bearings_paths(tmp_path)

    assert paths["summary"] == legacy["summary"]
    assert using_legacy is True


def test_cmd_index_regenerates_bearings_after_changes(monkeypatch, tmp_path, capsys):
    called = {"generate": 0, "agents": 0}

    monkeypatch.setattr("cli.load_config", lambda repo_path: {})
    monkeypatch.setattr(
        "cli.index_repo",
        lambda repo_path, cfg: {
            "files_changed": 1,
            "files_unchanged": 0,
            "files_scanned": 1,
            "entities_indexed": 1,
            "total_entities": 1,
            "ir_rows": 1,
            "total_ir_rows": 1,
            "abbreviations": 0,
            "caller_relationships": 0,
            "compression_level": "Behavior",
            "store_dir": str(tmp_path / ".codeir"),
        },
    )
    monkeypatch.setattr("index.pattern_detector.detect_patterns", lambda db_path: [])
    monkeypatch.setattr(
        "cli.get_stats",
        lambda repo_path: {
            "classification_quality": {
                "structural_percent": 75.0,
                "fallback_percent": 25.0,
                "specific_percent": 50.0,
                "misc_percent": 50.0,
                "unknown_percent": 0.0,
            }
        },
    )
    monkeypatch.setattr("cli._checkpoint_store", lambda repo_path: None)
    monkeypatch.setattr("cli._generate_bearings_files", lambda repo_path: called.__setitem__("generate", called["generate"] + 1))
    monkeypatch.setattr("cli._ensure_agent_rules", lambda repo_path: called.__setitem__("agents", called["agents"] + 1))

    args = Namespace(repo_path=tmp_path, level=None, mode=None, compact=False)
    cmd_index(args)
    out = capsys.readouterr().out

    assert called["generate"] == 1
    assert called["agents"] == 1
    assert "Classification: 75.0% structural, 25.0% fallback" in out
    assert "Domains: 50.0% specific, 50.0% misc, 0.0% unknown" in out


def test_cmd_init_skip_index_loads_domain_for_bearings(tmp_path):
    codeir_dir = tmp_path / ".codeir"
    codeir_dir.mkdir()
    conn = sqlite3.connect(codeir_dir / "entities.db")
    conn.execute(
        "CREATE TABLE modules ("
        "file_path TEXT PRIMARY KEY, category TEXT NOT NULL, domain TEXT NOT NULL, "
        "content_hash TEXT NOT NULL, entity_count INTEGER NOT NULL, "
        "deps_internal TEXT NOT NULL, indexed_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, file_path TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO modules VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("app/views.py", "router", "ui", "hash", 2, "models", "now"),
    )
    conn.execute(
        "INSERT INTO entities VALUES (?, ?)",
        ("VIEW", "app/views.py"),
    )
    conn.commit()
    conn.close()

    args = Namespace(
        repo_path=tmp_path,
        level=None,
        platform="claude",
        list_only=False,
        force=False,
        skip_index=True,
    )

    cmd_init(args)

    bearings = (codeir_dir / "bearings.md").read_text(encoding="utf-8")
    assert "views.py" in bearings
    assert "deps:models" in bearings


def test_load_modules_for_bearings_defaults_missing_optional_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE modules (file_path TEXT PRIMARY KEY, category TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO modules VALUES (?, ?)",
        ("src/app.py", "core_logic"),
    )

    modules = _load_modules_for_bearings(conn)

    assert modules == [
        {
            "file_path": "src/app.py",
            "category": "core_logic",
            "domain": "unknown",
            "entity_count": 0,
            "deps_internal": "",
        }
    ]
    conn.close()
