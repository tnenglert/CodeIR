"""Tests for language frontends and source-file matching."""

from pathlib import Path

import pytest

from index.languages import (
    get_frontend,
    get_frontend_for_extensions,
    get_frontend_for_file,
    get_frontends_for_extensions,
    normalize_extensions,
    path_matches_extensions,
    resolve_frontend_config,
)
from index.locator import discover_source_files


def test_normalize_extensions_lowercases_and_deduplicates() -> None:
    assert normalize_extensions(["py", ".PY", ".d.ts"]) == (".py", ".d.ts")


def test_path_matches_extensions_supports_compound_suffixes() -> None:
    path = Path("types.d.ts")
    assert path_matches_extensions(path, [".d.ts"])
    assert not path_matches_extensions(path, [".py"])


def test_frontend_resolution_for_python_files() -> None:
    assert get_frontend("python").name == "python"
    assert get_frontend_for_file(Path("module.py")).name == "python"
    assert get_frontend_for_extensions([".py"]).name == "python"


def test_frontend_resolution_for_rust_files() -> None:
    assert get_frontend("rust").name == "rust"
    assert get_frontend_for_file(Path("lib.rs")).name == "rust"


def test_frontend_resolution_for_typescript_files() -> None:
    assert get_frontend("typescript").name == "typescript"
    assert get_frontend_for_file(Path("app.ts")).name == "typescript"
    assert get_frontend_for_file(Path("component.tsx")).name == "typescript"
    assert get_frontend_for_file(Path("types.d.ts")).name == "typescript"
    assert get_frontend_for_extensions([".ts"]).name == "typescript"


def test_mixed_extensions_resolve_to_multiple_frontends() -> None:
    frontends = get_frontends_for_extensions([".py", ".rs"])
    assert [frontend.name for frontend in frontends] == ["python", "rust"]
    with pytest.raises(ValueError):
        get_frontend_for_extensions([".py", ".rs"])


def test_resolve_frontend_config_auto_detects_mixed_repo(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.1.0'\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn helper() {}\n", encoding="utf-8")

    frontends, extensions = resolve_frontend_config(tmp_path, {"hidden_dirs": [".git", ".codeir"]})
    assert [frontend.name for frontend in frontends] == ["python", "rust"]
    assert extensions == [".py", ".rs"]


def test_resolve_frontend_config_detects_typescript_repo(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","version":"0.1.0"}\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const answer = 42;\n", encoding="utf-8")

    frontends, extensions = resolve_frontend_config(tmp_path, {"hidden_dirs": [".git", ".codeir"]})
    assert [frontend.name for frontend in frontends] == ["typescript"]
    assert extensions == [".ts"]


def test_discover_source_files_respects_hidden_dirs_and_compound_extensions(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "src" / "types.d.ts").write_text("export type T = string;\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "skip.py").write_text("print('skip')\n", encoding="utf-8")

    py_files = discover_source_files(tmp_path, [".py"], hidden_dirs=[".git"])
    dts_files = discover_source_files(tmp_path, [".d.ts"], hidden_dirs=[".git"])

    assert [path.name for path in py_files] == ["keep.py"]
    assert [path.name for path in dts_files] == ["types.d.ts"]


def test_unsupported_extension_raises() -> None:
    with pytest.raises(ValueError):
        get_frontend_for_file(Path("module.go"))
