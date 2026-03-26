"""Tests for the expand CLI command."""

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli


def test_cmd_expand_numbered_output(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        cli,
        "get_entity_location",
        lambda repo_path, entity_id: {
            "entity_id": "AAA",
            "qualified_name": "pkg.alpha",
            "kind": "function",
            "file_path": "alpha.py",
            "start_line": 10,
            "end_line": 12,
        } if entity_id == "AAA" else None,
    )
    monkeypatch.setattr(
        cli,
        "extract_code_slice",
        lambda repo_path, file_path, start_line, end_line: "first()\nsecond()\nthird()\n",
    )

    args = Namespace(
        entity_ids=["AAA"],
        repo_path=tmp_path,
        number=True,
    )

    cli.cmd_expand(args)
    out = capsys.readouterr().out

    assert "Entity: pkg.alpha  [function]" in out
    assert "File:   alpha.py:10-12" in out
    assert "10: first()" in out
    assert "11: second()" in out
    assert "12: third()" in out
