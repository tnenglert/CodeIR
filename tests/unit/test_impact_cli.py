"""Tests for the impact CLI command."""

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli


class _DummyConn:
    def __init__(self):
        self.row_factory = None

    def close(self):
        return None


def test_cmd_impact_prints_summary_and_excludes_areas(monkeypatch, capsys, tmp_path):
    codeir_dir = tmp_path / ".codeir"
    codeir_dir.mkdir()
    (codeir_dir / "entities.db").write_text("")

    monkeypatch.setattr(cli, "connect", lambda db_path: _DummyConn())
    monkeypatch.setattr(
        cli,
        "get_entity_annotations",
        lambda conn, entity_ids: {
            "LIB1": {"caller_count": 3, "pattern_base": None, "kind": "function", "line_count": 10},
            "TEST1": {"caller_count": 0, "pattern_base": None, "kind": "function", "line_count": 8},
        },
    )
    monkeypatch.setattr(
        cli,
        "compute_impact",
        lambda conn, entity_id, depth, level: {
            "root": {
                "entity_id": "ROOT",
                "qualified_name": "pkg.root",
                "file_path": "lib/root.py",
                "start_line": 10,
                "kind": "function",
                "ir_text": "FN ROOT C=x F=R A=1 #CORE",
            },
            "impact_by_depth": {
                1: [
                    {
                        "entity_id": "LIB1",
                        "qualified_name": "pkg.lib1",
                        "file_path": "lib/a.py",
                        "start_line": 20,
                        "kind": "function",
                        "resolution": "import",
                        "ir_text": "FN LIB1",
                        "via": "ROOT",
                        "category": "core_logic",
                    },
                    {
                        "entity_id": "TEST1",
                        "qualified_name": "pkg.test1",
                        "file_path": "test/test_a.py",
                        "start_line": 30,
                        "kind": "function",
                        "resolution": "local",
                        "ir_text": "FN TEST1",
                        "via": "ROOT",
                        "category": "tests",
                    },
                ]
            },
            "affected_files": {"lib/a.py", "test/test_a.py"},
            "affected_categories": {"core_logic", "tests"},
        },
    )

    args = Namespace(
        entity_id="ROOT",
        repo_path=tmp_path,
        depth=2,
        level="Behavior",
        show_all=False,
        exclude_area=["test"],
    )

    cli.cmd_impact(args)
    out = capsys.readouterr().out

    assert "Affected: 1 entities across 1 files" in out
    assert "By depth: d1=1" in out
    assert "By area: lib=1" in out
    assert "By category: core_logic=1" in out
    assert "Top files: lib/a.py=1" in out
    assert "Excluded areas: test" in out
    assert "LIB1" in out
    assert "TEST1" not in out
