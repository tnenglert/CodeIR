"""Mixed-language indexing tests for Python, Rust, and TypeScript repos."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_rust")
pytest.importorskip("tree_sitter_typescript")

from index.indexer import index_repo
from index.store.db import connect
from index.store.stats import get_stats


def _entity_id_by_qualified_name(conn, qualified_name: str) -> str:
    row = conn.execute(
        "SELECT id FROM entities WHERE qualified_name = ?",
        (qualified_name,),
    ).fetchone()
    assert row is not None, qualified_name
    return row[0]


def test_index_repo_supports_mixed_python_rust_and_typescript(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'mixed-demo'\nversion = '0.1.0'\n",
        encoding="utf-8",
    )
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "mixed-demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    py_pkg = tmp_path / "pkg"
    py_pkg.mkdir()
    (py_pkg / "__init__.py").write_text("", encoding="utf-8")
    (py_pkg / "util.py").write_text(
        """
def helper():
    return 1

def call_helper():
    return helper()
""".strip()
        + "\n",
        encoding="utf-8",
    )

    rust_src = tmp_path / "src"
    rust_src.mkdir()
    (rust_src / "util.rs").write_text(
        """
pub fn helper() -> i32 {
    1
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    ts_src = tmp_path / "websrc"
    ts_src.mkdir()
    (ts_src / "util.ts").write_text(
        """
export function helper(): number {
  return 1;
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (ts_src / "app.ts").write_text(
        """
import { helper } from "./util";

export function callHelper(): number {
  return helper();
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (rust_src / "lib.rs").write_text(
        """
mod util;

pub fn call_helper() -> i32 {
    util::helper()
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = index_repo(
        tmp_path,
        {
            "compression_level": "Behavior+Index",
            "hidden_dirs": [".git", ".codeir", "__pycache__", "target"],
        },
    )

    assert result["source_language"] == "mixed"
    assert result["source_languages"] == ["python", "rust", "typescript"]
    assert result["entities_indexed"] >= 6

    conn = connect(tmp_path / ".codeir" / "entities.db")
    qualified_names = {
        row[0]
        for row in conn.execute(
            "SELECT qualified_name FROM entities ORDER BY qualified_name"
        ).fetchall()
    }
    assert "pkg.util.helper" in qualified_names
    assert "pkg.util.call_helper" in qualified_names
    assert "util.helper" in qualified_names
    assert "call_helper" in qualified_names
    assert "websrc.util.helper" in qualified_names
    assert "websrc.app.callHelper" in qualified_names

    meta = dict(conn.execute("SELECT key, value FROM index_meta").fetchall())
    assert meta["source_language"] == "mixed"
    assert json.loads(meta["source_languages"]) == ["python", "rust", "typescript"]

    python_helper_id = _entity_id_by_qualified_name(conn, "pkg.util.helper")
    python_caller_id = _entity_id_by_qualified_name(conn, "pkg.util.call_helper")
    rust_helper_id = _entity_id_by_qualified_name(conn, "util.helper")
    rust_caller_id = _entity_id_by_qualified_name(conn, "call_helper")
    ts_helper_id = _entity_id_by_qualified_name(conn, "websrc.util.helper")
    ts_caller_id = _entity_id_by_qualified_name(conn, "websrc.app.callHelper")

    callers = {
        tuple(row)
        for row in conn.execute(
            "SELECT entity_id, caller_id FROM callers ORDER BY entity_id, caller_id"
        ).fetchall()
    }
    conn.close()

    assert (python_helper_id, python_caller_id) in callers
    assert (rust_helper_id, rust_caller_id) in callers
    assert (ts_helper_id, ts_caller_id) in callers
    assert (python_helper_id, rust_caller_id) not in callers
    assert (rust_helper_id, python_caller_id) not in callers
    assert (python_helper_id, ts_caller_id) not in callers
    assert (rust_helper_id, ts_caller_id) not in callers
    assert (ts_helper_id, python_caller_id) not in callers
    assert (ts_helper_id, rust_caller_id) not in callers

    stats = get_stats(tmp_path)
    assert stats["source_language"] == "mixed"
    assert stats["source_languages"] == ["python", "rust", "typescript"]
