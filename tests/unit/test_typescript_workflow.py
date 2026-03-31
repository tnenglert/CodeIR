"""Regression tests for TypeScript indexing and navigation workflows."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "typescript_repo"


def _copy_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "typescript_repo"
    shutil.copytree(FIXTURE, repo_path)
    return repo_path


def _run_cli(repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "cli.py", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _entity_id(repo_path: Path, qualified_name: str) -> str:
    conn = sqlite3.connect(repo_path / ".codeir" / "entities.db")
    try:
        row = conn.execute(
            "SELECT id FROM entities WHERE qualified_name = ?",
            (qualified_name,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"Missing entity for {qualified_name}"
    return str(row[0])


def _entity_ids(repo_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(repo_path / ".codeir" / "entities.db")
    try:
        rows = conn.execute(
            "SELECT qualified_name, id FROM entities ORDER BY qualified_name"
        ).fetchall()
    finally:
        conn.close()
    return {str(name): str(entity_id) for name, entity_id in rows}


def test_typescript_index_search_show_expand_and_bearings(tmp_path):
    repo_path = _copy_repo(tmp_path)

    indexed = _run_cli(repo_path, "index", str(repo_path))
    assert indexed.returncode == 0, indexed.stderr
    assert "Language: typescript" in indexed.stdout

    conn = sqlite3.connect(repo_path / ".codeir" / "entities.db")
    try:
        meta = dict(conn.execute("SELECT key, value FROM index_meta").fetchall())
    finally:
        conn.close()
    assert meta["source_language"] == "typescript"
    assert int(meta["source_files_indexed"]) == 8

    search = _run_cli(repo_path, "search", "UserService", "--repo-path", str(repo_path))
    assert search.returncode == 0, search.stderr
    assert "UserService" in search.stdout

    register_user_id = _entity_id(repo_path, "UserService.registerUser")

    show = _run_cli(repo_path, "show", register_user_id, "--repo-path", str(repo_path))
    assert show.returncode == 0, show.stderr
    assert "AMT" in show.stdout
    assert "formatHandle" in show.stdout
    assert "normalizeUser" in show.stdout

    expanded = _run_cli(repo_path, "expand", register_user_id, "--repo-path", str(repo_path))
    assert expanded.returncode == 0, expanded.stderr
    assert "async registerUser(profile: UserProfile): Promise<UserId>" in expanded.stdout
    assert "return persistUser" in expanded.stdout

    generated = _run_cli(repo_path, "bearings", "--generate", "--repo-path", str(repo_path))
    assert generated.returncode == 0, generated.stderr

    bearings = _run_cli(repo_path, "bearings", "--full", "--repo-path", str(repo_path))
    assert bearings.returncode == 0, bearings.stderr
    assert "router" in bearings.stdout
    assert "schema" in bearings.stdout
    assert "userRoutes.ts" in bearings.stdout
    assert "deps:src/services/userService,src/types/domain" in bearings.stdout


def test_typescript_callers_scope_impact_and_stable_ids(tmp_path):
    repo_path = _copy_repo(tmp_path)

    indexed = _run_cli(repo_path, "index", str(repo_path))
    assert indexed.returncode == 0, indexed.stderr

    first_ids = _entity_ids(repo_path)
    user_saved_id = _entity_id(repo_path, "AuditLabels.userSaved")
    write_audit_id = _entity_id(repo_path, "writeAudit")
    normalize_id = _entity_id(repo_path, "normalizeUser")
    register_user_id = _entity_id(repo_path, "UserService.registerUser")
    mount_routes_id = _entity_id(repo_path, "mountUserRoutes")
    format_summary_id = _entity_id(repo_path, "UserService.formatSummary")

    callers = _run_cli(repo_path, "callers", user_saved_id, "--repo-path", str(repo_path))
    assert callers.returncode == 0, callers.stderr
    assert write_audit_id in callers.stdout

    scope = _run_cli(repo_path, "scope", register_user_id, "--repo-path", str(repo_path))
    assert scope.returncode == 0, scope.stderr
    assert "callers" in scope.stdout.lower()
    assert "callees" in scope.stdout.lower()
    assert "siblings" in scope.stdout.lower()
    assert format_summary_id in scope.stdout

    impact = _run_cli(repo_path, "impact", normalize_id, "--depth", "2", "--repo-path", str(repo_path))
    assert impact.returncode == 0, impact.stderr
    assert register_user_id in impact.stdout
    assert mount_routes_id in impact.stdout

    reindexed = _run_cli(repo_path, "index", str(repo_path))
    assert reindexed.returncode == 0, reindexed.stderr

    second_ids = _entity_ids(repo_path)
    assert first_ids == second_ids
