"""Tests for init platform detection and selection."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ir.init import Codex, detect_runtime_platforms, select_platforms


def _names(platforms):
    return [platform.name for platform in platforms]


def test_detect_runtime_platforms_codex_env():
    env = {"CODEX_THREAD_ID": "thread-123"}

    assert _names(detect_runtime_platforms(env)) == ["codex"]


def test_detect_runtime_platforms_honors_override():
    env = {"CODEIR_CURRENT_PLATFORM": "openclaw"}

    assert _names(detect_runtime_platforms(env)) == ["openclaw"]


def test_select_platforms_prefers_repo_markers_over_runtime(tmp_path):
    (tmp_path / ".claude").mkdir()
    env = {"CODEX_THREAD_ID": "thread-123"}

    selection = select_platforms(tmp_path, env=env)

    assert selection.mode == "repo"
    assert _names(selection.repo_detected) == ["claude"]
    assert _names(selection.runtime_detected) == ["codex"]
    assert _names(selection.selected) == ["claude"]


def test_select_platforms_falls_back_to_runtime(tmp_path):
    env = {"CODEX_THREAD_ID": "thread-123"}

    selection = select_platforms(tmp_path, env=env)

    assert selection.mode == "runtime_fallback"
    assert _names(selection.repo_detected) == []
    assert _names(selection.runtime_detected) == ["codex"]
    assert _names(selection.selected) == ["codex"]


def test_select_platforms_current_uses_runtime_only(tmp_path):
    (tmp_path / ".claude").mkdir()
    env = {"CODEX_THREAD_ID": "thread-123"}

    selection = select_platforms(tmp_path, requested_platform="current", env=env)

    assert selection.mode == "current"
    assert _names(selection.selected) == ["codex"]


def test_codex_render_includes_bearings_first_guidance():
    content = Codex().render()

    assert "orient by running\n`codeir bearings` before search, grep, or expand." in content
    assert "### Three workflows" in content
    assert "**Show mode**" in content
    assert "**Expand mode**" in content
    assert "**Grep mode**" in content
    assert "You must minimize total tool calls." in content
    assert "compact behavior snapshots for one or more entities" in content
    assert "codeir show <entity_id> [<entity_id> ...] [--level Index|Behavior]" in content
    assert "codeir grep <pattern> --evidence" in content
    assert "instead of `rg -n ...` followed by `sed -n ...`" in content
    assert "### Selection rules" in content
    assert "Do not `expand` weak matches just to be sure." in content
