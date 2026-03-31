"""Regression tests for TypeScript language support."""

import shutil
import sqlite3
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli
from cli import load_config
from index.indexer import index_repo
from index.search import search_entities
from index.store.db import connect
from index.store.fetch import get_entity_with_ir
from index.store.stats import get_stats


FIXTURE_REPO = ROOT / "tests" / "fixtures" / "ts_repo"


def _copy_fixture(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo_path = tmp_path / "ts_repo"
    shutil.copytree(FIXTURE_REPO, repo_path)
    return repo_path


def _entity_ids(repo_path: Path) -> dict[str, str]:
    conn = connect(repo_path / ".codeir" / "entities.db")
    conn.row_factory = sqlite3.Row
    try:
        return {
            row["qualified_name"]: row["id"]
            for row in conn.execute("SELECT id, qualified_name FROM entities ORDER BY qualified_name")
        }
    finally:
        conn.close()


def test_typescript_indexing_supports_new_entity_kinds_and_stable_ids(tmp_path):
    repo_path = _copy_fixture(tmp_path)
    repo_path_2 = _copy_fixture(tmp_path / "second")

    first = index_repo(repo_path, load_config(repo_path))
    second = index_repo(repo_path, load_config(repo_path))
    ids = _entity_ids(repo_path)
    index_repo(repo_path_2, load_config(repo_path_2))
    ids_2 = _entity_ids(repo_path_2)

    assert first["language"] == "typescript"
    assert first["files_scanned"] == 8
    assert first["entities_indexed"] >= 16
    assert second["status"] == "no_changes"
    assert ids == ids_2

    assert ids["User"] == "USER"
    assert ids["UserRecord"] == "USRRCRD"
    assert ids["UserStatus"] == "USRSTTS"
    assert ids["LegacyUser"] == "LGCYSR"
    assert ids["LegacyUser.normalize"] == "NRMLZ"
    assert ids["DEFAULT_PREFIX"] == "DFLTPRFX"
    assert ids["UserService.saveUser"] == "SVSR"
    assert ids["formatUser"] == "FRMTSR.02"

    user_ir = get_entity_with_ir(repo_path, ids["User"], "Behavior")
    assert user_ir is not None
    assert user_ir["kind"] == "interface"
    assert user_ir["ir_text"].startswith("IFC USER")

    search_results = search_entities("LegacyUser", repo_path, limit=10)
    assert {row["kind"] for row in search_results} >= {"namespace", "function"}

    stats = get_stats(repo_path)
    assert stats["language"] == "typescript"
    assert stats["file_coverage"]["source_files_indexed"] == 8
    assert any(item["kind"] == "namespace" for item in stats["entities_by_kind"])


def test_typescript_cli_workflow_outputs_useful_navigation_context(tmp_path, capsys):
    repo_path = _copy_fixture(tmp_path)
    index_repo(repo_path, load_config(repo_path))
    ids = _entity_ids(repo_path)

    cli.cmd_search(Namespace(query=["UserService"], repo_path=repo_path, limit=20, category=None, patterns=False))
    search_out = capsys.readouterr().out
    assert "UserService.saveUser" in search_out
    assert "src/services/userService.ts:6" in search_out

    cli.cmd_show(
        Namespace(
            entity_ids=[ids["UserService.saveUser"], ids["User"]],
            repo_path=repo_path,
            level="Behavior",
            full=True,
        )
    )
    show_out = capsys.readouterr().out
    assert "AMT SVSR" in show_out
    assert "IFC USER" in show_out

    cli.cmd_expand(Namespace(entity_ids=[ids["UserService.saveUser"]], repo_path=repo_path, number=False))
    expand_out = capsys.readouterr().out
    assert "async saveUser(user: User)" in expand_out
    assert "persistUser(user);" in expand_out

    cli.cmd_callers(Namespace(entity_id=ids["formatUser"], repo_path=repo_path, resolution=None, show_all=True))
    callers_out = capsys.readouterr().out
    assert "Callers of FRMTSR.02" in callers_out
    assert "UserService.formatUser" in callers_out or "FRMTSR" in callers_out

    cli.cmd_scope(Namespace(entity_id=ids["UserService.saveUser"], repo_path=repo_path, level="Behavior", show_all=True))
    scope_out = capsys.readouterr().out
    assert "Scope for: UserService.saveUser" in scope_out
    assert "callees (what this calls)" in scope_out
    assert "persistUser" in scope_out

    cli.cmd_impact(
        Namespace(
            entity_id=ids["formatUser"],
            repo_path=repo_path,
            depth=2,
            level="Behavior",
            exclude_area=None,
            show_all=True,
        )
    )
    impact_out = capsys.readouterr().out
    assert "Impact analysis for: formatUser" in impact_out
    assert "LegacyUser.normalize" in impact_out or "NRMLZ" in impact_out

    cli.cmd_stats(Namespace(repo_path=repo_path))
    stats_out = capsys.readouterr().out
    assert "Language: typescript" in stats_out
    assert "interface" in stats_out
    assert "namespace" in stats_out

    cli.cmd_module_map(Namespace(repo_path=repo_path))
    map_out = capsys.readouterr().out
    assert "src/routes/userRoutes.ts" in map_out
    assert "router" in map_out

    cli.cmd_bearings(Namespace(repo_path=repo_path, generate=True, full=False, category=None))
    capsys.readouterr()
    cli.cmd_bearings(Namespace(repo_path=repo_path, generate=False, full=False, category=None))
    bearings_out = capsys.readouterr().out
    assert "Categories" in bearings_out
    assert "router" in bearings_out
