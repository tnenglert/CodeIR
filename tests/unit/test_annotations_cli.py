"""Tests for annotated entity CLI presentation."""

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli


def test_format_annotated_entity_shows_only_fuzzy_resolution():
    line = cli.format_annotated_entity(
        entity_id="FOO",
        file_path="pkg/foo.py",
        annotations={"FOO": {"caller_count": 3, "pattern_base": None, "kind": "function", "line_count": 12}},
        resolution="import",
    )

    assert "Callers=3" in line
    assert "Res=" not in line

    fuzzy_line = cli.format_annotated_entity(
        entity_id="FOO",
        file_path="pkg/foo.py",
        annotations={"FOO": {"caller_count": 3, "pattern_base": None, "kind": "function", "line_count": 12}},
        resolution="fuzzy",
    )
    assert "Res=fuzzy" in fuzzy_line


def test_cmd_callers_prints_fuzzy_resolution_only(monkeypatch, capsys, tmp_path):
    codeir_dir = tmp_path / ".codeir"
    codeir_dir.mkdir()
    (codeir_dir / "entities.db").write_text("")

    class _DummyConn:
        def __init__(self):
            self.row_factory = None

        def execute(self, sql, params=None):
            params = params or []
            normalized = " ".join(sql.split())
            if normalized.startswith("SELECT name, qualified_name FROM entities WHERE id = ?"):
                return _Rows([{"name": "target", "qualified_name": "pkg.target"}])
            if "SELECT caller_id, caller_name, caller_file, resolution FROM callers" in normalized:
                return _Rows([{"caller_id": "CALLER1", "caller_name": "pkg.caller", "caller_file": "lib/caller.py", "resolution": "fuzzy"}])
            if normalized.startswith("SELECT id, qualified_name, file_path, calls_json FROM entities"):
                return _Rows([])
            if normalized.startswith("SELECT COUNT(*) FROM entities WHERE name = ?"):
                return _Rows([(1,)])
            raise AssertionError(f"Unexpected SQL: {sql}")

        def close(self):
            return None

    class _Rows(list):
        def fetchone(self):
            return self[0] if self else None

        def fetchall(self):
            return list(self)

    monkeypatch.setattr(cli, "connect", lambda db_path: _DummyConn())
    monkeypatch.setattr(
        cli,
        "get_entity_annotations",
        lambda conn, entity_ids: {
            "CALLER1": {"caller_count": 5, "pattern_base": None, "kind": "function", "line_count": 9},
        },
    )

    args = Namespace(entity_id="TARGET", repo_path=tmp_path, resolution=None, show_all=False)
    cli.cmd_callers(args)
    out = capsys.readouterr().out

    assert "Res=fuzzy" in out
    assert "~CALLER1" not in out


def test_cmd_impact_omits_non_fuzzy_resolution(monkeypatch, capsys, tmp_path):
    codeir_dir = tmp_path / ".codeir"
    codeir_dir.mkdir()
    (codeir_dir / "entities.db").write_text("")

    class _DummyConn:
        def __init__(self):
            self.row_factory = None

        def close(self):
            return None

    monkeypatch.setattr(cli, "connect", lambda db_path: _DummyConn())
    monkeypatch.setattr(
        cli,
        "get_entity_annotations",
        lambda conn, entity_ids: {
            "LIB1": {"caller_count": 3, "pattern_base": None, "kind": "function", "line_count": 10},
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
                ]
            },
            "affected_files": {"lib/a.py"},
            "affected_categories": {"core_logic"},
        },
    )

    args = Namespace(
        entity_id="ROOT",
        repo_path=tmp_path,
        depth=2,
        level="Behavior",
        show_all=False,
        exclude_area=[],
    )

    cli.cmd_impact(args)
    out = capsys.readouterr().out

    assert "Res=" not in out


def test_cmd_callers_warns_when_callers_graph_is_stale(monkeypatch, capsys, tmp_path):
    codeir_dir = tmp_path / ".codeir"
    codeir_dir.mkdir()
    (codeir_dir / "entities.db").write_text("")

    class _DummyConn:
        def __init__(self):
            self.row_factory = None

        def execute(self, sql, params=None):
            params = params or []
            normalized = " ".join(sql.split())
            if normalized.startswith("SELECT value FROM index_meta WHERE key = ?"):
                key = params[0]
                meta = {
                    "callers_status": "stale",
                    "callers_built_at": "2026-04-07T12:00:00+00:00",
                    "callers_error": "boom",
                }
                value = meta.get(key)
                return _Rows([(value,)]) if value is not None else _Rows([])
            if normalized.startswith("SELECT name, qualified_name FROM entities WHERE id = ?"):
                return _Rows([{"name": "target", "qualified_name": "pkg.target"}])
            if "SELECT caller_id, caller_name, caller_file, resolution FROM callers" in normalized:
                return _Rows([{"caller_id": "CALLER1", "caller_name": "pkg.caller", "caller_file": "lib/caller.py", "resolution": "fuzzy"}])
            if normalized.startswith("SELECT id, qualified_name, file_path, calls_json FROM entities"):
                return _Rows([])
            if normalized.startswith("SELECT COUNT(*) FROM entities WHERE name = ?"):
                return _Rows([(1,)])
            raise AssertionError(f"Unexpected SQL: {sql}")

        def close(self):
            return None

    class _Rows(list):
        def fetchone(self):
            return self[0] if self else None

        def fetchall(self):
            return list(self)

    monkeypatch.setattr(cli, "connect", lambda db_path: _DummyConn())
    monkeypatch.setattr(
        cli,
        "get_entity_annotations",
        lambda conn, entity_ids: {
            "CALLER1": {"caller_count": 5, "pattern_base": None, "kind": "function", "line_count": 9},
        },
    )

    args = Namespace(entity_id="TARGET", repo_path=tmp_path, resolution=None, show_all=False)
    cli.cmd_callers(args)
    out = capsys.readouterr().out

    assert "Warning: caller graph may be stale" in out
    assert "Last successful caller rebuild: 2026-04-07T12:00:00+00:00" in out
    assert "Last caller rebuild error: boom" in out
