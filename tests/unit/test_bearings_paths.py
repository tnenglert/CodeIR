"""Tests for bearings path helpers."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli import _get_bearings_paths, _resolve_bearings_paths


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
