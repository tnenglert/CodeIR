"""Tests for the trace CLI command."""

import sqlite3
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli


def _create_trace_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, qualified_name TEXT, file_path TEXT, start_line INTEGER, kind TEXT)"
    )
    conn.execute(
        "CREATE TABLE callers (entity_id TEXT, caller_id TEXT, resolution TEXT)"
    )
    conn.execute(
        "INSERT INTO entities (id, qualified_name, file_path, start_line, kind) VALUES (?, ?, ?, ?, ?)",
        ("AAA", "pkg.alpha", "alpha.py", 10, "function"),
    )
    conn.commit()
    conn.close()


def test_cmd_trace_returns_zero_hop_for_same_entity(tmp_path, capsys):
    repo_path = tmp_path
    codeir_dir = repo_path / ".codeir"
    codeir_dir.mkdir()
    db_path = codeir_dir / "entities.db"
    _create_trace_db(db_path)

    args = Namespace(
        from_entity="AAA",
        to_entity="AAA",
        repo_path=repo_path,
        depth=10,
        resolution="any",
    )

    cli.cmd_trace(args)
    out = capsys.readouterr().out

    assert "Call path found (0 hops):" in out
    assert "AAA              pkg.alpha" in out
    assert "alpha.py:10" in out
    assert "No call path found" not in out
