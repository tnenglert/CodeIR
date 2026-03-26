"""Tests for the show CLI command."""

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli


def test_cmd_show_supports_multiple_entity_ids(monkeypatch, capsys, tmp_path):
    rows = {
        "AAA": {
            "qualified_name": "pkg.alpha",
            "kind": "function",
            "file_path": "alpha.py",
            "start_line": 10,
            "end_line": 18,
            "line": 10,
            "ir_text": "FN AAA C=foo F=IR A=2 #CORE",
        },
        "BBB": {
            "qualified_name": "pkg.beta",
            "kind": "class",
            "file_path": "beta.py",
            "start_line": 20,
            "end_line": 20,
            "line": 20,
            "ir_text": "CLS BBB C=Bar F=E A=1 #CORE",
        },
    }

    monkeypatch.setattr(
        cli,
        "get_entity_with_ir",
        lambda repo_path, entity_id, mode: rows.get(entity_id),
    )

    args = Namespace(
        entity_ids=["AAA", "BBB"],
        repo_path=tmp_path,
        level="Behavior",
        full=True,
    )

    cli.cmd_show(args)
    out = capsys.readouterr().out

    assert "pkg.alpha [function]  alpha.py:10-18" in out
    assert "FN AAA C=foo F=IR A=2 #CORE" in out
    assert "pkg.beta [class]  beta.py:20" in out
    assert "CLS BBB C=Bar F=E A=1 #CORE" in out


def test_cmd_show_reports_missing_entities_after_found_results(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        cli,
        "get_entity_with_ir",
        lambda repo_path, entity_id, mode: {
            "qualified_name": "pkg.alpha",
            "kind": "function",
            "file_path": "alpha.py",
            "start_line": 10,
            "end_line": 18,
            "line": 10,
            "ir_text": "FN AAA C=foo F=IR A=2 #CORE",
        } if entity_id == "AAA" else None,
    )

    args = Namespace(
        entity_ids=["AAA", "MISSING"],
        repo_path=tmp_path,
        level="Behavior",
        full=True,
    )

    cli.cmd_show(args)
    out = capsys.readouterr().out

    assert "pkg.alpha [function]  alpha.py:10-18" in out
    assert "Entity not found: MISSING (level=Behavior)" in out
    assert "Run `codeir index <repo_path>` first." not in out
