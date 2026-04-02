"""Tests for bearings path helpers and refresh behavior."""

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli import _get_bearings_paths, _resolve_bearings_paths, cmd_index


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


def test_cmd_index_regenerates_bearings_after_changes(monkeypatch, tmp_path):
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
    monkeypatch.setattr("cli._generate_bearings_files", lambda repo_path: called.__setitem__("generate", called["generate"] + 1))
    monkeypatch.setattr("cli._ensure_agent_rules", lambda repo_path: called.__setitem__("agents", called["agents"] + 1))

    args = Namespace(repo_path=tmp_path, level=None, mode=None, compact=False)
    cmd_index(args)

    assert called["generate"] == 1
    assert called["agents"] == 1
